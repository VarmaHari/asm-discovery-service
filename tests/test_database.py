import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.engine import URL

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import create_database_engine, get_db
from app.models import Asset, Domain, Scan, User


def _sqlite_engine():
    engine = create_database_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def test_database_engine_connects() -> None:
    engine = create_database_engine("sqlite://")

    with engine.connect() as connection:
        assert connection.scalar(select(text("1"))) == 1

    engine.dispose()


def test_model_constraints_and_delete_cascade() -> None:
    engine = _sqlite_engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        owner = User(
            email="OWNER@example.com",
            full_name="Owner User",
            hashed_password="hash",
        )
        session.add(owner)
        session.flush()
        domain = Domain(name="example.com", owner=owner)
        session.add(domain)
        session.flush()
        scan = Scan(domain=domain)
        session.add(scan)
        session.flush()
        asset = Asset(scan=scan, record_type="A", value="192.0.2.1")
        session.add(asset)
        session.commit()
        domain_id = domain.id
        scan_id = scan.id
        asset_id = asset.id

        with pytest.raises(IntegrityError):
            session.add(
                User(
                    email="owner@example.com",
                    full_name="Duplicate Owner",
                    hashed_password="hash",
                )
            )
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.add(Domain(name="EXAMPLE.COM", created_by=owner.id))
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            session.add(
                Asset(
                    scan_id=scan.id,
                    record_type="A",
                    value="192.0.2.1",
                )
            )
            session.commit()
        session.rollback()

        session.delete(owner)
        session.commit()
        assert session.get(Domain, domain_id) is None
        assert session.get(Scan, scan_id) is None
        assert session.get(Asset, asset_id) is None

    engine.dispose()


def test_initial_migration_creates_core_tables(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "clean_migration.db"
    database_url = str(
        URL.create("sqlite", database=str(database_path))
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert {"users", "domains", "scans", "assets", "alembic_version"} <= table_names
        assert {
            "ix_scans_domain_created_at",
            "uq_scans_single_running_per_domain",
        } <= {index["name"] for index in inspector.get_indexes("scans")}
        assert {
            "ix_assets_scan_record_type",
            "ix_assets_record_type_created_at",
        } <= {index["name"] for index in inspector.get_indexes("assets")}
        assert any(
            (constraint.get("name") or "").endswith("_valid_role")
            for constraint in inspector.get_check_constraints("users")
        )
        assert any(
            (constraint.get("name") or "").endswith("_valid_status")
            for constraint in inspector.get_check_constraints("scans")
        )
        assert any(
            (constraint.get("name") or "").endswith("_valid_status")
            for constraint in inspector.get_check_constraints("domains")
        )
        assert {"created_by", "status"} <= {
            column["name"] for column in inspector.get_columns("domains")
        }
        assert {"full_name"} <= {
            column["name"] for column in inspector.get_columns("users")
        }
        assert {"triggered_by"} <= {
            column["name"] for column in inspector.get_columns("scans")
        }
        with engine.connect() as connection:
            sqlite_indexes = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'index'"
                    )
                )
            }
        assert "uq_domains_name_ci" in sqlite_indexes
        assert "uq_users_email_ci" in sqlite_indexes
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_assignment_migration_backfills_existing_rows(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "existing_schema.db"
    database_url = str(URL.create("sqlite", database=str(database_path)))
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "0005_scan_asset_query_indexes")

    user_id = uuid.uuid4()
    domain_id = uuid.uuid4()
    scan_id = uuid.uuid4()
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, role, is_active) "
                "VALUES (:id, :email, :password, :role, 1)"
            ),
            {
                "id": str(user_id),
                "email": "Existing.User@example.com",
                "password": "legacy-hash",
                "role": "Analyst",
            },
        )
        connection.execute(
            text(
                "INSERT INTO domains (id, user_id, name) "
                "VALUES (:id, :user_id, :name)"
            ),
            {
                "id": str(domain_id),
                "user_id": str(user_id),
                "name": "Legacy.Example",
            },
        )
        connection.execute(
            text(
                "INSERT INTO scans (id, domain_id, status) "
                "VALUES (:id, :domain_id, 'completed')"
            ),
            {"id": str(scan_id), "domain_id": str(domain_id)},
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            user = connection.execute(
                text(
                    "SELECT full_name, email, role FROM users WHERE id = :id"
                ),
                {"id": str(user_id)},
            ).one()
            domain = connection.execute(
                text(
                    "SELECT created_by, name, status FROM domains WHERE id = :id"
                ),
                {"id": str(domain_id)},
            ).one()
            scan = connection.execute(
                text(
                    "SELECT status, triggered_by FROM scans WHERE id = :id"
                ),
                {"id": str(scan_id)},
            ).one()
        assert user == ("existing.user@example.com", "existing.user@example.com", "ANALYST")
        assert domain == (str(user_id), "legacy.example", "COMPLETED")
        assert scan == ("COMPLETED", str(user_id))
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_database_session_closes_after_dependency_finishes(monkeypatch) -> None:
    from app.db import session as session_module

    class FakeSession:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake_session = FakeSession()
    monkeypatch.setattr(
        session_module,
        "get_session_factory",
        lambda: lambda: fake_session,
    )

    dependency = get_db()
    assert next(dependency) is fake_session
    dependency.close()

    assert fake_session.closed is True
