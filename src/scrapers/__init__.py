"""Scrapers package.

A scraper is a class implementing :class:`BaseScraper`. Register it in
``SCRAPER_REGISTRY`` so the scheduler can pick it up.
"""

from src.scrapers.barnes import BarnesScraper
from src.scrapers.base import BaseScraper, ScrapeOutcome  # noqa: F401
from src.scrapers.bienici import BienIciScraper
from src.scrapers.breteuil import BreteuilScraper
from src.scrapers.cabinetroche import CabinetRocheScraper
from src.scrapers.century21 import Century21Scraper
from src.scrapers.district import DistrictScraper
from src.scrapers.engelvoelkers import EngelVoelkersScraper
from src.scrapers.espaces_atypiques import EspacesAtypiquesScraper
from src.scrapers.figaro import FigaroScraper  # noqa: F401 (kept for re-enable)
from src.scrapers.foncia import FonciaScraper
from src.scrapers.glf import GlfScraper
from src.scrapers.gtf import GtfScraper
from src.scrapers.guyhoquet import GuyHoquetScraper  # noqa: F401
from src.scrapers.junot import JunotScraper
from src.scrapers.laforet import LaforetScraper
from src.scrapers.leboncoin import LeBonCoinScraper
from src.scrapers.locservice import LocserviceScraper
from src.scrapers.logic_immo import LogicImmoScraper  # noqa: F401
from src.scrapers.nestenn import NestennScraper
from src.scrapers.orpi import OrpiScraper
from src.scrapers.pap import PapScraper
from src.scrapers.paris_gestion import ParisGestionScraper
from src.scrapers.parisseine import ParisSeineScraper
from src.scrapers.seloger import SeLogerScraper
from src.scrapers.superimmo import SuperImmoScraper

SCRAPER_REGISTRY: dict[str, type[BaseScraper]] = {
    # Aggregators / large platforms
    "leboncoin": LeBonCoinScraper,
    "seloger": SeLogerScraper,
    "pap": PapScraper,
    "locservice": LocserviceScraper,
    "bienici": BienIciScraper,
    "superimmo": SuperImmoScraper,
    "paris_gestion": ParisGestionScraper,
    # logic_immo — disabled: dropped all rental listings in 2026, only sales
    # remain. SeLoger covers the same stock (AVIV group).
    # figaro — disabled: Cloudflare + Bot Management — even Scrapfly ASP fails
    # (3/3 attempts). Stock heavily duplicated with seloger anyway.
    # Big agency networks
    "foncia": FonciaScraper,
    "glf": GlfScraper,
    "gtf": GtfScraper,
    "orpi": OrpiScraper,
    "century21": Century21Scraper,
    "laforet": LaforetScraper,
    # guyhoquet — disabled: Vue SPA where Paris filter requires UI interaction;
    # /biens/result?…&localisation=Paris returns "popular ads" national mix.
    "nestenn": NestennScraper,
    # Niche Paris boutiques (low concurrence — strongest "pépite" gisement)
    "espaces_atypiques": EspacesAtypiquesScraper,
    "junot": JunotScraper,
    "barnes": BarnesScraper,
    "engelvoelkers": EngelVoelkersScraper,
    "parisseine": ParisSeineScraper,
    "district": DistrictScraper,
    "breteuil": BreteuilScraper,
    "cabinetroche": CabinetRocheScraper,
}
