"""DB access for the bot — get/create users, save criteria.

Wraps sqlalchemy queries so handlers stay thin. All functions accept and
operate within the caller's session so we get a single transaction per
Telegram update.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User, UserCriteria, UserStatus, WizardStep
from src.utils.crypto import encrypt


async def get_user_by_chat(session: AsyncSession, chat_id: int | str) -> User | None:
    r = await session.execute(select(User).where(User.telegram_chat_id == str(chat_id)))
    return r.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    chat_id: int | str,
    username: str | None,
    first_name: str | None,
) -> tuple[User, bool]:
    """Returns ``(user, created)``."""
    user = await get_user_by_chat(session, chat_id)
    if user is not None:
        # Keep username/first_name fresh — Telegram allows renames.
        changed = False
        if username and user.telegram_username != username:
            user.telegram_username = username
            changed = True
        if first_name and user.telegram_first_name != first_name:
            user.telegram_first_name = first_name
            changed = True
        if changed:
            await session.flush()
        return user, False
    user = User(
        telegram_chat_id=str(chat_id),
        telegram_username=username,
        telegram_first_name=first_name,
        status=UserStatus.onboarding,
        wizard_step=WizardStep.not_started,
    )
    session.add(user)
    await session.flush()
    return user, True


async def set_wizard_step(session: AsyncSession, user: User, step: WizardStep) -> None:
    user.wizard_step = step
    await session.flush()


async def save_anthropic_key(session: AsyncSession, user: User, plaintext_key: str) -> None:
    user.anthropic_api_key_enc = encrypt(plaintext_key)
    user.consecutive_key_failures = 0
    await session.flush()


async def save_criteria(
    session: AsyncSession,
    user: User,
    criteria: dict[str, Any],
    alert_threshold: int,
) -> None:
    """Upsert ``user_criteria`` for this user and flip status to ``active``."""
    if user.criteria is None:
        uc = UserCriteria(
            user_id=user.id,
            criteria=criteria,
            alert_threshold=alert_threshold,
        )
        session.add(uc)
    else:
        user.criteria.criteria = criteria
        user.criteria.alert_threshold = alert_threshold
    user.status = UserStatus.active
    user.wizard_step = WizardStep.done
    await session.flush()


async def set_status(session: AsyncSession, user: User, status: UserStatus) -> None:
    user.status = status
    await session.flush()
