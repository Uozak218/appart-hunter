"""Initial schema.

Creates all core tables and enables the PostGIS extension.

Revision ID: 20260418_0001
Revises:
Create Date: 2026-04-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260418_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    listing_status = sa.Enum(
        "new", "scored", "notified", "filtered_out", "gone", name="listing_status"
    )
    score_stage = sa.Enum("haiku_filter", "sonnet_pepite", name="score_stage")

    op.create_table(
        "listings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("rent_cc", sa.Float),
        sa.Column("charges", sa.Float),
        sa.Column("surface_m2", sa.Float),
        sa.Column("rooms", sa.Integer),
        sa.Column("property_type", sa.String(8)),
        sa.Column("furnished", sa.Boolean),
        sa.Column("floor", sa.Integer),
        sa.Column("has_elevator", sa.Boolean),
        sa.Column("postal_code", sa.String(8)),
        sa.Column("arrondissement", sa.Integer),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("address", sa.Text),
        sa.Column("raw", postgresql.JSONB),
        sa.Column("external_hash", sa.String(64), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("status", listing_status, nullable=False, server_default="new"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_listings_source_extid"),
    )
    op.create_index("ix_listings_source", "listings", ["source"])
    op.create_index("ix_listings_external_id", "listings", ["external_id"])
    op.create_index("ix_listings_postal_code", "listings", ["postal_code"])
    op.create_index("ix_listings_external_hash", "listings", ["external_hash"])
    op.create_index("ix_listings_dedup_key", "listings", ["dedup_key"])
    op.create_index("ix_listings_status", "listings", ["status"])
    op.create_index("ix_listings_dedup_key_status", "listings", ["dedup_key", "status"])

    op.create_table(
        "listing_photos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, server_default="0", nullable=False),
        sa.Column("meta", sa.JSON),
    )
    op.create_index("ix_listing_photos_listing_id", "listing_photos", ["listing_id"])

    op.create_table(
        "price_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rent_cc", sa.Float, nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_price_history_listing_id", "price_history", ["listing_id"])
    op.create_index(
        "ix_price_history_listing_time", "price_history", ["listing_id", "observed_at"]
    )

    op.create_table(
        "scores",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", score_stage, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("score", sa.Integer),
        sa.Column("reasons", postgresql.JSONB),
        sa.Column("red_flags", postgresql.JSONB),
        sa.Column("highlights", postgresql.JSONB),
        sa.Column("raw", postgresql.JSONB),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_scores_listing_id", "scores", ["listing_id"])
    op.create_index("ix_scores_listing_stage", "scores", ["listing_id", "stage"])

    op.create_table(
        "api_usage",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("input_tokens", sa.Integer, server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer, server_default="0", nullable=False),
        sa.Column(
            "cache_creation_input_tokens", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column(
            "cache_read_input_tokens", sa.Integer, server_default="0", nullable=False
        ),
        sa.Column("cost_usd", sa.Float, server_default="0", nullable=False),
        sa.Column("cost_eur", sa.Float, server_default="0", nullable=False),
        sa.Column(
            "listing_id",
            sa.Integer,
            sa.ForeignKey("listings.id", ondelete="SET NULL"),
        ),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_api_usage_created_at", "api_usage", ["created_at"])
    op.create_index("ix_api_usage_listing_id", "api_usage", ["listing_id"])

    op.create_table(
        "scraper_health",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(32), nullable=False, unique=True),
        sa.Column("consecutive_failures", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_runs", sa.Integer, server_default="0", nullable=False),
        sa.Column("total_failures", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("last_listings_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("alerted", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("scraper_health")
    op.drop_index("ix_api_usage_listing_id", table_name="api_usage")
    op.drop_index("ix_api_usage_created_at", table_name="api_usage")
    op.drop_table("api_usage")
    op.drop_index("ix_scores_listing_stage", table_name="scores")
    op.drop_index("ix_scores_listing_id", table_name="scores")
    op.drop_table("scores")
    op.drop_index("ix_price_history_listing_time", table_name="price_history")
    op.drop_index("ix_price_history_listing_id", table_name="price_history")
    op.drop_table("price_history")
    op.drop_index("ix_listing_photos_listing_id", table_name="listing_photos")
    op.drop_table("listing_photos")
    for idx in (
        "ix_listings_dedup_key_status",
        "ix_listings_status",
        "ix_listings_dedup_key",
        "ix_listings_external_hash",
        "ix_listings_postal_code",
        "ix_listings_external_id",
        "ix_listings_source",
    ):
        op.drop_index(idx, table_name="listings")
    op.drop_table("listings")
    sa.Enum(name="score_stage").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="listing_status").drop(op.get_bind(), checkfirst=True)
