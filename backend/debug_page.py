"""
Script de diagnostic : ouvre la page, attend le chargement, dump le HTML.
Usage: python debug_page.py --url "https://www.mwcbarcelona.com/exhibitors"
"""
import asyncio
import argparse
from playwright.async_api import async_playwright


async def dump(url: str, timeout_ms: int) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        print(f"Ouverture de {url} ...")
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Attends que la page soit stable (réseau calme)
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # Pause manuelle : ferme les bandeaux cookie si besoin
        print("Page chargée. Attente 5 secondes (ferme le bandeau cookie si présent)...")
        await asyncio.sleep(5)

        html = await page.content()

        out_file = "page_dump.html"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\nHTML sauvegardé dans : {out_file}  ({len(html)} caractères)")

        # Affiche aussi un résumé des sélecteurs potentiels
        print("\n--- Analyse rapide ---")
        for selector in [
            "ul.exhibitor-listing > a.exhibitor-item",
            "a.exhibitor-item",
            ".exhibitor-item",
            ".exhibitor-card",
            "[class*='exhibitor']",
            "[class*='company']",
            "[class*='vendor']",
        ]:
            count = await page.locator(selector).count()
            if count > 0:
                print(f"  TROUVE  {selector!r}  → {count} éléments")
            else:
                print(f"  absent  {selector!r}")

        await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.mwcbarcelona.com/exhibitors")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    args = parser.parse_args()
    asyncio.run(dump(args.url, args.timeout_ms))


if __name__ == "__main__":
    main()
