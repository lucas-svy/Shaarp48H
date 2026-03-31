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

# Force UTF-8 output on Windows (avoids CP1252 UnicodeEncodeError with non-latin chars)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib.parse import urljoin

StatusCallback = Optional[Callable[[str], None]]


def _status(cb: StatusCallback, msg: str) -> None:
    if cb:
        cb(msg)

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


def load_spec_from_dict(data: Dict[str, Any]) -> ScrapeSpec:
    """Build a ScrapeSpec directly from a dict (no file needed)."""
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


async def analyze_page(url: str, headless: bool = True, timeout_ms: int = 30_000, on_status: StatusCallback = None) -> str:
    """Load a page with Playwright and return structured analysis for spec generation.

    Returns a string containing:
    - candidate CSS selectors (repeated elements likely to be cards)
    - an HTML snippet from the content area (skips header/nav)
    """
    async with async_playwright() as p:
        _status(on_status, "Lancement du navigateur...")
        browser: Browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
            timezone_id="Europe/Paris",
        )
        page: Page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US'] });
            window.chrome = { runtime: {} };
        """)
        try:
            _status(on_status, f"Chargement de la page : {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                _status(on_status, "Attente du rendu JavaScript...")
                await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass

            _status(on_status, "Détection des sélecteurs CSS répétés...")
            # Detect repeated VISIBLE elements — these are likely the exhibitor cards
            candidates: list = await page.evaluate("""() => {
                const hiddenClasses = new Set([
                    'opacity-0','invisible','hidden','sr-only',
                    'translate-y-full','translate-x-full','-translate-y-full',
                    'scale-0','pointer-events-none'
                ]);
                const counts = {};
                document.querySelectorAll('*').forEach(el => {
                    const classes = [...el.classList].filter(Boolean);
                    if (!classes.length) return;
                    // Skip elements with hidden/animation Tailwind classes
                    if (classes.some(c => hiddenClasses.has(c))) return;
                    // Skip elements that are not visible
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    const key = el.tagName.toLowerCase() + '.' + classes.join('.');
                    counts[key] = (counts[key] || 0) + 1;
                });
                return Object.entries(counts)
                    .filter(([, n]) => n >= 5)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 30)
                    .map(([sel, n]) => sel + ' (x' + n + ')');
            }""")

            # For the top repeated element candidates, check if they contain <a href> links
            link_info: list = await page.evaluate("""() => {
                const hidden = new Set([
                    'opacity-0','invisible','hidden','sr-only',
                    'translate-y-full','-translate-y-full','scale-0'
                ]);
                const counts = {};
                const samples = {};
                document.querySelectorAll('*').forEach(el => {
                    const cls = [...el.classList].filter(Boolean);
                    if (!cls.length) return;
                    if (cls.some(c => hidden.has(c))) return;
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    const key = el.tagName.toLowerCase() + '.' + cls.join('.');
                    counts[key] = (counts[key] || 0) + 1;
                    if (!samples[key]) samples[key] = el;
                });
                return Object.entries(counts)
                    .filter(([, n]) => n >= 5)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 20)
                    .map(([sel, n]) => {
                        const el = samples[sel];
                        const link = el ? el.querySelector('a[href]') : null;
                        const linkHref = link ? link.getAttribute('href') : null;
                        const linkClass = link ? [...link.classList].join('.') : null;
                        const linkSel = link
                            ? (linkClass ? 'a.' + linkClass : 'a')
                            : null;
                        return sel + ' (x' + n + ')' + (linkSel ? ' → link: ' + linkSel + ' href=' + linkHref : ' → no <a> found');
                    });
            }""")

            _status(on_status, "Extraction du contenu HTML...")
            # Get a content-area HTML snippet (skip first 3 000 chars = likely header/nav)
            full_html = await page.inner_html("body")
            snippet = full_html[3_000:3_000 + 20_000]

            candidates_txt = "\n".join(link_info) if link_info else "(none found)"
            return (
                f"URL: {url}\n\n"
                f"Repeated elements with link info (candidate selectors for exhibitor cards):\n{candidates_txt}\n\n"
                f"HTML snippet (characters 3000–23000 of body):\n{snippet}"
            )
        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# Hybrid scraper helpers
# ---------------------------------------------------------------------------

# Domaines de tracking/analytics à ignorer lors de l'interception API
_TRACKING_DOMAINS = {
    "onetrust", "cookielaw", "cookiebot", "trustarc",
    "google-analytics", "googletagmanager", "googlesyndication",
    "doubleclick", "facebook.net", "fbcdn",
    "hotjar", "segment.io", "amplitude", "mixpanel",
    "intercom", "hubspot", "salesforce", "marketo", "pardot",
    "optimizely", "fullstory", "logrocket", "newrelic",
    "cloudflare", "akamai", "sentry.io",
}


def _is_tracking_url(url: str) -> bool:
    """Return True if the URL looks like a tracking/analytics endpoint."""
    low = url.lower()
    return any(domain in low for domain in _TRACKING_DOMAINS)


def _looks_like_company_name(name: str) -> bool:
    """Return True if the string looks like a real company name, not a cookie/token."""
    if not name or len(name) < 3:
        return False
    # Cookie / token patterns: starts with _, contains =, only lowercase+digits
    if name.startswith("_") or "=" in name or name.startswith("__"):
        return False
    # Pure alphanumeric-with-dashes tokens (e.g. "tfpvi", "_evga_81e3")
    import re as _re
    if _re.match(r'^[a-z0-9_-]{1,30}$', name):
        return False
    # Must contain at least one letter
    if not any(c.isalpha() for c in name):
        return False
    return True


def _unwrap_graphql_edges(data: Any) -> Any:
    """Unwrap GraphQL-style {edges: [{node: {...}}, ...]} into a plain list of dicts."""
    if isinstance(data, dict):
        edges = data.get("edges")
        if isinstance(edges, list) and edges and isinstance(edges[0], dict) and "node" in edges[0]:
            return [e["node"] for e in edges if isinstance(e, dict) and "node" in e]
    return data


def _find_exhibitor_list(data: Any) -> Optional[list]:
    """Recursively find the best list-of-dicts that looks like exhibitor records.
    Handles plain arrays, nested dicts, and GraphQL edges/node patterns."""
    exhibitor_keys = {
        "name", "title", "company", "exhibitor", "companyname",
        "nom", "label", "exhibitorname", "raison_sociale", "organizationname",
        "displayname", "brandname", "societyname",
    }

    best: Optional[list] = None
    best_score = 0

    def _search(obj: Any, depth: int = 0) -> None:
        nonlocal best, best_score
        if depth > 6:
            return

        # Unwrap GraphQL edges pattern before inspecting
        obj = _unwrap_graphql_edges(obj)

        if isinstance(obj, list):
            # Require at least 5 items to avoid small config arrays
            if len(obj) >= 5 and isinstance(obj[0], dict):
                first = obj[0]
                keys = {k.lower().replace("-", "_").replace(" ", "_") for k in first.keys()}
                score = len(exhibitor_keys & keys) * len(obj)
                if score > best_score:
                    best_score = score
                    best = obj
                for item in obj[:3]:
                    _search(item, depth + 1)
        elif isinstance(obj, dict):
            for v in obj.values():
                _search(v, depth + 1)

    _search(data)
    # Require at least 1 matching exhibitor key to avoid false positives
    return best if best_score > 0 else None


def _map_exhibitor_fields(
    item: Dict[str, Any], base_url: str, spec_base_url: Optional[str]
) -> Exhibitor:
    """Best-effort mapping from arbitrary JSON object to Exhibitor."""
    name: Optional[str] = None
    booth: Optional[str] = None
    profile_url: Optional[str] = None

    for k in ["name", "companyName", "company", "title", "nom", "label",
              "exhibitorName", "raison_sociale", "organizationName"]:
        if k in item and isinstance(item[k], str) and item[k].strip():
            name = item[k].strip()
            break
    if not name:
        for k, v in item.items():
            if any(x in k.lower() for x in ["name", "company", "nom", "title", "label"]):
                if isinstance(v, str) and v.strip():
                    name = v.strip()
                    break

    for k in ["url", "website", "profileUrl", "profile_url", "link", "href",
              "pageUrl", "exhibitorUrl", "slug"]:
        if k in item and isinstance(item[k], str) and item[k].strip():
            profile_url = _normalize_url(item[k], base_url, spec_base_url)
            break
    if not profile_url:
        for k, v in item.items():
            if any(x in k.lower() for x in ["url", "link", "href", "website", "slug"]):
                if isinstance(v, str) and v.strip():
                    profile_url = _normalize_url(v, base_url, spec_base_url)
                    break

    for k in ["booth", "stand", "boothNumber", "booth_number", "hall", "location", "standNumber"]:
        if k in item and item[k] is not None:
            booth = str(item[k])
            break

    raw = {k: v for k, v in item.items()
           if k not in {"name", "url", "booth", "companyName", "profileUrl"}}
    return Exhibitor(name=name, booth=booth, profile_url=profile_url, raw=raw)


def _scrape_static_sync(
    url: str, spec: ScrapeSpec, *, limit: int, offset: int
) -> Optional[List[Exhibitor]]:
    """Synchronous HTTP + BeautifulSoup (no browser). SSR sites only."""
    try:
        import requests as _req
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }
        resp = _req.get(url, headers=headers, timeout=8, allow_redirects=True)
        if resp.status_code >= 400:
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select(spec.cards_selector)
        if len(cards) < 3:
            return None

        exhibitors: List[Exhibitor] = []
        for card in cards[offset : offset + limit]:
            name: Optional[str] = None
            booth: Optional[str] = None
            profile_url: Optional[str] = None
            categories: List[str] = []

            if "name" in spec.fields:
                el = card.select_one(spec.fields["name"])
                if el:
                    name = el.get_text(strip=True) or None

            if "booth" in spec.fields:
                el = card.select_one(spec.fields["booth"])
                if el:
                    booth = el.get_text(strip=True) or None

            if "categories" in spec.fields:
                el = card.select_one(spec.fields["categories"])
                if el:
                    cats_text = el.get_text(strip=True)
                    if "," in cats_text:
                        categories = [c.strip() for c in cats_text.split(",") if c.strip()]
                    else:
                        categories = [c.strip() for c in cats_text.split("\n") if c.strip()]

            if "profile_url" in spec.fields:
                sel = spec.fields["profile_url"]
                if sel == ":scope":
                    href = card.get("href")
                else:
                    el = card.select_one(sel)
                    href = el.get("href") if el else None
                profile_url = _normalize_url(href, url, spec.base_url)

            exhibitors.append(
                Exhibitor(name=name, booth=booth, categories=categories, profile_url=profile_url)
            )

        named = sum(1 for e in exhibitors if e.name)
        return exhibitors if named >= 3 else None
    except Exception:
        return None


async def _scrape_static(
    url: str, spec: ScrapeSpec, *, limit: int, offset: int, on_status: StatusCallback = None
) -> Optional[List[Exhibitor]]:
    """Async wrapper around the sync HTTP scraper."""
    import asyncio

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _scrape_static_sync, url, spec, limit, offset)



async def _scrape_with_single_browser(
    url: str,
    spec: ScrapeSpec,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    user_agent: Optional[str] = None,
    on_status: StatusCallback = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Exhibitor]:
    """Single-browser hybrid: intercepts API calls AND falls back to DOM extraction
    in the SAME browser session — no redundant browser launches."""
    captured: List[Dict[str, Any]] = []
    spec_base = spec.base_url if spec else None

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
            timezone_id="Europe/Paris",
            java_script_enabled=True,
        )
        page: Page = await context.new_page()

        # Hide headless/bot signals that sites like VivaTech check
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US'] });
            window.chrome = { runtime: {} };
        """)

        # ── API interception (runs in background during navigation) ──────────
        async def on_response(response: Any) -> None:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if response.status < 200 or response.status >= 300:
                return
            if _is_tracking_url(response.url):
                return
            try:
                text = await response.text()
                if len(text) < 200 or len(text) > 5_000_000:
                    return
                captured.append({"url": response.url, "text": text})
            except Exception:
                pass

        page.on("response", on_response)

        # Block images & media — keep CSS/JS so the page renders correctly
        async def block_media(route: Any) -> None:
            if route.request.resource_type in {"image", "media", "font"}:
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_media)

        try:
            _status(on_status, f"Chargement de la page : {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            wait_for = spec.wait_for_selector or spec.cards_selector
            mode = spec.pagination.mode

            # ── Wait strategy depends on pagination mode ─────────────────────
            if mode == "infinite_scroll":
                # SPAs need time for JS to finish loading + API calls to complete
                _status(on_status, "Attente du chargement JS (infinite scroll)...")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    await page.wait_for_timeout(2_000)
            else:
                # next_button / none: skip networkidle, go straight to selector
                # networkidle can take 10-12s and adds no value for paginated sites
                pass

            # ── 1. Check intercepted API responses ───────────────────────────
            _status(on_status, f"Analyse des réponses API interceptées ({len(captured)} appels JSON)...")
            for cap in captured:
                try:
                    data = json.loads(cap["text"])
                except Exception:
                    continue
                lst = _find_exhibitor_list(data)
                if not lst:
                    continue
                slice_ = lst[offset : offset + limit]
                exhibitors = [
                    _map_exhibitor_fields(item, url, spec_base)
                    for item in slice_
                    if isinstance(item, dict)
                ]
                real_names = sum(1 for e in exhibitors if _looks_like_company_name(e.name or ""))
                if real_names >= 5:
                    _status(on_status, f"✓ API interceptée — {real_names} exposants")
                    return _dedupe(exhibitors, spec.dedupe_by)

            # ── 2. Fall back to DOM extraction on the already-loaded page ────
            _status(on_status, "→ Pas d'API détectée — extraction DOM sur la page chargée...")

            if mode == "infinite_scroll":
                # Cards get visible CSS classes only after scroll triggers animations
                _status(on_status, "Scroll initial pour déclencher le chargement des exposants...")
                for i in range(6):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1_200)
                    try:
                        cards_now = await page.query_selector_all(spec.cards_selector)
                        if len(cards_now) >= 5:
                            _status(on_status, f"{len(cards_now)} cards chargées après {i+1} scroll(s)")
                            break
                    except Exception:
                        pass

            # Wait for the cards selector to appear
            try:
                await page.wait_for_selector(wait_for, timeout=20_000)
            except Exception:
                if mode == "infinite_scroll":
                    # Last resort for SPAs: wait for networkidle then retry
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                        await page.wait_for_selector(wait_for, timeout=10_000)
                    except Exception:
                        if wait_for != spec.cards_selector:
                            try:
                                await page.wait_for_selector(spec.cards_selector, timeout=8_000)
                            except Exception:
                                _status(on_status, "⚠ Sélecteur introuvable sur la page.")
                                return []
                        else:
                            _status(on_status, "⚠ Sélecteur introuvable sur la page.")
                            return []
                else:
                    _status(on_status, "⚠ Sélecteur introuvable sur la page.")
                    return []

            results: List[Exhibitor] = []

            if spec.pagination.mode == "next_button":
                cumulative = 0
                for page_idx in range(max(1, spec.pagination.max_pages)):
                    page_cards = await _extract_exhibitors_from_page(
                        page, spec, on_status=on_status, offset=cumulative
                    )
                    page_count = len(page_cards)
                    if cumulative + page_count <= offset:
                        cumulative += page_count
                        _status(on_status, f"Page {page_idx + 1} ignorée (avant offset {offset})...")
                    else:
                        page_start = max(0, offset - cumulative)
                        needed = limit - len(results)
                        results.extend(page_cards[page_start:][:needed])
                        cumulative += page_count
                        _status(on_status, f"{len(results)}/{limit} exposants récupérés...")
                    if len(results) >= limit:
                        break
                    next_href = await _get_next_page_href(page, spec)
                    if not next_href:
                        break
                    await page.goto(next_href, wait_until="domcontentloaded", timeout=timeout_ms)
                    # No networkidle for next_button — just wait for the cards selector
                    try:
                        await page.wait_for_selector(wait_for, timeout=15_000)
                    except Exception:
                        break

            elif spec.pagination.mode == "infinite_scroll":
                _status(on_status, f"Scroll jusqu'à l'exposant {offset + limit}...")
                await _paginate_infinite_scroll(
                    page, spec, on_status=on_status, target=offset + limit
                )
                all_cards = await _extract_exhibitors_from_page(page, spec, on_status=on_status)
                results = all_cards[offset : offset + limit]

            else:
                all_cards = await _extract_exhibitors_from_page(page, spec, on_status=on_status)
                results = all_cards[offset : offset + limit]

            _status(on_status, f"Déduplication de {len(results)} entrées...")
            results = _dedupe(results, spec.dedupe_by)
            _status(on_status, f"✓ {len(results)} exposants récupérés (offset={offset}).")
            return results

        finally:
            await context.close()
            await browser.close()


