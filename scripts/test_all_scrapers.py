"""Smoke-test every registered scraper: fetch listing page, print entry counts.

Usage (inside the scraper container):
    docker compose exec scraper python -m scripts.test_all_scrapers
"""

from __future__ import annotations

import asyncio
import traceback

from src.config import get_criteria
from src.scrapers import SCRAPER_REGISTRY


async def test_source(name: str, cls) -> None:
    criteria = get_criteria()
    scraper = cls(session=None, criteria=criteria)
    backend = "scrapfly" if cls.use_scrapfly else "playwright"
    print(f"\n=== {name} ({backend}) ===")
    try:
        # Use the scraper's own _browser() so Scrapfly-backed scrapers actually
        # go through Scrapfly. Falls back to StealthBrowser otherwise.
        async with scraper._browser() as ctx:
            page = await ctx.new_page()
            try:
                entries = await scraper.fetch_listing_page(page)
            finally:
                await page.close()
            print(f"[{name}] {len(entries)} entries")
            for e in entries[:3]:
                print(f"  - {e.get('external_id')}: {e.get('url')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] FAILED: {exc}")
        traceback.print_exc()


async def main() -> None:
    for name, cls in SCRAPER_REGISTRY.items():
        await test_source(name, cls)


if __name__ == "__main__":
    asyncio.run(main())
