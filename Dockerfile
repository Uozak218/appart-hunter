# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tini \
        ca-certificates \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Optional: set INSTALL_DEV=1 to install the [dev] extras (pytest, ruff, mypy).
# Used by docker-compose.override.yml for local dev. Prod image stays slim.
ARG INSTALL_DEV=0
ENV INSTALL_DEV=${INSTALL_DEV}

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "1" ]; then \
           pip install ".[dev]"; \
       else \
           pip install .; \
       fi

# Install Chromium + OS deps for Playwright
RUN python -m playwright install --with-deps chromium

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY config ./config

# Non-root user. Pre-create mountpoints for named volumes so Docker inherits
# the correct ownership when they are first attached (otherwise Docker creates
# them as root:root and pfh can't write to /app/.playwright or /app/logs).
RUN useradd -m -u 1000 pfh \
    && mkdir -p /app/.playwright /app/logs /app/backups \
    && chown -R pfh:pfh /app /ms-playwright
USER pfh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.main", "all"]
