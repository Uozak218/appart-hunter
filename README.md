# Paris Flat Hunter

Personal bot that scrapes Paris rental listing sites in real time, scores each
new ad with Claude (text + vision), and alerts on Telegram + Discord for the
ones matching your criteria. Strictly personal, single-user.

## What it does

- Polls **LeBonCoin**, **SeLoger** (aggressive: ~90s), **PAP**, **Bien'ici**,
  **Logic-Immo** (normal: ~5 min) 24/7.
- Normalises each listing into a common shape, dedups cross-site (fuzzy match
  on title + surface + price + arrondissement), and tracks price changes over
  time.
- Scores every candidate with a **two-stage Claude pipeline**:
  1. **Claude Haiku 4.5** — cheap text filter enforcing the hard criteria
     (T2/T3, rent ≤ 2000€ CC, target zones, no furnished, etc.).
  2. **Claude Sonnet 4.6** with vision — gem scoring (0-100) on the photos
     *only* if stage 1 passes.
- System prompts are **cached** (Anthropic ephemeral cache), cutting Sonnet
  input cost by ~90% on repeat calls.
- Hard monthly budget cap (default 30 €/month). Scoring stops when reached;
  scraping continues so you don't miss anything once it resets.
- Alerts:
  - **Score ≥ 70** → Telegram + Discord alert.
  - **Score ≥ 85** → `URGENT 🔥` alert with notification sound.
  - **Price drop ≥ 3%** → `📉 Baisse de prix` alert.

## Architecture

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Scrapers   │──▶│  Postgres   │◀──│   FastAPI   │
│ (Playwright)│   │  (+ PostGIS)│   │ (webhooks)  │
└─────────────┘   └─────────────┘   └─────────────┘
       │                 ▲                 ▲
       ▼                 │                 │
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│  Scheduler  │──▶│   Scoring   │──▶│  Notifier   │
│ (APScheduler)│   │(Claude API)│   │ (TG+Discord)│
└─────────────┘   └─────────────┘   └─────────────┘
```

Pipeline per listing:

1. Scheduler triggers a scraper on its cadence (with jitter).
2. Scraper pulls the listing page, extracts external IDs.
3. For every new / changed ID it fetches the detail page + photos.
4. `RawListing` is upserted. Cross-site dedup + price-history updated.
5. Scoring pipeline: Haiku filter → Sonnet scorer → alert if ≥ threshold.
6. Notifier drains its queue and pushes to Telegram + Discord in parallel.

## Repository layout

```
paris-flat-hunter/
├── src/
│   ├── main.py                  # entrypoint: api / scheduler / all
│   ├── config.py                # Pydantic Settings + YAML criteria loader
│   ├── api/                     # FastAPI (health, admin, telegram webhook)
│   ├── scheduler/jobs.py        # APScheduler job wiring
│   ├── scrapers/                # base, stealth, proxies, one module per site
│   ├── scoring/                 # Haiku filter, Sonnet scorer, prompts, budget
│   ├── notifier/                # Telegram + Discord + templates + queue
│   ├── storage/                 # async engine, repos, dedup
│   ├── models/                  # SQLAlchemy models
│   ├── schemas/                 # Pydantic schemas shared across layers
│   └── utils/                   # logging, hashing, geo helpers
├── migrations/                  # Alembic
├── config/search_criteria.yaml  # edit-anytime criteria
├── tests/                       # unit tests (mocked Claude)
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── scripts/{deploy.sh,init_db.sh}
```

## Quick start — local dev

### 1. Clone + configure

```bash
git clone <repo> paris-flat-hunter && cd paris-flat-hunter
cp .env.example .env
chmod 600 .env
# Required: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
#           POSTGRES_PASSWORD, ADMIN_TOKEN
# Optional (leave empty to skip): DISCORD_WEBHOOK_URL, NTFY_TOPIC
```

See [Secrets & security](#secrets--security) below for where the values go.

### 2. Install

```bash
make install-dev
make playwright      # downloads Chromium + OS deps
```

### 3. Run Postgres + migrations

```bash
# Start only postgres locally (quickest):
docker compose up -d postgres
alembic upgrade head
```

### 4. Launch

```bash
python -m src.main all      # API + scheduler in one process
# or split:
python -m src.main api
python -m src.main scheduler
```

Sanity-check the API at `http://localhost:8000/health`.

### 5. Run the tests

```bash
make test
```

Tests mock every external call (Anthropic, Telegram, Discord, Playwright) so
they run fully offline.

## Docker Compose

