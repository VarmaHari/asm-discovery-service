"""Normalize user roles for authorization.

Revision ID: 0003_normalize_user_roles
Revises: 0002_user_is_active
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_normalize_user_roles"
down_revision = "0002_user_is_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE users SET role = 'Admin' WHERE lower(role) = 'admin'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET role = 'Analyst' WHERE lower(role) = 'user'"
        )
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="Viewer",
        )
        batch_op.create_check_constraint(
            "valid_role",
            "role IN ('Admin', 'Analyst', 'Viewer')",
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("valid_role", type_="check")
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="user",
        )
    op.execute(
        sa.text(
            "UPDATE users SET role = 'admin' WHERE role = 'Admin'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE users SET role = 'user' WHERE role IN ('Analyst', 'Viewer')"
        )
    )
