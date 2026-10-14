"""Centralised logging via loguru.

Rotating file sink + coloured stdout. Sensitive tokens are scrubbed.

In addition to our app loggers (``loguru``), we also tame the two noisiest
stdlib loggers so prod logs stay readable:
- ``uvicorn.access`` is muted on polled healthcheck paths (/health, /ready,
  /metrics, /dashboard) via ``_HealthcheckFilter``.
- ``sqlalchemy.engine`` stays at WARNING (no statement echo).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from loguru import logger

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9\-_]+"),
    re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"),  # Telegram tokens
    re.compile(r"https://discord\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
]


_HEALTHCHECK_PATHS = ("/health", "/ready", "/metrics")


class _HealthcheckFilter(logging.Filter):
    """Drop uvicorn access logs for noisy polled endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(f'"GET {p}' in msg for p in _HEALTHCHECK_PATHS)


def _scrub(message: str) -> str:
    for pat in SECRET_PATTERNS:
        message = pat.sub("<redacted>", message)
    return message


def _patcher(record: dict) -> None:
    record["message"] = _scrub(record["message"])


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    logger.remove()
    logger.configure(patcher=_patcher)

    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan> "
            "<level>{message}</level>"
        ),
        enqueue=True,
    )

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_path / "pfh.log",
        level=level,
        rotation="00:00",  # daily
        retention="14 days",
        compression="gz",
        enqueue=True,
    )

    # Tame stdlib loggers that Uvicorn + SQLAlchemy install.
    uv_access = logging.getLogger("uvicorn.access")
    uv_access.addFilter(_HealthcheckFilter())

    # Defensive: drop the equivalent on uvicorn.error too (ASGI lifespan spam).
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


__all__ = ["logger", "setup_logging"]
