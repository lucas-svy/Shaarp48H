"""Debug script for VivaTechnology exhibitors page.

Runs headful so you can see the browser and dismiss any cookie banner.
Captures ALL JSON responses and prints candidate CSS selectors.

Usage:
    python debug_vivatech.py
"""

import asyncio
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

URL = "https://www.vivatechnology.com/exhibitors"


async def main() -> None:
    all_json: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Capture ALL JSON responses — no URL filtering
        async def handle_response(response):
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if response.status < 200 or response.status >= 300:
                return
            try:
                text = await response.text()
                if len(text) < 100:
                    return
                all_json.append({"url": response.url, "size": len(text), "text": text})
                print(f"  [JSON] {len(text):>8} chars  {response.url}")
            except Exception:
                pass

        page.on("response", handle_response)

        print(f"Opening {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        print("Page loaded. Waiting 6s — dismiss cookie banner if needed...")
        await page.wait_for_timeout(6_000)

        # Scroll to trigger lazy loading + API calls
        print("Scrolling to trigger exhibitor loading...")
        for i in range(6):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1_500)
            count = await page.evaluate("document.querySelectorAll('*').length")
            print(f"  scroll {i+1}/6 — {count} DOM elements")

        await page.wait_for_timeout(2_000)

        # ── Candidate CSS selectors ──────────────────────────────────────────
        print("\n--- Candidate selectors (visible, repeated ≥5x) ---")
        candidates = await page.evaluate("""() => {
            const hidden = new Set([
                'opacity-0','invisible','hidden','sr-only',
                'translate-y-full','-translate-y-full','scale-0','pointer-events-none'
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
                .slice(0, 40)
                .map(([sel, n]) => {
                    const el = samples[sel];
                    const link = el ? el.querySelector('a[href]') : null;
                    const h = el ? (el.querySelector('h1,h2,h3,h4') || {}).textContent : null;
                    return {
                        sel: sel,
                        count: n,
                        hasLink: !!link,
                        linkHref: link ? link.getAttribute('href') : null,
                        heading: h ? h.trim().slice(0, 60) : null
                    };
                });
        }""")

        for c in candidates:
            link_info = f"  → {c['linkHref']}" if c['hasLink'] else ""
            head_info = f"  h={c['heading']}" if c['heading'] else ""
            print(f"  {c['sel']} (x{c['count']}){link_info}{head_info}")

        # ── Analyse JSON responses ───────────────────────────────────────────
        print(f"\n--- JSON responses captured: {len(all_json)} total ---")

        # Sort by size descending — large responses are more likely to be data
        all_json.sort(key=lambda x: x["size"], reverse=True)

        for r in all_json[:20]:
            print(f"\n{'='*70}")
            print(f"URL: {r['url']}")
            print(f"Size: {r['size']} chars")
            try:
                data = json.loads(r["text"])
                # Try to find lists of objects
                def find_lists(obj, depth=0, path="root"):
                    if depth > 4:
                        return
                    if isinstance(obj, list) and len(obj) >= 3:
                        if isinstance(obj[0], dict):
                            keys = list(obj[0].keys())[:8]
                            print(f"  LIST[{len(obj)}] at {path} → keys: {keys}")
                            if len(obj) > 0:
                                print(f"    first item: {json.dumps(obj[0], ensure_ascii=False)[:300]}")
                    elif isinstance(obj, dict):
                        for k, v in list(obj.items())[:10]:
                            find_lists(v, depth+1, f"{path}.{k}")
                find_lists(data)
            except Exception as e:
                print(f"  (parse error: {e}) raw: {r['text'][:200]}")

        print("\n--- Done ---")
        print("Tip: look for LIST entries above with exhibitor-like keys (name, company, title, etc.)")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
