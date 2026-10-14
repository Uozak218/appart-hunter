"""Multi-tenant: users + user_criteria tables.

Revision ID: 20260518_0003
Revises: 20260418_0002
Create Date: 2026-05-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260518_0003"
down_revision = "20260418_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    user_status = sa.Enum(
        "onboarding", "active", "paused", "disabled", name="user_status"
    )
    wizard_step = sa.Enum(
        "not_started",
        "ask_key",
        "ask_budget",
        "ask_surface",
        "ask_types",
        "ask_arrondissements",
        "ask_excludes",
        "ask_threshold",
        "confirm",
        "done",
        name="user_wizard_step",
    )
    user_status.create(op.get_bind(), checkfirst=True)
    wizard_step.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_chat_id", sa.String(32), nullable=False, unique=True),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("telegram_first_name", sa.String(128), nullable=True),
        sa.Column("anthropic_api_key_enc", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("onboarding", "active", "paused", "disabled", name="user_status", create_type=False),
            nullable=False,
            server_default="onboarding",
        ),
        sa.Column(
            "wizard_step",
            postgresql.ENUM(
                "not_started",
                "ask_key",
                "ask_budget",
                "ask_surface",
                "ask_types",
                "ask_arrondissements",
                "ask_excludes",
                "ask_threshold",
                "confirm",
                "done",
                name="user_wizard_step",
                create_type=False,
            ),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column("consecutive_key_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alerts_sent_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_active_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_telegram_chat_id", "users", ["telegram_chat_id"], unique=True)

    op.create_table(
        "user_criteria",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("criteria", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("alert_threshold", sa.Integer(), nullable=False, server_default="70"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_criteria")
    op.drop_index("ix_users_telegram_chat_id", table_name="users")
    op.drop_table("users")
    sa.Enum(name="user_wizard_step").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_status").drop(op.get_bind(), checkfirst=True)
