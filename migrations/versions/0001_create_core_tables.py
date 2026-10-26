"""Create users, domains, scans, and assets.

Revision ID: 0001_core_tables
Revises:
Create Date: 2026-10-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.String(length=32),
            server_default="user",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "domains",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=253), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_domains_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_domains"),
        sa.UniqueConstraint("user_id", "name", name="uq_domains_user_name"),
    )
    op.create_index("ix_domains_name", "domains", ["name"], unique=False)

    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("domain_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domains.id"],
            name="fk_scans_domain_id_domains",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scans"),
    )
    op.create_index("ix_scans_domain_id", "scans", ["domain_id"], unique=False)
    op.create_index("ix_scans_status", "scans", ["status"], unique=False)

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scan_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("record_type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_assets_scan_id_scans",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
        sa.UniqueConstraint(
            "scan_id",
            "record_type",
            "value",
            name="uq_assets_scan_record_value",
        ),
    )
    op.create_index("ix_assets_scan_id", "assets", ["scan_id"], unique=False)
    op.create_index(
        "ix_assets_record_type",
        "assets",
        ["record_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_assets_record_type", table_name="assets")
    op.drop_index("ix_assets_scan_id", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_scans_status", table_name="scans")
    op.drop_index("ix_scans_domain_id", table_name="scans")
    op.drop_table("scans")
    op.drop_index("ix_domains_name", table_name="domains")
    op.drop_table("domains")
    op.drop_table("users")
