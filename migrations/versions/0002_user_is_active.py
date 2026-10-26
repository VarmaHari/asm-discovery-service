"""Add active status to users.

Revision ID: 0002_user_is_active
Revises: 0001_core_tables
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_user_is_active"
down_revision = "0001_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_active")
