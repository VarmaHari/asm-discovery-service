"""Align users, domains, roles, and scans with the assignment contract.

Revision ID: 0006_assignment_contract_alignment
Revises: 0005_scan_asset_query_indexes
Create Date: 2026-10-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_assignment_contract_alignment"
down_revision = "0005_scan_asset_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_email = bind.execute(
        sa.text(
            "SELECT lower(email) FROM users GROUP BY lower(email) "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate_email is not None:
        raise RuntimeError(
            "Cannot apply case-insensitive email uniqueness: resolve "
            f"duplicate email {duplicate_email[0]!r} first."
        )
    duplicate = bind.execute(
        sa.text(
            "SELECT lower(name) FROM domains GROUP BY lower(name) "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot apply global domain uniqueness: resolve duplicate "
            f"domain name {duplicate[0]!r} across owners first."
        )
    op.execute(sa.text("UPDATE domains SET name = lower(name)"))
    op.execute(sa.text("UPDATE users SET email = lower(email)"))

    op.add_column(
        "users",
        sa.Column("full_name", sa.String(length=150), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE users SET full_name = email"
        )
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "full_name",
            existing_type=sa.String(length=150),
            nullable=False,
        )
        batch_op.drop_constraint("valid_role", type_="check")
        batch_op.drop_constraint("uq_users_email", type_="unique")
    op.execute(sa.text("UPDATE users SET role = upper(role)"))
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "role",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="VIEWER",
        )
        batch_op.create_check_constraint(
            "valid_role",
            "role IN ('ADMIN', 'ANALYST', 'VIEWER')",
        )
    op.create_index(
        "uq_users_email_ci",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.add_column(
        "domains",
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="PENDING",
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE domains
            SET status = CASE (
                SELECT lower(scans.status)
                FROM scans
                WHERE scans.domain_id = domains.id
                ORDER BY scans.created_at DESC, scans.id DESC
                LIMIT 1
            )
                WHEN 'running' THEN 'RUNNING'
                WHEN 'completed' THEN 'COMPLETED'
                WHEN 'failed' THEN 'FAILED'
                ELSE 'PENDING'
            END
            """
        )
    )
    with op.batch_alter_table("domains") as batch_op:
        batch_op.drop_constraint("uq_domains_user_name", type_="unique")
        batch_op.alter_column(
            "user_id",
            new_column_name="created_by",
            existing_type=sa.Uuid(as_uuid=True),
            existing_nullable=False,
        )
        batch_op.create_check_constraint(
            "valid_status",
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
        )

    op.create_index(
        "uq_domains_name_ci",
        "domains",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_index("ix_domains_status", "domains", ["status"], unique=False)
    op.create_index(
        "ix_domains_created_by_status",
        "domains",
        ["created_by", "status"],
        unique=False,
    )

    op.drop_index(
        "uq_scans_single_running_per_domain",
        table_name="scans",
    )
    with op.batch_alter_table("scans") as batch_op:
        batch_op.drop_constraint("valid_status", type_="check")
    op.execute(sa.text("UPDATE scans SET status = upper(status)"))
    with op.batch_alter_table("scans") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="PENDING",
        )
        batch_op.create_check_constraint(
            "valid_status",
            "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
        )
    op.create_index(
        "uq_scans_single_running_per_domain",
        "scans",
        ["domain_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
        sqlite_where=sa.text("status = 'RUNNING'"),
    )

    with op.batch_alter_table("scans") as batch_op:
        batch_op.add_column(
            sa.Column("triggered_by", sa.Uuid(as_uuid=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_scans_triggered_by_users",
            "users",
            ["triggered_by"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(
        sa.text(
            """
            UPDATE scans
            SET triggered_by = (
                SELECT domains.created_by
                FROM domains
                WHERE domains.id = scans.domain_id
            )
            """
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("scans") as batch_op:
        batch_op.drop_constraint("fk_scans_triggered_by_users", type_="foreignkey")
        batch_op.drop_column("triggered_by")
    op.drop_index(
        "uq_scans_single_running_per_domain",
        table_name="scans",
    )
    with op.batch_alter_table("scans") as batch_op:
        batch_op.drop_constraint("valid_status", type_="check")
    op.execute(sa.text("UPDATE scans SET status = lower(status)"))
    with op.batch_alter_table("scans") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            existing_nullable=False,
            server_default="pending",
        )
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

    op.drop_index("ix_domains_created_by_status", table_name="domains")
    op.drop_index("ix_domains_status", table_name="domains")
    op.drop_index("uq_domains_name_ci", table_name="domains")
    with op.batch_alter_table("domains") as batch_op:
        batch_op.drop_constraint("valid_status", type_="check")
        batch_op.alter_column(
            "created_by",
            new_column_name="user_id",
            existing_type=sa.Uuid(as_uuid=True),
            existing_nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_domains_user_name",
            ["user_id", "name"],
        )
        batch_op.drop_column("status")

    op.drop_index("uq_users_email_ci", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("valid_role", type_="check")
    op.execute(
        sa.text(
            "UPDATE users SET role = CASE role "
            "WHEN 'ADMIN' THEN 'Admin' "
            "WHEN 'ANALYST' THEN 'Analyst' ELSE 'Viewer' END"
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
        batch_op.drop_column("full_name")
        batch_op.create_unique_constraint("uq_users_email", ["email"])
