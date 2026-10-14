"""Application configuration.

Secrets/infra come from environment variables (Pydantic Settings).
Search criteria come from a YAML file so they can be tuned without a redeploy.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Env-backed settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: str = "dev"
    log_level: str = "INFO"
    tz: str = "Europe/Paris"

    # Postgres
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "pfh"
    postgres_user: str = "pfh"
    postgres_password: str = "pfh"
    database_url: str | None = None

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_haiku_model: str = "claude-haiku-4-5"
    anthropic_sonnet_model: str = "claude-sonnet-4-6"
    anthropic_monthly_budget_eur: float = 30.0
    usd_eur_rate: float = 0.92

    # Multi-tenant (Phase 1) — gated by ``multitenant_enabled``. When false, the
    # legacy single-user scoring path (using ``anthropic_api_key`` above +
    # ``search_criteria.yaml``) is the only one that runs. When true, every
    # active user in the ``users`` table also gets scored with their BYOK.
    multitenant_enabled: bool = False
    # 32-byte Fernet key (urlsafe base64). Used to encrypt per-user Anthropic
    # keys at rest. Generate once with ``python -c "from cryptography.fernet
    # import Fernet; print(Fernet.generate_key().decode())"``.
    encryption_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    # One or more chat IDs, comma-separated (e.g. "12345,67890" to ping both
    # you and your partner). Each recipient must have /start'ed the bot once.
    telegram_chat_id: str = ""
    telegram_webhook_url: str = ""

    @property
    def telegram_chat_ids(self) -> list[str]:
        return [cid.strip() for cid in self.telegram_chat_id.split(",") if cid.strip()]

    # Discord
    discord_webhook_url: str = ""

    # ntfy.sh (optional third channel — free, no account)
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    # Scoring thresholds (override YAML if present)
    score_alert_threshold: int = 70
    score_urgent_threshold: int = 85

    # Scrapers
    playwright_headless: bool = True
    scrape_interval_leboncoin: int = 90
    scrape_interval_seloger: int = 90
    scrape_interval_pap: int = 300
    scrape_interval_locservice: int = 180  # P2P → poll plus souvent, faible coût (HTTP-only)
    scrape_interval_paris_gestion: int = 600  # WP boutique, ~14 stock, rotation lente
    scrape_interval_bienici: int = 300
    scrape_interval_logic_immo: int = 300
    scrape_interval_superimmo: int = 300
    scrape_interval_foncia: int = 300
    scrape_interval_glf: int = 600
    scrape_interval_gtf: int = 900
    # New agency/aggregator sources — long defaults until selectors are validated.
    scrape_interval_figaro: int = 600
    scrape_interval_orpi: int = 600
    scrape_interval_century21: int = 600
    scrape_interval_laforet: int = 600
    scrape_interval_guyhoquet: int = 600
    scrape_interval_nestenn: int = 600
    # Niche Paris boutiques — low listing volume, 15-min polling is plenty.
    scrape_interval_espaces_atypiques: int = 900
    scrape_interval_junot: int = 900
    scrape_interval_barnes: int = 900
    scrape_interval_engelvoelkers: int = 900
    scrape_interval_parisseine: int = 900
    scrape_interval_district: int = 900
    scrape_interval_breteuil: int = 900
    scrape_interval_cabinetroche: int = 900
    scraper_fail_threshold: int = 3

    # Proxies
    use_proxies: bool = False
    proxy_provider: str = ""
    proxy_url: str = ""
    proxy_rotation_seconds: int = 600

    # Scrapfly — DataDome/Cloudflare bypass for the 4 blocked sources.
    # Leave empty to disable; the scrapers that opt-in will hard-fail at startup
    # with a clear error if the key is missing.
    scrapfly_api_key: str = ""
    scrapfly_country: str = "fr"

    # Criteria file
    search_criteria_path: str = "config/search_criteria.yaml"

    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    admin_token: str = "change-me-admin-token"

    # Contact-message flow
    user_first_name: str = "Léo"
    auto_send_email: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = ""
    smtp_from_email: str = ""

    # Auto-application (DossierFacile). Rollout = enabled(dry-run) -> live.
    auto_apply_enabled: bool = False  # feature on/off (drafts + Telegram receipts)
    auto_apply_live: bool = False  # True = really SMTP-send; False = dry-run only
    auto_apply_min_score: int = 75  # only auto-apply at/above this score
    auto_apply_daily_cap: int = 20  # max real sends per rolling 24h (safety)
    user_dossier_url: str = ""  # DossierFacile link injected into each application

    # Active scraping window — only scrape when listings drop & the user can act.
    # Default: Mon-Sat 08:00-19:00 (settings.tz). Cuts ~60% of the Scrapfly burn.
    scrape_window_enabled: bool = True
    scrape_window_start_hour: int = 8
    scrape_window_end_hour: int = 19  # exclusive (last active hour is 18:xx)
    scrape_window_last_weekday: int = 5  # Mon=0 .. Sat=5 → Sunday (6) excluded

    # Scrapfly credit guard: pause DataDome sources + Telegram-alert when the
    # billing-period burn runs ahead of a sustainable pace.
    scrapfly_guard_enabled: bool = True
    scrapfly_guard_ratio: float = 1.5  # trip when used > 1.5x the on-pace amount

    # Skip the detail fetch (and its Scrapfly cost) for listings already
    # rejectable from listing-page data — older than this many days, when the
    # source exposes a publication date (leboncoin). 0 = no recency cutoff.
    max_listing_age_days: int = 7

    @property
    def sqlalchemy_url(self) -> str:
        """Return an async SQLAlchemy URL (asyncpg driver)."""
        if self.database_url:
            # Normalise driver hint
            url = self.database_url
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgresql://") and "+asyncpg" not in url:
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def alembic_url(self) -> str:
        """Sync URL used by Alembic (psycopg driver not required in v1: use asyncpg via async env)."""
        return self.sqlalchemy_url


# ---------------------------------------------------------------------------
# YAML criteria
# ---------------------------------------------------------------------------


class FloorRules(BaseModel):
    no_ground_floor: bool = True
    elevator_required_above: int = 4


class PropertyTypes(BaseModel):
    preferred: list[str] = Field(default_factory=lambda: ["T3"])
    acceptable: list[str] = Field(default_factory=lambda: ["T2"])


class PropertyCriteria(BaseModel):
    types: PropertyTypes = Field(default_factory=PropertyTypes)
    min_surface: dict[str, float] = Field(default_factory=lambda: {"T3": 45.0, "T2": 40.0})
    max_rent_cc: float = 2000.0
    # ``None`` = accept both furnished + unfurnished (badge in alert).
    # ``False`` = strict unfurnished only.
    # ``True`` = strict furnished only.
    furnished: bool | None = None
    floor: FloorRules = Field(default_factory=FloorRules)


class TouristicBBox(BaseModel):
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


class ZoneRule(BaseModel):
    code: str
    areas: list[str] = Field(default_factory=list)
    exclude_touristic: bool = False
    touristic_bbox: TouristicBBox | None = None
    # When True, the listing must be within walking distance of a well-served
    # metro station to be accepted (used for the 20e). Requires lat/lon.
    require_well_connected_metro: bool = False


class Line14Proximity(BaseModel):
    """Accept any Paris intramuros listing within N minutes walk of a line-14 station.

    Overrides the default ``zones.include`` list for those listings — line 14 is
    fast enough that being next to any of its stops is worth a visit regardless
    of arrondissement.
    """

    enabled: bool = False
    max_walk_minutes: float = 5.0


class ZoneCriteria(BaseModel):
    include: list[ZoneRule] = Field(default_factory=list)
    line14_proximity: Line14Proximity = Field(default_factory=Line14Proximity)


class GemCriteria(BaseModel):
    must_have_signals: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)


class ScoringCriteria(BaseModel):
    alert_threshold: int = 70
    urgent_threshold: int = 85
    price_drop_alert: bool = True
    min_price_drop_pct: float = 3.0


# Median Paris rent EUR/m² (charges comprises) as of 2026. Used by the scam
# detector to flag listings priced <60% of the local floor as almost certain
# arnaques. Override per-arrondissement via YAML ``market_prices``.
DEFAULT_EUR_PER_M2_FLOOR: dict[int, float] = {
    1: 42.0,
    2: 40.0,
    3: 38.0,
    4: 40.0,
    5: 36.0,
    6: 42.0,
    7: 42.0,
    8: 42.0,
    9: 33.0,
    10: 30.0,
    11: 28.0,
    12: 28.0,
    13: 26.0,
    14: 28.0,
    15: 29.0,
    16: 32.0,
    17: 30.0,
    18: 26.0,
    19: 24.0,
    20: 25.0,
}


class QuartierOverride(BaseModel):
    """Per-quartier override of the arrondissement baseline.

    Matches a listing when ``arrondissement`` equals the given value AND any
    of ``keywords`` appears in the listing title/description/address (case
    and accent insensitive). Example: 75011 "Oberkampf" floor is ~31 €/m²
    while 75011 "Nation" is closer to the arr baseline (28).
    """

    arrondissement: int
    name: str
    eur_per_m2: float
    keywords: list[str] = Field(default_factory=list)


class MarketPrices(BaseModel):
    # Fallback used when an arrondissement isn't specified.
    default_eur_per_m2: float = 28.0
    # Explicit per-arrondissement floors (key = int arrondissement).
    eur_per_m2: dict[int, float] = Field(default_factory=lambda: dict(DEFAULT_EUR_PER_M2_FLOOR))
    # Optional quartier refinements (checked before per-arr floor).
    quartiers: list[QuartierOverride] = Field(default_factory=list)

    def floor_for(
        self,
        arrondissement: int | None,
        *,
        text: str | None = None,
    ) -> float:
        if arrondissement is not None and text and self.quartiers:
            # Lazy import to keep config dependency graph flat
            from src.utils.hashing import normalise_text

            norm = normalise_text(text)
            for q in self.quartiers:
                if q.arrondissement != arrondissement:
                    continue
                for kw in q.keywords:
                    kw_norm = normalise_text(kw)
                    if kw_norm and kw_norm in norm:
                        return q.eur_per_m2
        if arrondissement is None:
            return self.default_eur_per_m2
        return self.eur_per_m2.get(arrondissement, self.default_eur_per_m2)


class SearchCriteria(BaseModel):
    name: str = "default"
    property: PropertyCriteria = Field(default_factory=PropertyCriteria)
    zones: ZoneCriteria = Field(default_factory=ZoneCriteria)
    excludes: list[str] = Field(default_factory=list)
    gem_criteria: GemCriteria = Field(default_factory=GemCriteria)
    red_flags: list[str] = Field(default_factory=list)
    scoring: ScoringCriteria = Field(default_factory=ScoringCriteria)
    market_prices: MarketPrices = Field(default_factory=MarketPrices)

    def allowed_zone_codes(self) -> set[str]:
        return {z.code for z in self.zones.include}


def load_criteria(path: str | Path) -> SearchCriteria:
    """Load the YAML criteria file.

    Backward compatible:
      - Old format: a flat document → parsed as the single SearchCriteria.
      - New format: ``{profiles: [{name, ...}, ...]}`` → first profile wins
        (full multi-profile fan-out can be added later by iterating
        ``load_profiles``).
    """
    profiles = load_profiles(path)
    return profiles[0]


def load_profiles(path: str | Path) -> list[SearchCriteria]:
    p = Path(path)
    if not p.exists():
        return [SearchCriteria()]
    with p.open("r", encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    if isinstance(data.get("profiles"), list) and data["profiles"]:
        return [SearchCriteria.model_validate(p) for p in data["profiles"]]
    return [SearchCriteria.model_validate(data)]


# ---------------------------------------------------------------------------
# Cached singletons
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_criteria() -> SearchCriteria:
    return load_criteria(get_settings().search_criteria_path)
