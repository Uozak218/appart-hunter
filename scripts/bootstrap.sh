#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 24.04 VPS for Paris Flat Hunter.
# Run as root on first SSH:
#   ssh root@<ip>
#   bash <(curl -fsSL https://raw.githubusercontent.com/SebSusini/LaPepite/main/scripts/bootstrap.sh)
#
# Or if you already git-cloned as root:
#   bash scripts/bootstrap.sh
#
# What this does:
#   1. Updates the system
#   2. Creates a non-root user `pfh` with sudo + your SSH key
#   3. Hardens SSH (no root login, no password auth)
#   4. Enables UFW firewall (SSH only; API stays on loopback/compose network)
#   5. Installs Docker + Docker Compose v2
#   6. Clones the repo into /home/pfh/paris-flat-hunter
#   7. Drops a pre-filled .env skeleton (chmod 600)
#
# After it finishes:
#   - ssh pfh@<ip>
#   - cd paris-flat-hunter
#   - vim .env                  # paste your 5 tokens
#   - bash scripts/deploy.sh    # builds images + migrates DB + starts services

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/SebSusini/LaPepite.git}"
PFH_USER="${PFH_USER:-pfh}"
APP_DIR="/home/${PFH_USER}/paris-flat-hunter"
SSH_PORT="${SSH_PORT:-22}"

log() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
err() { printf "\n\033[1;31mERROR: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Run this script as root."

log "Updating APT + upgrading system"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y --no-install-recommends \
    ca-certificates curl git vim ufw fail2ban \
    apt-transport-https gnupg lsb-release unattended-upgrades

log "Enabling unattended security upgrades"
dpkg-reconfigure -f noninteractive unattended-upgrades

# ---- User ------------------------------------------------------------------
if id -u "${PFH_USER}" >/dev/null 2>&1; then
    log "User ${PFH_USER} already exists"
else
    log "Creating user ${PFH_USER}"
    adduser --disabled-password --gecos "" "${PFH_USER}"
fi
usermod -aG sudo "${PFH_USER}"

# Copy root's SSH key to the pfh user so you can log in as pfh right away
if [[ -f /root/.ssh/authorized_keys ]]; then
    log "Propagating SSH keys from root to ${PFH_USER}"
    install -d -m 700 -o "${PFH_USER}" -g "${PFH_USER}" "/home/${PFH_USER}/.ssh"
    install -m 600 -o "${PFH_USER}" -g "${PFH_USER}" \
        /root/.ssh/authorized_keys "/home/${PFH_USER}/.ssh/authorized_keys"
else
    log "WARNING: /root/.ssh/authorized_keys not found — add your public key to /home/${PFH_USER}/.ssh/authorized_keys manually before logging out of root."
fi

# Passwordless sudo for pfh (you're the only user, convenience > ceremony)
echo "${PFH_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-${PFH_USER}"
chmod 440 "/etc/sudoers.d/90-${PFH_USER}"

# ---- SSH hardening ---------------------------------------------------------
log "Hardening SSH"
SSHD_CONF=/etc/ssh/sshd_config
cp "${SSHD_CONF}" "${SSHD_CONF}.bak.$(date +%s)"
sed -ri 's/^#?PermitRootLogin.*/PermitRootLogin no/' "${SSHD_CONF}"
sed -ri 's/^#?PasswordAuthentication.*/PasswordAuthentication no/' "${SSHD_CONF}"
sed -ri 's/^#?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' "${SSHD_CONF}"
sed -ri 's/^#?ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' "${SSHD_CONF}"
# Restart via the modern socket unit or classic service
systemctl restart ssh 2>/dev/null || systemctl restart sshd

# ---- Firewall --------------------------------------------------------------
log "Configuring UFW"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow "${SSH_PORT}"/tcp
# The FastAPI port stays INSIDE the docker network by default.
# If you want to expose it publicly, uncomment the next line and set a strong ADMIN_TOKEN:
# ufw allow 8000/tcp
ufw --force enable

log "Enabling fail2ban"
systemctl enable --now fail2ban

# ---- Docker ----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker Engine + Compose v2"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
else
    log "Docker already installed"
fi

usermod -aG docker "${PFH_USER}"

# ---- Clone repo ------------------------------------------------------------
if [[ -d "${APP_DIR}/.git" ]]; then
    log "Repo already cloned at ${APP_DIR} — pulling latest"
    sudo -u "${PFH_USER}" git -C "${APP_DIR}" pull --ff-only || true
else
    log "Cloning ${REPO_URL} into ${APP_DIR}"
    sudo -u "${PFH_USER}" git clone "${REPO_URL}" "${APP_DIR}"
fi

# ---- .env skeleton ---------------------------------------------------------
ENV_FILE="${APP_DIR}/.env"
if [[ -f "${ENV_FILE}" ]]; then
    log ".env already exists — leaving it alone"
else
    log "Creating .env from .env.example"
    cp "${APP_DIR}/.env.example" "${ENV_FILE}"

    # Generate strong random values for DB password + admin token.
    PG_PASS="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
    ADMIN_TOKEN="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
    sed -ri "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${PG_PASS}|" "${ENV_FILE}"
    sed -ri "s|^ADMIN_TOKEN=.*|ADMIN_TOKEN=${ADMIN_TOKEN}|" "${ENV_FILE}"
    sed -ri "s|^APP_ENV=.*|APP_ENV=prod|" "${ENV_FILE}"

    chown "${PFH_USER}:${PFH_USER}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
fi

# ---- Done ------------------------------------------------------------------
IP_ADDR="$(curl -s -4 https://ifconfig.me || echo 'your.vps.ip')"

cat <<EOF

============================================================
  🎉 Bootstrap complete.
============================================================

Next steps (run these from YOUR laptop):

  1) SSH in as the new user:
       ssh ${PFH_USER}@${IP_ADDR}

  2) Edit .env and paste your 3 tokens:
       cd paris-flat-hunter
       vim .env
     Fill in:
       - ANTHROPIC_API_KEY
       - TELEGRAM_BOT_TOKEN  + TELEGRAM_CHAT_ID
       - DISCORD_WEBHOOK_URL
     POSTGRES_PASSWORD and ADMIN_TOKEN are already randomised.

  3) Deploy:
       bash scripts/deploy.sh

  4) Sanity checks:
       docker compose ps
       curl http://localhost:8000/health
       docker compose logs -f scraper

Security state:
  - Root SSH login: disabled
  - Password SSH login: disabled (keys only)
  - UFW: default deny, SSH allowed
  - fail2ban: active
  - Unattended security upgrades: enabled
  - API port 8000: NOT exposed publicly (internal to compose network)

Your admin token (save it somewhere):
  grep ^ADMIN_TOKEN= ${ENV_FILE}

EOF
