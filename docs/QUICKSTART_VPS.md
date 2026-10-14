# Quickstart — Paris Flat Hunter on Hetzner CX22

End-to-end setup in ~15 minutes, assuming you already have an SSH keypair on
your laptop (`~/.ssh/id_ed25519.pub`).

## 1. Create the VPS

1. Sign up at <https://console.hetzner.cloud/>.
2. **Add Project** → *Paris Flat Hunter* → **Add Server**.
3. Settings:
   - Location: **Nuremberg** or **Helsinki** (whichever is closer).
   - OS: **Ubuntu 24.04**.
   - Type: **Shared vCPU → CX22** (2 vCPU, 4 GB, ~4 €/mois).
   - Networking: tick **Public IPv4** and **Public IPv6**.
   - SSH keys: paste `~/.ssh/id_ed25519.pub` (or click *Add*).
   - Name: `pfh-prod`.
4. **Create & Buy now**. Hetzner emails you the IP.

## 2. Bootstrap the box (1 minute)

From your laptop:

```bash
ssh root@<VPS_IP>
# once logged in:
bash <(curl -fsSL https://raw.githubusercontent.com/Uozak218/appart-hunter/main/scripts/bootstrap.sh)
```

The script will:

- create user `pfh` with your SSH key
- disable root SSH + password SSH
- enable UFW (SSH only) + fail2ban + unattended security upgrades
- install Docker + Compose v2
- clone the repo into `/home/pfh/paris-flat-hunter`
- generate strong random values for `POSTGRES_PASSWORD` and `ADMIN_TOKEN`
- chmod 600 the `.env` file

When it finishes it prints a recap.

## 3. Paste your API tokens

```bash
# still from your laptop
ssh pfh@<VPS_IP>
cd paris-flat-hunter
vim .env
```

Fill in these three:

```
ANTHROPIC_API_KEY=sk-ant-...              # console.anthropic.com
TELEGRAM_BOT_TOKEN=123456789:AA...         # @BotFather on Telegram
TELEGRAM_CHAT_ID=123456789                 # see "Telegram chat id" below
```

`POSTGRES_PASSWORD` and `ADMIN_TOKEN` are already randomised by the bootstrap
script — don't touch them.

*Discord and ntfy.sh are optional*: leave `DISCORD_WEBHOOK_URL=` and
`NTFY_TOPIC=` empty to skip them. Telegram alone is the recommended setup.

### Telegram chat id

1. On Telegram, DM `@BotFather`, `/newbot`, follow prompts → copy the token
   into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot ("hi").
3. From your laptop:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates" | python3 -m json.tool
   ```
   Look for `"chat":{"id": 123456789, ...}` → that number is your
   `TELEGRAM_CHAT_ID`.

### Discord webhook (optional, skip if Telegram-only)

If you want Discord mirroring too: Discord server → target channel →
*Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL*.
Paste into `DISCORD_WEBHOOK_URL`. Otherwise leave it empty.

## 4. Deploy

```bash
bash scripts/deploy.sh
```

This builds the Docker image, runs Alembic migrations, starts Postgres + API
+ scheduler, and tails the logs. First build takes ~3-5 min (downloads
Chromium).

## 5. Sanity checks

From the VPS:

```bash
docker compose ps                 # all containers healthy
curl http://localhost:8000/health # {"status":"ok"}
docker compose logs -f scraper    # watch the first LBC / SeLoger run
```

Send yourself a test alert by temporarily lowering the score threshold in
`.env`:

```bash
echo "SCORE_ALERT_THRESHOLD=0" >> .env
docker compose restart scraper
# wait for the next scrape, you should get a Telegram + Discord alert
# then revert:
sed -i '/^SCORE_ALERT_THRESHOLD=0$/d' .env
docker compose restart scraper
```

## 6. Day-2 ops

| Action | Command |
| --- | --- |
| Pull latest + redeploy | `bash scripts/deploy.sh` |
| Tail logs | `docker compose logs -f` |
| Tail a single service | `docker compose logs -f scraper` |
| Admin stats | `curl -H "X-Admin-Token: $(grep ^ADMIN_TOKEN= .env \| cut -d= -f2)" http://localhost:8000/admin/stats` |
| Trigger a scrape now | `curl -X POST -H "X-Admin-Token: $TOKEN" http://localhost:8000/admin/scrape/leboncoin` |
| Edit search criteria | `vim config/search_criteria.yaml && docker compose restart scraper` |
| Stop everything | `docker compose down` |
| Postgres shell | `docker compose exec postgres psql -U pfh -d pfh` |

## 7. Costs

- Hetzner CX22: **~4 €/mois** + 0.60 €/mois for the public IPv4.
- Anthropic API: capped at **30 €/mois** by design. Realistic spend with
  prompt caching + Haiku-gating is typically 5-15 €/mois.
- Telegram, Discord: free.

Total: ~**5-20 €/mois** all-in.
