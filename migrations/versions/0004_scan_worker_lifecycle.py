"""Add scan lifecycle integrity and failure details.

Revision ID: 0004_scan_worker_lifecycle
Revises: 0003_normalize_user_roles
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_scan_worker_lifecycle"
down_revision = "0003_normalize_user_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("error_message", sa.String(length=1024), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE scans SET status = lower(status) "
            "WHERE lower(status) IN ('pending', 'running', 'completed', 'failed')"
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY domain_id ORDER BY created_at, id
                ) AS position
                FROM scans
                WHERE status = 'running'
            )
            UPDATE scans
            SET status = 'failed',
                error_message = 'Duplicate running scan normalized during migration',
                completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP)
            WHERE id IN (SELECT id FROM ranked WHERE position > 1)
            """
        )
    )
    with op.batch_alter_table("scans") as batch_op:
        batch_op.create_check_constraint(
            "valid_status",
            "status IN ('pending', 'running', 'completed', 'failed')",
        )
    op.create_index(
        "uq_scans_single_running_per_domain",
        "scans",
        ["domain_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
        sqlite_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_scans_single_running_per_domain",
        table_name="scans",
    )
    with op.batch_alter_table("scans") as batch_op:
        batch_op.drop_constraint("valid_status", type_="check")
        batch_op.drop_column("error_message")