async def scrape_hybrid(
    url: str,
    spec: ScrapeSpec,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    user_agent: Optional[str] = None,
    on_status: StatusCallback = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Exhibitor]:
    """Hybrid scraper — 2 étapes, 1 seul navigateur.

    1. HTTP statique (sans navigateur) — ~1 s
    2. Navigateur unique : interception API d'abord, puis extraction DOM
       sur la même page déjà chargée — ~5–15 s selon le site
    """
    # 1 — Static HTTP (no browser at all)
    _status(on_status, "⚡ [1/2] Tentative HTTP statique...")
    try:
        result = await _scrape_static(url, spec, limit=limit, offset=offset, on_status=on_status)
        if result:
            _status(on_status, f"✓ HTTP statique réussi — {len(result)} exposants")
            return _dedupe(result, spec.dedupe_by)
    except Exception:
        pass
    _status(on_status, "→ Site dynamique, lancement du navigateur...")

    # 2 — Single browser: API interception + DOM extraction
    _status(on_status, "⚡ [2/2] Navigateur unique (API + DOM)...")
    return await _scrape_with_single_browser(
        url, spec,
        headless=headless, timeout_ms=timeout_ms,
        user_agent=user_agent, on_status=on_status,
        limit=limit, offset=offset,
    )


async def scrape_exhibitors(
    url: str,
    spec: ScrapeSpec,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    user_agent: Optional[str] = None,
    on_status: StatusCallback = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Exhibitor]:
    """Scrape exhibitor data from a salon website.

    Args:
        limit: Max number of exhibitors to return.
        offset: Skip the first N exhibitors (for pagination).

    Returns a list of Exhibitor objects.
    """

    async with async_playwright() as p:
        _status(on_status, "Lancement du navigateur...")
        browser: Browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=user_agent or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="fr-FR",
            timezone_id="Europe/Paris",
        )
        page: Page = await context.new_page()
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US'] });
            window.chrome = { runtime: {} };
        """)

        try:
            _status(on_status, f"Chargement de la page : {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            try:
                _status(on_status, "Attente du rendu JavaScript...")
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            wait_for = spec.wait_for_selector or spec.cards_selector
            _status(on_status, f"Recherche des exposants (offset={offset}, limit={limit})...")

            results: List[Exhibitor] = []

            if spec.pagination.mode == "next_button":
                # Navigate pages, skipping those before offset, stopping once limit reached.
                cumulative = 0
                for page_index in range(max(1, spec.pagination.max_pages)):
                    await page.wait_for_selector(wait_for, timeout=timeout_ms)
                    page_cards = await _extract_exhibitors_from_page(page, spec, on_status=on_status, offset=cumulative)
                    page_count = len(page_cards)

                    if cumulative + page_count <= offset:
                        # Entire page is before our offset — skip it
                        cumulative += page_count
                        _status(on_status, f"Page {page_index + 1} ignorée (avant offset {offset})...")
                    else:
                        # Take the relevant slice from this page
                        page_start = max(0, offset - cumulative)
                        useful = page_cards[page_start:]
                        needed = limit - len(results)
                        results.extend(useful[:needed])
                        cumulative += page_count
                        _status(on_status, f"{len(results)}/{limit} exposants récupérés...")

                    if len(results) >= limit:
                        break

                    next_href = await _get_next_page_href(page, spec)
                    if not next_href:
                        break

                    await page.goto(next_href, wait_until="domcontentloaded", timeout=timeout_ms)
                    # No networkidle for next_button — just wait for the cards selector

            else:
                await page.wait_for_selector(wait_for, timeout=timeout_ms)
                if spec.pagination.mode == "infinite_scroll":
                    _status(on_status, f"Scroll jusqu'à l'exposant {offset + limit}...")
                    await _paginate_infinite_scroll(page, spec, on_status=on_status, target=offset + limit)
                all_cards = await _extract_exhibitors_from_page(page, spec, on_status=on_status)
                results = all_cards[offset:offset + limit]

            _status(on_status, f"Déduplication de {len(results)} entrées...")
            results = _dedupe(results, spec.dedupe_by)
            _status(on_status, f"✓ {len(results)} exposants récupérés (offset={offset}).")
            return results
        finally:
            await context.close()
            await browser.close()


async def _extract_exhibitors_from_page(page: Page, spec: ScrapeSpec, on_status: StatusCallback = None, offset: int = 0) -> List[Exhibitor]:
    cards = await page.query_selector_all(spec.cards_selector)
    out: List[Exhibitor] = []

    for i, card in enumerate(cards):
        # Emit status every 10 cards
        if i % 10 == 0:
            _status(on_status, f"Extraction des exposants... {offset + i}/{offset + len(cards)}")

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


async def _paginate_infinite_scroll(page: Page, spec: ScrapeSpec, on_status: StatusCallback = None, target: int = 0) -> None:
    """Scroll until `target` cards are visible (or the page stops loading)."""
    last_count = 0
    stable_rounds = 0

    for _ in range(max(1, spec.pagination.max_scroll_rounds)):
        cards = await page.query_selector_all(spec.cards_selector)
        count = len(cards)

        if count == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            _status(on_status, f"Chargement... {count} exposants visibles")

        # Stop early if we have enough cards for the requested slice
        if target > 0 and count >= target:
            _status(on_status, f"Objectif atteint : {count} exposants chargés.")
            return

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
