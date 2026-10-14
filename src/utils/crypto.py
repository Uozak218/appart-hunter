"""Fernet symmetric encryption for per-user Anthropic keys at rest.

Each user's API key is stored ``Fernet.encrypt(key)`` in ``users.anthropic_api_key_enc``
and decrypted only at scoring time. The Fernet master key lives in
``ENCRYPTION_KEY`` env var.

Generate a fresh key with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from src.config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = get_settings().encryption_key.encode()
    if not key:
        raise RuntimeError(
            'ENCRYPTION_KEY missing. Generate one with: python -c "from '
            'cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string (e.g. an Anthropic API key) and return urlsafe base64."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet token back to plaintext; raises ``InvalidToken`` on tamper."""
    if not ciphertext:
        return ""
    return _fernet().decrypt(ciphertext.encode()).decode()


def safe_decrypt(ciphertext: str) -> str | None:
    """Like :func:`decrypt` but returns ``None`` instead of raising — useful
    when reading user records where the master key may have rotated.
    """
    try:
        return decrypt(ciphertext)
    except (InvalidToken, ValueError):
        return None
