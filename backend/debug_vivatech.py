"""Debug script for VivaTechnology exhibitors page.

Runs headful so you can see the browser and dismiss any cookie banner.
Captures:
  - Network requests (XHR/fetch) that look like exhibitor data
  - Repeated visible elements (candidate CSS selectors)
  - An HTML dump of the page after full load

Usage:
    python debug_vivatech.py
"""

import asyncio
import json
import re

from playwright.async_api import async_playwright

URL = "https://www.vivatechnology.com/exhibitors"
OUTPUT_HTML = "vivatech_dump.html"


async def main() -> None:
    api_responses: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        # Intercept responses that look like JSON exhibitor lists
        async def handle_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "json" in ct and any(k in url for k in ["exhibitor", "company", "vendor", "participant", "expose"]):
                try:
                    body = await response.json()
                    api_responses.append({"url": url, "data": body})
                    print(f"  [API] {url}")
                except Exception:
                    pass

        page.on("response", handle_response)

        print(f"Opening {URL} ...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        print("Page loaded. Waiting 8 seconds — dismiss cookie banner if needed...")
        await page.wait_for_timeout(8_000)

        # Scroll to trigger lazy loading
        print("Scrolling to load exhibitors...")
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1_500)

        # Candidate selectors (visible elements repeated 5+ times)
        print("\n--- Candidate selectors (visible, repeated) ---")
        candidates = await page.evaluate("""() => {
            const hidden = new Set([
                'opacity-0','invisible','hidden','sr-only',
                'translate-y-full','-translate-y-full','scale-0'
            ]);
            const counts = {};
            document.querySelectorAll('*').forEach(el => {
                const cls = [...el.classList].filter(Boolean);
                if (!cls.length) return;
                if (cls.some(c => hidden.has(c))) return;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                const key = el.tagName.toLowerCase() + '.' + cls.join('.');
                counts[key] = (counts[key] || 0) + 1;
            });
            return Object.entries(counts)
                .filter(([, n]) => n >= 5)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 40)
                .map(([sel, n]) => sel + ' (x' + n + ')');
        }""")

        for c in candidates:
            print(" ", c)

        # Save HTML
        html = await page.content()
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\nHTML saved to {OUTPUT_HTML} ({len(html)} chars)")

        # API responses summary
        if api_responses:
            print(f"\n--- API responses captured ({len(api_responses)}) ---")
            for r in api_responses:
                print(f"  {r['url']}")
                print(f"    → {str(r['data'])[:200]}")
        else:
            print("\n--- No JSON API responses captured for exhibitors ---")
            print("  Try opening Network tab in DevTools and filter by Fetch/XHR while scrolling.")

        await browser.close()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
