"""Multi-tenant users + per-user criteria.

A ``User`` is one human using the bot via Telegram DM (chat_id = primary
external identity). Each user carries their **own Anthropic API key**
(BYOK — encrypted at rest) so scoring cost is metered per-user.

Scrapfly stays shared infrastructure (admin-side, not per-user).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.storage.database import Base


class UserStatus(enum.StrEnum):
    onboarding = "onboarding"  # wizard in progress, no criteria yet
    active = "active"  # alerts ON
    paused = "paused"  # alerts OFF (user typed /pause)
    disabled = "disabled"  # bot disabled (key invalid / repeated 401)


class WizardStep(enum.StrEnum):
    """States of the onboarding ConversationHandler.

    Mirrors the 8-question flow: key → budget → surface → types → arr →
    excludes → threshold → confirm. Stored so we can resume mid-wizard if
    a user goes offline.
    """

    not_started = "not_started"
    ask_key = "ask_key"
    ask_budget = "ask_budget"
    ask_surface = "ask_surface"
    ask_types = "ask_types"
    ask_arrondissements = "ask_arrondissements"
    ask_excludes = "ask_excludes"
    ask_threshold = "ask_threshold"
    confirm = "confirm"
    done = "done"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Fernet-encrypted Anthropic API key (sk-ant-...). Decrypted only at
    # scoring time. Never logged in clear.
    anthropic_api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), default=UserStatus.onboarding
    )
    wizard_step: Mapped[WizardStep] = mapped_column(
        Enum(WizardStep, name="user_wizard_step"), default=WizardStep.not_started
    )
    # Counters for ops visibility — bumped by handlers, never reset.
    consecutive_key_failures: Mapped[int] = mapped_column(Integer, default=0)
    alerts_sent_total: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    criteria: Mapped[UserCriteria | None] = relationship(
        "UserCriteria", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class UserCriteria(Base):
    __tablename__ = "user_criteria"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Free-form JSON criteria mirroring the YAML SearchCriteria shape.
    # Stored as JSONB so we can index/filter later if needed.
    criteria: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    alert_threshold: Mapped[int] = mapped_column(Integer, default=70)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship("User", back_populates="criteria")
