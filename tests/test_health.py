import logging

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app


def test_settings_are_loaded_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_NAME", "Environment ASM Service")
    monkeypatch.setenv("APP_VERSION", "2.0.0")
    monkeypatch.setenv("APP_LOG_LEVEL", "WARNING")

    settings = Settings()

    assert settings.app_name == "Environment ASM Service"
    assert settings.app_version == "2.0.0"
    assert settings.log_level == "WARNING"


def test_health_endpoint_returns_service_status() -> None:
    application = create_app(Settings())
    engine = create_engine("sqlite://")

    def override_get_db():
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(application) as client:
            response = client.get("/health")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "connected",
    }


def test_health_endpoint_reports_database_unavailable() -> None:
    application = create_app(Settings())

    def unavailable_db():
        class BrokenSession:
            def execute(self, _statement):
                del _statement
                raise OperationalError(
                    "SELECT 1",
                    {},
                    RuntimeError("connection refused"),
                )

            def close(self) -> None:
                pass

        yield BrokenSession()

    application.dependency_overrides[get_db] = unavailable_db

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_application_lifespan_logs_startup_and_shutdown(caplog) -> None:
    application = create_app(
        Settings(app_name="Test ASM Service", log_level="INFO")
    )

    with caplog.at_level(logging.INFO):
        with TestClient(application):
            pass

    assert "Application startup complete: Test ASM Service" in caplog.text
    assert "Application shutdown complete: Test ASM Service" in caplog.text
