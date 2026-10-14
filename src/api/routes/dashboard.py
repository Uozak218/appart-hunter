"""Lightweight HTML dashboard — stats, last 50 listings, scraper health.

Password-gated via the same ``ADMIN_TOKEN``. Passed as a query param
(``?token=...``) so the browser can bookmark it, or as the ``X-Admin-Token``
header via curl/htmx.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.dependencies import get_db
from src.config import get_criteria, get_settings
from src.models.listing import Listing
from src.models.score import Score, ScoreStage
from src.models.scraper_health import ScraperHealth
from src.scoring.pipeline import ScoringPipeline
from src.storage.repositories.listing_repo import ListingRepository

router = APIRouter(tags=["dashboard"])


def _verify_token(
    request: Request,
    token: str | None = Query(default=None),
) -> None:
    admin = get_settings().admin_token
    provided = token or request.headers.get("X-Admin-Token")
    if not provided or provided != admin:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")


DASHBOARD_PAGE_SIZE = 50
DASHBOARD_MAX_PAGE = 20  # hard cap: refuses ?page=9999 → blocks Postgres


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    token: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=DASHBOARD_MAX_PAGE),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    _verify_token(request, token)

    listing_repo = ListingRepository(session)
    pipeline = ScoringPipeline(session, get_criteria())
    statuses = await listing_repo.count_by_status()
    spent, cap, pct = await pipeline.budget.budget_state()

    # Paginated listings: LIMIT 50 OFFSET (page-1)*50. Photos are eager-loaded
    # only for the rows we'll actually render, not the whole table.
    offset = (page - 1) * DASHBOARD_PAGE_SIZE
    recent = await session.execute(
        select(Listing, Score)
        .join(
            Score,
            (Score.listing_id == Listing.id) & (Score.stage == ScoreStage.sonnet_pepite),
        )
        .options(selectinload(Listing.photos))
        .order_by(desc(Score.created_at))
        .limit(DASHBOARD_PAGE_SIZE)
        .offset(offset)
    )

    health_result = await session.execute(select(ScraperHealth).order_by(ScraperHealth.source))
    health_rows = list(health_result.scalars().all())

    # Last-hour cost
    recent_cost = await pipeline.recent_spend(hours=1)
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_cost = await pipeline.score_repo.cost_since(today_start)

    html = _render(
        statuses=statuses,
        spent=spent,
        cap=cap,
        pct=pct,
        listings=list(recent.all()),
        health=health_rows,
        hourly_cost=recent_cost,
        today_cost=today_cost,
        page=page,
        token=token or "",
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Rendering — single template, no deps.
# ---------------------------------------------------------------------------


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    delta = datetime.now(UTC) - dt.astimezone(UTC)
    if delta < timedelta(minutes=1):
        return f"{int(delta.total_seconds())}s"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() / 60)}m"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() / 3600)}h"
    return f"{delta.days}d"


def _score_badge(score: int | None, urgent_threshold: int, alert_threshold: int) -> str:
    if score is None:
        return '<span class="badge grey">—</span>'
    cls = "red" if score >= urgent_threshold else ("green" if score >= alert_threshold else "grey")
    return f'<span class="badge {cls}">{score}</span>'


def _health_row(row: ScraperHealth) -> str:
    ok = row.consecutive_failures == 0
    cls = "green" if ok else ("amber" if row.consecutive_failures < 3 else "red")
    return (
        f"<tr>"
        f"<td><b>{escape(row.source)}</b></td>"
        f'<td><span class="badge {cls}">{row.consecutive_failures} fail</span></td>'
        f"<td>{_fmt_dt(row.last_success_at)} ago</td>"
        f"<td>{row.last_listings_count}</td>"
        f"<td>{row.total_runs}</td>"
        f'<td class="muted small">{escape((row.last_error or "")[:180])}</td>'
        f"</tr>"
    )


def _listing_row(listing: Listing, score_row: Score, urgent: int, alert: int) -> str:
    arr = f"75{listing.arrondissement:02d}" if listing.arrondissement else ""
    thumb = listing.photos[0].url if listing.photos else ""
    thumb_html = f'<img src="{escape(thumb)}" loading="lazy" />' if thumb else ""
    price = f"{int(listing.rent_cc)} €" if listing.rent_cc else "—"
    surface = f"{int(listing.surface_m2)} m²" if listing.surface_m2 else "—"
    highlights = ", ".join(score_row.highlights or [])[:120]
    return (
        "<tr>"
        f"<td>{thumb_html}</td>"
        f"<td>{_score_badge(score_row.score, urgent, alert)}</td>"
        f"<td>{escape(listing.title or '')[:80]}</td>"
        f"<td>{arr}</td>"
        f"<td>{price}</td>"
        f"<td>{surface}</td>"
        f"<td class='small muted'>{escape(highlights)}</td>"
        f"<td>{_fmt_dt(score_row.created_at)}</td>"
        f'<td><a href="{escape(listing.url)}" target="_blank">↗</a></td>'
        "</tr>"
    )


def _pager_html(page: int, token: str, has_more: bool) -> str:
    tok = f"&token={escape(token)}" if token else ""
    prev_link = (
        f'<a href="?page={page - 1}{tok}">‹ précédent</a>'
        if page > 1
        else '<span class="muted">‹ précédent</span>'
    )
    next_link = (
        f'<a href="?page={page + 1}{tok}">suivant ›</a>'
        if has_more
        else '<span class="muted">suivant ›</span>'
    )
    return f'<div class="pager">{prev_link}  ·  <b>page {page}</b>  ·  {next_link}</div>'


def _render(
    *,
    statuses: dict,
    spent: float,
    cap: float,
    pct: float,
    listings: list[tuple[Listing, Score]],
    health: list[ScraperHealth],
    hourly_cost: float,
    today_cost: float,
    page: int = 1,
    token: str = "",
) -> str:
    c = get_criteria().scoring
    listings_html = "\n".join(
        _listing_row(listing, s, c.urgent_threshold, c.alert_threshold) for listing, s in listings
    )
    health_html = "\n".join(_health_row(h) for h in health)
    status_html = " · ".join(f"{escape(k)}: <b>{v}</b>" for k, v in sorted(statuses.items()))
    bar_pct = min(100, pct)
    bar_cls = "red" if pct >= 80 else ("amber" if pct >= 50 else "green")
    has_more = len(listings) == DASHBOARD_PAGE_SIZE
    pager = _pager_html(page, token, has_more)

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="refresh" content="60" />
<title>Paris Flat Hunter — Dashboard</title>
<style>
:root {{ color-scheme: dark; }}
body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0f1115; color: #e5e7eb; margin: 0; padding: 1.5rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 1rem; }}
h2 {{ font-size: 1.05rem; margin: 2rem 0 .5rem; color: #9ca3af; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .75rem; }}
.card {{ background: #1a1d24; border: 1px solid #262a33; border-radius: 8px; padding: 1rem; }}
.card .val {{ font-size: 1.6rem; font-weight: 600; }}
.card .lbl {{ color: #9ca3af; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; }}
table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
th, td {{ text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #262a33; vertical-align: middle; }}
th {{ color: #9ca3af; font-weight: 500; font-size: .75rem; text-transform: uppercase; letter-spacing: .05em; }}
img {{ width: 60px; height: 45px; object-fit: cover; border-radius: 4px; }}
.badge {{ display: inline-block; padding: .1rem .5rem; border-radius: 999px; font-size: .8rem; font-weight: 600; }}
.green {{ background: #16432a; color: #86efac; }}
.amber {{ background: #4a3710; color: #fcd34d; }}
.red {{ background: #4a1a1a; color: #fca5a5; }}
.grey {{ background: #262a33; color: #9ca3af; }}
.bar {{ height: 6px; background: #262a33; border-radius: 3px; overflow: hidden; margin-top: .4rem; }}
.bar > div {{ height: 100%; transition: width .3s; }}
.bar .green {{ background: #22c55e; }}
.bar .amber {{ background: #f59e0b; }}
.bar .red {{ background: #ef4444; }}
.muted {{ color: #9ca3af; }}
.small {{ font-size: .8rem; }}
a {{ color: #60a5fa; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.row-stats {{ font-size: .85rem; color: #9ca3af; }}
</style></head>
<body>
<h1>🏠 Paris Flat Hunter — Dashboard <span class="muted small">(auto-refresh 60s)</span></h1>

<div class="grid">
  <div class="card">
    <div class="lbl">Budget mensuel</div>
    <div class="val">{spent:.2f} <span class="small muted">/ {cap:.0f} €</span></div>
    <div class="bar"><div class="{bar_cls}" style="width:{bar_pct:.1f}%"></div></div>
  </div>
  <div class="card">
    <div class="lbl">Coût dernière heure</div>
    <div class="val">{hourly_cost:.3f} €</div>
  </div>
  <div class="card">
    <div class="lbl">Coût aujourd'hui</div>
    <div class="val">{today_cost:.2f} €</div>
  </div>
  <div class="card">
    <div class="lbl">Annonces</div>
    <div class="val">{sum(statuses.values())}</div>
    <div class="row-stats">{status_html}</div>
  </div>
</div>

<h2>Scrapers</h2>
<table>
  <thead><tr><th>Source</th><th>Status</th><th>Dernier succès</th><th>Annonces</th><th>Runs</th><th>Dernière erreur</th></tr></thead>
  <tbody>{health_html or '<tr><td colspan=6 class="muted">Aucun run pour le moment</td></tr>'}</tbody>
</table>

<h2>Dernières annonces scorées</h2>
<table>
  <thead><tr><th></th><th>Score</th><th>Titre</th><th>CP</th><th>Loyer</th><th>Surface</th><th>Atouts</th><th>Quand</th><th>Lien</th></tr></thead>
  <tbody>{listings_html or '<tr><td colspan=9 class="muted">Rien encore</td></tr>'}</tbody>
</table>
{pager}
</body></html>
"""
