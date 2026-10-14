"""Verify the configured proxy actually works.

Usage (inside the scraper container):
    docker compose exec scraper python -m scripts.test_proxy

Checks, in order:
  1. USE_PROXIES + PROXY_URL are set.
  2. Direct httpx call through the proxy returns a non-datacenter IP.
  3. Playwright through the proxy loads leboncoin without a DataDome 403.
"""

from __future__ import annotations

import asyncio

import httpx
from playwright.async_api import async_playwright

from src.config import get_settings
from src.scrapers.proxies import build_proxy_option, get_httpx_proxy


async def check_httpx() -> None:
    proxy = get_httpx_proxy()
    if not proxy:
        print("  SKIP: no proxy configured")
        return
    async with httpx.AsyncClient(proxy=proxy, timeout=60.0) as c:
        r = await c.get("https://api.ipify.org?format=json")
        print(f"  exit IP (via proxy): {r.json()}")
        r = await c.get("https://ipinfo.io/json")
        j = r.json()
        print(
            f"  ipinfo: org={j.get('org')} country={j.get('country')} region={j.get('region')}"
        )


async def check_playwright_leboncoin() -> None:
    opt = build_proxy_option()
    if not opt:
        print("  SKIP: no proxy configured")
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=opt,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(locale="fr-FR")
        page = await ctx.new_page()
        resp = await page.goto(
            "https://www.leboncoin.fr/recherche?category=10&locations=d_75",
            timeout=30_000,
            wait_until="domcontentloaded",
        )
        status = resp.status if resp else "?"
        html = await page.content()
        blocked = "DataDome" in html or status == 403
        print(
            f"  leboncoin: HTTP {status}, {'BLOCKED (DataDome)' if blocked else 'OK'} "
            f"({len(html)} bytes)"
        )
        await browser.close()


async def main() -> None:
    s = get_settings()
    print(f"USE_PROXIES={s.use_proxies}  PROXY_PROVIDER={s.proxy_provider or '-'}")
    print(f"PROXY_URL={'set' if s.proxy_url else '<empty>'}")
    if not s.use_proxies or not s.proxy_url:
        print("\n❌ Proxy not configured. Set USE_PROXIES=true and PROXY_URL=... in .env")
        return

    print("\n[1/2] httpx through the proxy:")
    await check_httpx()

    print("\n[2/2] Playwright → leboncoin through the proxy:")
    await check_playwright_leboncoin()


if __name__ == "__main__":
    asyncio.run(main())