```bash
docker compose up -d --build
docker compose logs -f
```

### Dev mode (recommended when working locally)

`docker-compose.override.yml` is auto-picked up by Compose. It:

- Builds the `scraper` / `api` / `migrate` images with `INSTALL_DEV=1` so
  **pytest, ruff, mypy** are inside the container.
- Mounts `src/`, `tests/`, `config/`, `migrations/` as volumes so code
  changes on the host are reflected in the container **without rebuilding**.

Usage:
```bash
docker compose up -d --build                                # build with dev deps + start
docker compose exec scraper pytest -q                       # run the tests
docker compose exec scraper ruff check src tests            # lint
docker compose exec scraper python -m src.main selftest     # smoke test
docker compose exec scraper python scripts/rescore.py <id>  # replay scoring
docker compose exec scraper bash                            # open a shell
```

After editing Python code, restart the process to pick it up:
```bash
docker compose restart scraper
```

To run a prod-identical image (skip the override):
```bash
docker compose -f docker-compose.yml up -d --build
```

Services:

- `postgres` — PostgreSQL 16 + PostGIS
- `migrate` — runs `alembic upgrade head` once, then exits
- `api` — FastAPI on `${API_PORT:-8000}`
- `scraper` — APScheduler + scoring + notifier

## VPS setup (Hetzner CX22, Ubuntu 24.04)

```bash
# On a fresh box:
apt-get update && apt-get install -y docker.io docker-compose-plugin git
adduser pfh
usermod -aG docker pfh
su - pfh

git clone <repo> paris-flat-hunter && cd paris-flat-hunter
cp .env.example .env && chmod 600 .env
vim .env                # paste your secrets

bash scripts/deploy.sh  # builds, migrates, starts, tails logs
```

For updates: `git pull && bash scripts/deploy.sh`.

## Secrets & security

**`.env` is the only secrets surface.** It is in `.gitignore`; never commit it.

