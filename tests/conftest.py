import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.services.domains import PendingScanScheduler, get_initial_scan_scheduler


class InMemoryQueuedScheduler(PendingScanScheduler):
    def enqueue_scan(self, scan_id: uuid.UUID) -> bool:
        del scan_id
        return True


@pytest.fixture
def test_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record) -> None:
        del _record
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(test_engine: Engine) -> Iterator[TestClient]:
    settings = Settings(
        jwt_secret_key=SecretStr("test-only-signing-key-32-bytes-long")
    )

    def override_get_db():
        with Session(test_engine) as session:
            yield session

    application = create_app(settings)
    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_initial_scan_scheduler] = (
        InMemoryQueuedScheduler
    )
    application.state.test_engine = test_engine

    with TestClient(application) as test_client:
        yield test_client
