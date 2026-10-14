"""Scrapers package.

A scraper is a class implementing :class:`BaseScraper`. Register it in
``SCRAPER_REGISTRY`` so the scheduler can pick it up.
"""

from src.scrapers.barnes import BarnesScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.base import BaseScraper, ScrapeOutcome  # noqa: F401
from src.scrapers.bienici import BienIciScraper
from src.scrapers.breteuil import BreteuilScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.cabinetroche import CabinetRocheScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.century21 import Century21Scraper
from src.scrapers.district import DistrictScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.engelvoelkers import EngelVoelkersScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.espaces_atypiques import EspacesAtypiquesScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.figaro import FigaroScraper  # noqa: F401 (kept for re-enable)
from src.scrapers.foncia import FonciaScraper
from src.scrapers.glf import GlfScraper  # noqa: F401 (disabled)
from src.scrapers.gtf import GtfScraper  # noqa: F401 (disabled)
from src.scrapers.guyhoquet import GuyHoquetScraper  # noqa: F401
from src.scrapers.junot import JunotScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.laforet import LaforetScraper
from src.scrapers.leboncoin import LeBonCoinScraper
from src.scrapers.locservice import LocserviceScraper
from src.scrapers.logic_immo import LogicImmoScraper  # noqa: F401
from src.scrapers.nestenn import NestennScraper
from src.scrapers.orpi import OrpiScraper
from src.scrapers.pap import PapScraper  # noqa: F401 (disabled)
from src.scrapers.paris_gestion import ParisGestionScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.parisseine import ParisSeineScraper  # noqa: F401 (Paris-only, disabled)
from src.scrapers.seloger import SeLogerScraper  # noqa: F401 (disabled)
from src.scrapers.superimmo import SuperImmoScraper  # noqa: F401 (disabled)

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    # Aggregators / large platforms
    "leboncoin": LeBonCoinScraper,
    # seloger — disabled: 2026 redesign moved search to /classified-search with
    # AD08FR location ids (list.htm now 404s); ~35 Scrapfly credits per page.
    # pap — disabled: ~160 Scrapfly credits per page, results mostly colocations.
    "locservice": LocserviceScraper,
    "bienici": BienIciScraper,
    # superimmo — disabled: Cloudflare captcha blocks both curl_cffi and Playwright.
    # paris_gestion — disabled: Paris-only agency.
    # logic_immo — disabled: dropped all rental listings in 2026, only sales
    # remain. SeLoger covers the same stock (AVIV group).
    # figaro — disabled: Cloudflare + Bot Management — even Scrapfly ASP fails
    # (3/3 attempts). Stock heavily duplicated with seloger anyway.
    # Big agency networks
    "foncia": FonciaScraper,
    # glf — disabled: no rental stock in the 92/93 target towns (Paris/Boulogne only).
    # gtf — disabled: no rental stock in the 92/93 target towns.
    "orpi": OrpiScraper,
    "century21": Century21Scraper,
    "laforet": LaforetScraper,
    # guyhoquet — disabled: Vue SPA where Paris filter requires UI interaction;
    # /biens/result?…&localisation=Paris returns "popular ads" national mix.
    "nestenn": NestennScraper,
    # Niche Paris boutiques — disabled: Paris-intramuros-only stock, nothing in
    # the 92/93 target towns. Re-enable (and re-point) if the search moves back
    # to Paris: espaces_atypiques, junot, barnes, engelvoelkers, parisseine,
    # district, breteuil, cabinetroche.
}
