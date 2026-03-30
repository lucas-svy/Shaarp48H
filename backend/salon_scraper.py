"""Playwright-based scraper for professional trade fair (salon) exhibitor listings.

Goals:
- Provide a generic, configurable scraper that can be adapted per salon website.
- Return structured exhibitor data as JSON (easy to integrate later with Next.js).

Usage examples:
  python salon_scraper.py scrape --url "https://example.com/exhibitors" --spec specs/example.json
  python salon_scraper.py scrape --url "..." --spec specs/example.json --headful

The scraper is intentionally spec-driven. A spec defines:
- how to find exhibitor cards/rows
- selectors for fields (name, booth, categories, profile_url, etc.)
- pagination strategy (next button or infinite scroll)

Keep this module importable: other scripts (e.g. openai_chat.py) can call `scrape_exhibitors()`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, async_playwright


@dataclass
class Exhibitor:
    name: Optional[str] = None
    booth: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    profile_url: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PaginationSpec:
    mode: str = "none"  # none | next_button | infinite_scroll
    next_selector: Optional[str] = None
    max_pages: int = 1
    scroll_pause_ms: int = 800
    max_scroll_rounds: int = 30


@dataclass
class ScrapeSpec:
    cards_selector: str
    fields: Dict[str, str]
    pagination: PaginationSpec = field(default_factory=PaginationSpec)
    wait_for_selector: Optional[str] = None
    base_url: Optional[str] = None
    dedupe_by: str = "profile_url"  # profile_url | name


class ScraperError(RuntimeError):
    pass


def _read_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_spec(path: str) -> ScrapeSpec:
    data = _read_json_file(path)

    if "cards_selector" not in data or "fields" not in data:
        raise ScraperError("Spec must include 'cards_selector' and 'fields'.")

    pagination_data = data.get("pagination", {}) or {}
    pagination = PaginationSpec(
        mode=pagination_data.get("mode", "none"),
        next_selector=pagination_data.get("next_selector"),
        max_pages=int(pagination_data.get("max_pages", 1)),
        scroll_pause_ms=int(pagination_data.get("scroll_pause_ms", 800)),
        max_scroll_rounds=int(pagination_data.get("max_scroll_rounds", 30)),
    )

    return ScrapeSpec(
        cards_selector=data["cards_selector"],
        fields=dict(data["fields"]),
        pagination=pagination,
        wait_for_selector=data.get("wait_for_selector"),
        base_url=data.get("base_url"),
        dedupe_by=data.get("dedupe_by", "profile_url"),
    )


async def _extract_text(card, selector: str) -> Optional[str]:
    try:
        el = await card.query_selector(selector)
        if not el:
            return None
        txt = (await el.inner_text()) or ""
        txt = " ".join(txt.split())
        return txt or None
    except Exception:
        return None


async def _extract_attr(card, selector: str, attr: str) -> Optional[str]:
    try:
        el = await card.query_selector(selector)
        if not el:
            return None
        val = await el.get_attribute(attr)
        if not val:
            return None
        return val.strip() or None
    except Exception:
        return None


def _normalize_url(href: Optional[str], page_url: str, base_url: Optional[str]) -> Optional[str]:
    if not href:
        return None
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if base_url:
        return urljoin(base_url, href)
    return urljoin(page_url, href)


def _dedupe(exhibitors: Iterable[Exhibitor], key: str) -> List[Exhibitor]:
    seen: set[str] = set()
    out: List[Exhibitor] = []
    for ex in exhibitors:
        if key == "name":
            dedupe_key = (ex.name or "").strip().lower()
        else:
            dedupe_key = (ex.profile_url or "").strip().lower()

        if not dedupe_key:
            out.append(ex)
            continue

        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append(ex)
    return out


async def scrape_exhibitors(
    url: str,
    spec: ScrapeSpec,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    user_agent: Optional[str] = None,
) -> List[Exhibitor]:
    """Scrape exhibitor data from a salon website.

    Returns a list of Exhibitor objects.
    """

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=user_agent)
        page: Page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            wait_for = spec.wait_for_selector or spec.cards_selector

            results: List[Exhibitor] = []

            if spec.pagination.mode == "next_button":
                # Page-by-page accumulation.
                for page_index in range(max(1, spec.pagination.max_pages)):
                    await page.wait_for_selector(wait_for, timeout=timeout_ms)
                    results.extend(await _extract_exhibitors_from_page(page, spec))

                    next_href = await _get_next_page_href(page, spec)
                    if not next_href:
                        break

                    # Navigate to next page explicitly (more reliable than click).
                    await page.goto(next_href, wait_until="domcontentloaded", timeout=timeout_ms)

            else:
                # Single page scrape, potentially after loading more results.
                await page.wait_for_selector(wait_for, timeout=timeout_ms)
                if spec.pagination.mode == "infinite_scroll":
                    await _paginate_infinite_scroll(page, spec)
                results = await _extract_exhibitors_from_page(page, spec)

            results = _dedupe(results, spec.dedupe_by)
            return results
        finally:
            await context.close()
            await browser.close()


async def _extract_exhibitors_from_page(page: Page, spec: ScrapeSpec) -> List[Exhibitor]:
    cards = await page.query_selector_all(spec.cards_selector)
    out: List[Exhibitor] = []

    for card in cards:
        name_sel = spec.fields.get("name")
        booth_sel = spec.fields.get("booth")
        categories_sel = spec.fields.get("categories")
        link_sel = spec.fields.get("profile_url")

        name = await _extract_text(card, name_sel) if name_sel else None
        booth = await _extract_text(card, booth_sel) if booth_sel else None

        categories: List[str] = []
        if categories_sel:
            cats_text = await _extract_text(card, categories_sel)
            if cats_text:
                # Support either comma-separated or bullet-like text.
                if "," in cats_text:
                    categories = [c.strip() for c in cats_text.split(",") if c.strip()]
                else:
                    categories = [c.strip() for c in cats_text.split("\n") if c.strip()]

        profile_url: Optional[str] = None
        if link_sel:
            href = await _extract_attr(card, link_sel, "href")
            profile_url = _normalize_url(href, page.url, spec.base_url)

        raw: Dict[str, Any] = {}
        for k, selector in spec.fields.items():
            if k in {"name", "booth", "categories", "profile_url"}:
                continue
            raw[k] = await _extract_text(card, selector)

        out.append(
            Exhibitor(
                name=name,
                booth=booth,
                categories=categories,
                profile_url=profile_url,
                raw=raw,
            )
        )

    return out


async def _get_next_page_href(page: Page, spec: ScrapeSpec) -> Optional[str]:
    if not spec.pagination.next_selector:
        raise ScraperError("Pagination mode 'next_button' requires pagination.next_selector")

    el = await page.query_selector(spec.pagination.next_selector)
    if not el:
        return None

    href = await el.get_attribute("href")
    return _normalize_url(href, page.url, spec.base_url)


async def _paginate_next_button(page: Page, spec: ScrapeSpec, *, timeout_ms: int) -> None:
    # Deprecated: kept for backward compatibility but no longer used by scrape_exhibitors.
    # Use page-by-page accumulation in scrape_exhibitors instead.
    _ = (page, spec, timeout_ms)
    return None


async def _paginate_infinite_scroll(page: Page, spec: ScrapeSpec) -> None:
    last_count = 0
    stable_rounds = 0

    for _ in range(max(1, spec.pagination.max_scroll_rounds)):
        cards = await page.query_selector_all(spec.cards_selector)
        count = len(cards)

        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= 3:
            return

        last_count = count

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(spec.pagination.scroll_pause_ms)


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="salon_scraper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scrape = sub.add_parser("scrape", help="Scrape exhibitors and print JSON")
    scrape.add_argument("--url", required=True)
    scrape.add_argument("--spec", required=True, help="Path to a JSON spec file")
    scrape.add_argument("--headful", action="store_true", help="Run with visible browser")
    scrape.add_argument("--timeout-ms", type=int, default=30_000)

    args = parser.parse_args(argv)

    if args.cmd == "scrape":
        spec = load_spec(args.spec)
        exhibitors = asyncio.run(
            scrape_exhibitors(
                args.url,
                spec,
                headless=not args.headful,
                timeout_ms=args.timeout_ms,
            )
        )
        print(_json_dumps([asdict(e) for e in exhibitors]))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(cli_main())