| Variable | Where to get it |
| --- | --- |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com → *Settings → API Keys* |
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates` — `chat.id` in the JSON |
| `DISCORD_WEBHOOK_URL` *(optional)* | Leave empty to skip Discord. If wanted: Discord server → Channel → *Edit → Integrations → Webhooks → New Webhook* |
| `NTFY_TOPIC` *(optional)* | Leave empty to skip ntfy.sh. If wanted: any unguessable string like `pfh-9f3k2b7v`. Subscribe on the ntfy mobile app. |
| `POSTGRES_PASSWORD` | Pick a long random value |
| `ADMIN_TOKEN` | Long random value, used for `/admin/*` endpoints |

Security practices:

- `.env` permissions: `chmod 600 .env`
- Logs scrub Anthropic / Telegram tokens via a loguru patcher
  (`src/utils/logging.py`).
- Admin endpoints require `X-Admin-Token` header matching `ADMIN_TOKEN`.
- Postgres not exposed to the public network in `docker-compose.yml`.
- The Docker image runs as a non-root user (`pfh`, uid 1000).

You don't need any external vault — everything reads from env via
`pydantic-settings`. If you want Docker secrets, mount them over the same
env var names and they're picked up identically.

## Telegram bot setup

1. `/newbot` to @BotFather → save the token.
2. Add the bot to your chat (a DM with yourself works).
3. Send any message, then hit `getUpdates` to grab `chat.id`.
4. Put both in `.env`.

Webhook mode is ready but off by default. If you expose the API publicly,
set `TELEGRAM_WEBHOOK_URL=https://your.domain/telegram/webhook` and register
it once: `curl "https://api.telegram.org/bot$TOKEN/setWebhook?url=...”`. The
inline button "✍️ Générer message" wires a callback to
`/telegram/webhook` — stub today, ready to plug a Claude-written contact
message later.

## Discord webhook setup (optional)

Skip this section if you only use Telegram — leave `DISCORD_WEBHOOK_URL=`
empty and the Discord fan-out is disabled automatically.

If you want Discord mirroring: server → target channel → *Edit Channel →
Integrations → Webhooks → New Webhook*. Copy the URL into
`DISCORD_WEBHOOK_URL`.

## Changing criteria

Edit `config/search_criteria.yaml` and restart the scheduler:

```bash
docker compose restart scraper
```

The Haiku and Sonnet system prompts are re-rendered at startup from this file,
so any change takes effect on the next run. Cached prompt fingerprints also
reset automatically because the system text is different.

## Budget behaviour

- Every Anthropic response includes a `usage` block with input / output /
  cache-creation / cache-read tokens. `src/scoring/budget.py` maps those to
  EUR using per-model pricing + `USD_EUR_RATE`.
- Month-to-date totals live in the `api_usage` table.
- `budget-check` job runs every 30 min and DM's you when usage ≥ 80%.
- `allow_scoring()` is called before each stage — once the cap is hit,
  scoring is skipped until the next month rolls over.

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Scraper returns 0 entries | LBC DataDome or SeLoger Cloudflare challenge. Check `docker compose logs scraper`. First line of defense: confirm `PLAYWRIGHT_HEADLESS=true`. Then set `USE_PROXIES=true` and point `PROXY_URL` at IPRoyal/Webshare. |
| `403` / `429` spikes | You are hitting the site too hard. Bump `SCRAPE_INTERVAL_LEBONCOIN` / `_SELOGER` in `.env`. |
| Telegram alert never arrives | Check that the bot was added to the chat *and* that you've sent it at least one message. The chat ID is numeric, positive for DMs, negative for groups. |
| Discord webhook 401 | Webhook URL revoked — regenerate. |
| `scraper X down` Telegram alert | Check last error in `scraper_health` table. Most common: selectors drift. Update the relevant `src/scrapers/<site>.py`. |
| Budget exhausted alert | Raise `ANTHROPIC_MONTHLY_BUDGET_EUR` or wait for next month. |
| Postgres connection refused | `docker compose ps` — make sure `postgres` is `healthy` and migrations ran. |
| Listings parse but aren't scored | Either budget cap reached (see `/admin/stats`) or the zone gate rejected them (postcode not in `zones.include`). |

## Tests

```bash
make test           # unit tests (all mocked)
```

Scraper selectors are NOT tested against live sites — they drift. The base
scraper updates `scraper_health` on every run; the scheduler fires a
Telegram alert after `SCRAPER_FAIL_THRESHOLD` consecutive fails.

## Notification channels

- **Telegram** — main channel, always on (set `TELEGRAM_BOT_TOKEN` +
  `TELEGRAM_CHAT_ID`). Everything works with just this.
- **Discord** (optional, off by default) — set `DISCORD_WEBHOOK_URL` to also
  mirror alerts in a Discord channel. Leave empty to skip.
- **ntfy.sh** (optional, off by default) — set `NTFY_TOPIC=pfh-<random-string>`
  to get free push notifications via the [ntfy mobile app](https://ntfy.sh/).
  Leave empty to skip.

Each channel is independent — only the ones you actually configure fire.

## Auto re-scoring on price drops

The pipeline already re-scores any listing whose price changes at scrape time.
A safety-net job (`rescore-price-drops`, every 2h) catches price drops that
didn't get re-scored in real time (e.g. budget cap, transient API error).

## Pause / Resume (holidays, visits)

```bash
# Mute alerts for 48 hours
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  "http://host:8000/admin/pause?hours=48"

# Resume now
curl -X POST -H "X-Admin-Token: $ADMIN_TOKEN" \
  http://host:8000/admin/resume

# Check state
curl -H "X-Admin-Token: $ADMIN_TOKEN" http://host:8000/admin/pause
```

Scraping + scoring keep running; the notifier worker drops alerts while
paused. Max 168h (7 days) per pause.

## Persistent alert dedup

Alerts are deduplicated at two levels:

1. **In-memory 1h window** — catches same-session duplicates from multiple
   scrapers hitting the same listing.
2. **`listings.status == notified` in DB** — so a container restart never
   re-spams you with listings you've already seen.

## Stale listing cleanup

Daily at 04:00 Europe/Paris, listings not observed in the last 30 days are
flagged `status=gone`. Keeps `/metrics` + dashboard focused on live inventory.

## Agency vs particulier

Every listing gets a `poster_type` tag (`agency` / `private` / `unknown`)
from a cheap heuristic (explicit keywords + source owner_type flags). Surfaced
as `🏢 Agence` or `👤 Particulier` in Telegram alerts — tells you at a glance
whether you'll pay agency fees before you even click.

## Favorites CSV export

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" \
  "http://host:8000/admin/favorites.csv?interest=interested,contacted" \
  -o favorites.csv
```

Filter via `?interest=interested|contacted|not_interested`. Defaults to
`interested,contacted` — your shortlist, ready to share.

## Scoring pipeline (Claude)

The two-stage scoring pipeline goes:

1. **Python pre-filter** (`src/scoring/hard_filter.py`) — zero Claude tokens.
   When all criteria fields are present, we decide pass/fail deterministically.
   Saves ~50% of Haiku calls in steady state.
2. **Haiku tool call** (stage 1, only if the pre-filter is ambiguous) — uses
   Anthropic tool use so the JSON schema is enforced by the API. `temperature=0`
   for reproducibility.
3. **Sonnet tool call with vision** (stage 2, only if stage 1 passes) — same
   tool-use pattern, temperature 0. Sees up to 10 photos.

Every stored score includes `_prompt_version` so tuning the prompt over time
doesn't mix old and new scores. Bump `PROMPT_VERSION` in `src/scoring/prompts.py`
when you materially change the rubric.

## Anti-scam, favorites, weekly digest

- **Scam detector** (`src/scoring/scam_detector.py`) runs cheap heuristics
  before scoring: far-below-market price, Western Union / Bitcoin / "agent
  of God" patterns, foreign phone numbers, low-photo-low-price combos.
  Flagged listings get `status=scam` and are NEVER alerted (saves Claude
  tokens and your sanity).
- **Telegram favorites** — every alert ships with `❤️ Intéressé` /
  `🚫 Pas intéressé` / `📞 Contacté` buttons. Choices persist in
  `listings.user_interest`, surfaced in the weekly digest.
- **Weekly digest** — every Sunday at 19:00 (Europe/Paris) the bot DMs you a
  market recap: total listings vs. last week, top 5 gems, median €/m² per
  arrondissement, Claude spend, scraper health, and your favorites count.
  Run on demand with `docker compose exec scraper python -m src.main digest`.

## Multi-profile search

`config/search_criteria.yaml` accepts both legacy single-profile (default)
and a new multi-profile format:

```yaml
profiles:
  - name: t3_main
    property: { types: { preferred: [T3], acceptable: [T2] }, ... }
    ...
  - name: t2_gem_only
    property: { types: { preferred: [T2], acceptable: [] } }
    scoring: { alert_threshold: 90, urgent_threshold: 95 }
    ...
```

The first profile is used today. Multi-profile fan-out is a one-line change
in the scheduler when you want to enable it.

## Self-test

```bash
python -m src.main selftest
```

Walks the entire stack against a synthetic listing (no Anthropic call, no
Postgres write, no Telegram push). Useful right after a deploy.

## Dashboard + metrics + ops

- **Dashboard** — open `http://<host>:8000/dashboard?token=$ADMIN_TOKEN` in a
  browser. Auto-refreshes every 60s with budget bar, scraper health, last 50
  scored listings (thumbnails + score badges).
- **Prometheus metrics** — `GET /metrics` returns plaintext Prom exposition.
  Scrape from any Prometheus / VictoriaMetrics / Grafana Agent. Counters:
  listings by status+source, scores/alerts/cost for the last 24h + MTD,
  per-scraper failure counts.
- **Rescore** — replay the scoring pipeline on a listing already in DB:
  ```bash
  docker compose exec scraper python scripts/rescore.py <listing_id> --notify
  docker compose exec scraper python scripts/rescore.py --source leboncoin --external 12345
  ```
- **DB backup** — `bash scripts/backup_db.sh` dumps gzipped into `./backups/`,
  default 7-day retention. Cron one-liner at the top of the script.

## Contact-message flow (Telegram "✍️ Générer message")

Tap the inline button under any alert:

1. Claude (Haiku) writes a 4–6 sentence FR message, signed with
   `USER_FIRST_NAME`.
2. If a real landlord email is detected on the listing (mostly PAP
   particuliers + Bien'ici) **and** `AUTO_SEND_EMAIL=true`, the message
   fires via SMTP (e.g. Gmail app-password) and Telegram says
   `✉️ Envoyé à foo@bar`.
3. Otherwise it posts the message in Telegram ready to copy-paste into the
   site's own contact form.

## Status / what's left to tune manually

- **Selectors**: LBC has the most robust path (JSON from `__NEXT_DATA__`).
  SeLoger / PAP / Logic-Immo have tentative selectors — validate on the first
  live run and tune. Bien'ici uses the public JSON API.
- **CX22 perf**: with 5 scrapers polling every 90-300s, 2 vCPU / 4 GB is
  comfortable. If you add many more sources, consider splitting the scraper
  container per site.
- **Contact-message draft**: Telegram inline callback is wired but the
  Claude-draft step is a placeholder. Implement in
  `src/api/routes/telegram_webhook.py`.
- **Scraper failure alerts**: fire after 3 consecutive fails by default;
  adjust `SCRAPER_FAIL_THRESHOLD`.

## License

MIT — personal use.
