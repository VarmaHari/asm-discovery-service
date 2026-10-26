"""Add scan history and asset filter indexes.

Revision ID: 0005_scan_asset_query_indexes
Revises: 0004_scan_worker_lifecycle
Create Date: 2026-10-21
"""

from alembic import op


revision = "0005_scan_asset_query_indexes"
down_revision = "0004_scan_worker_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scans_domain_created_at",
        "scans",
        ["domain_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_assets_scan_record_type",
        "assets",
        ["scan_id", "record_type"],
        unique=False,
    )
    op.create_index(
        "ix_assets_record_type_created_at",
        "assets",
        ["record_type", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assets_record_type_created_at", table_name="assets")
    op.drop_index("ix_assets_scan_record_type", table_name="assets")
    op.drop_index("ix_scans_domain_created_at", table_name="scans")
