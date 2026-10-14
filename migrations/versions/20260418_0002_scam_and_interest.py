"""Add scam_score / scam_reasons / user_interest to listings, plus new enum values.

Revision ID: 20260418_0002
Revises: 20260418_0001
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260418_0002"
down_revision = "20260418_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend listing_status enum with the new "scam" value.
    op.execute("ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'scam'")

    user_interest = sa.Enum(
        "none", "interested", "not_interested", "contacted", name="user_interest"
    )
    user_interest.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "listings",
        sa.Column("scam_score", sa.Integer, server_default="0", nullable=False),
    )
    op.add_column(
        "listings",
        sa.Column("scam_reasons", postgresql.JSONB, nullable=True),
    )
    op.add_column(
        "listings",
        sa.Column(
            "user_interest",
            user_interest,
            server_default="none",
            nullable=False,
        ),
    )
    op.create_index("ix_listings_user_interest", "listings", ["user_interest"])


def downgrade() -> None:
    op.drop_index("ix_listings_user_interest", table_name="listings")
    op.drop_column("listings", "user_interest")
    op.drop_column("listings", "scam_reasons")
    op.drop_column("listings", "scam_score")
    sa.Enum(name="user_interest").drop(op.get_bind(), checkfirst=True)
    # Note: Postgres can't easily remove a single enum value; left in place.
