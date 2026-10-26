from datetime import datetime, timedelta, timezone
import uuid
from typing import cast

import jwt
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.models import User

PASSWORD = "Correct-Horse-2026!"
TEST_SECRET = "test-only-signing-key-32-bytes-long"


def _application(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


def _test_engine(client: TestClient) -> Engine:
    return cast(Engine, getattr(_application(client).state, "test_engine"))


def _admin_token(client: TestClient) -> str:
    with Session(_test_engine(client)) as session:
        admin = session.scalar(
            select(User).where(User.email == "admin@example.com")
        )
        if admin is None:
            admin = User(
                email="admin@example.com",
                full_name="Test Admin",
                hashed_password="unused-test-hash",
                role=UserRole.ADMIN.value,
            )
            session.add(admin)
            session.commit()
            session.refresh(admin)
        return create_access_token(
            admin.id,
            Settings(jwt_secret_key=SecretStr(TEST_SECRET)),
        )


def register(
    client: TestClient,
    *,
    email: str = "user@example.com",
    full_name: str = "Test User",
    role: UserRole = UserRole.ANALYST,
    password: str = PASSWORD,
) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "full_name": full_name,
            "role": role.value,
            "password": password,
        },
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_registration_and_login_protect_current_user_endpoint(
    client: TestClient,
) -> None:
    registered = register(client)
    assert registered["role"] == UserRole.ANALYST.value
    assert registered["full_name"] == "Test User"
    assert registered["is_active"] is True
    assert "hashed_password" not in registered

    with Session(_test_engine(client)) as session:
        user = session.scalar(select(User).where(User.email == "user@example.com"))
        assert user is not None
        assert user.hashed_password.startswith("$2")

    token_response = client.post(
        "/auth/login",
        json={"email": "USER@example.com", "password": PASSWORD},
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert token_data["token_type"] == "bearer"
    assert token_data["expires_in"] == 1800

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == registered["id"]
    assert response.json()["email"] == "user@example.com"


def test_registration_is_admin_only_and_role_is_validated(client: TestClient) -> None:
    payload = {
        "email": "created@example.com",
        "full_name": "Created User",
        "role": UserRole.VIEWER.value,
        "password": PASSWORD,
    }
    assert client.post("/auth/register", json=payload).status_code == 401

    analyst = register(client)
    token = client.post(
        "/auth/login",
        json={"email": analyst["email"], "password": PASSWORD},
    ).json()["access_token"]
    forbidden = client.post(
        "/auth/register",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403

    created_admin = register(
        client,
        email="created-admin@example.com",
        role=UserRole.ADMIN,
    )
    assert created_admin["role"] == UserRole.ADMIN.value
    assert register(client, email="viewer@example.com", role=UserRole.VIEWER)[
        "role"
    ] == UserRole.VIEWER.value


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "full_name": "Invalid", "role": "ANALYST", "password": PASSWORD},
        {"email": "short@example.com", "full_name": "Short", "role": "ANALYST", "password": "short"},
        {"email": "weak@example.com", "full_name": "Weak", "role": "ANALYST", "password": "alllowercase123!"},
        {"email": "missing-role@example.com", "full_name": "Missing role", "password": PASSWORD},
        {"email": "missing-name@example.com", "role": "ANALYST", "password": PASSWORD},
        {"email": "unknown-role@example.com", "full_name": "Unknown role", "role": "OWNER", "password": PASSWORD},
    ],
)
def test_registration_rejects_invalid_payloads(
    client: TestClient, payload: dict
) -> None:
    response = client.post(
        "/auth/register",
        json=payload,
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )
    assert response.status_code == 422
    if payload.get("password"):
        assert f'"{payload["password"]}"' not in response.text


def test_duplicate_registration_is_conflict_even_with_email_case_change(
    client: TestClient,
) -> None:
    register(client)
    response = client.post(
        "/auth/register",
        json={
            "email": "USER@example.com",
            "full_name": "Another User",
            "role": UserRole.VIEWER.value,
            "password": "Another-Strong-2026!",
        },
        headers={"Authorization": f"Bearer {_admin_token(client)}"},
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "email,password",
    [
        ("user@example.com", "Incorrect-Horse-2026!"),
        ("missing@example.com", PASSWORD),
    ],
)
def test_login_rejects_wrong_or_nonexistent_credentials(
    client: TestClient, email: str, password: str
) -> None:
    register(client)
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_token_is_rejected(client: TestClient) -> None:
    registered = register(client)
    expired_token = create_access_token(
        uuid.UUID(registered["id"]),
        Settings(jwt_secret_key=SecretStr(TEST_SECRET)),
        expires_delta=timedelta(seconds=-1),
    )
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c"])
def test_malformed_token_is_rejected(client: TestClient, token: str) -> None:
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_inactive_account_cannot_login_or_access_protected_endpoint(
    client: TestClient,
) -> None:
    registered = register(client)
    with Session(_test_engine(client)) as session:
        user = session.scalar(select(User).where(User.email == registered["email"]))
        assert user is not None
        user.is_active = False
        session.commit()

    login_response = client.post(
        "/auth/login",
        json={"email": registered["email"], "password": PASSWORD},
    )
    assert login_response.status_code == 403

    token = create_access_token(
        uuid.UUID(registered["id"]),
        Settings(jwt_secret_key=SecretStr(TEST_SECRET)),
    )
    protected_response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert protected_response.status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {
            "sub": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iss": "untrusted-issuer",
            "token_type": "access",
        },
        {
            "sub": "not-a-uuid",
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iss": "asm-discovery-service",
            "token_type": "access",
        },
        {
            "sub": str(uuid.uuid4()),
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "iss": "asm-discovery-service",
            "token_type": "refresh",
        },
    ],
)
def test_invalid_jwt_claims_are_rejected(client: TestClient, claims: dict) -> None:
    token = jwt.encode(claims, TEST_SECRET, algorithm="HS256")
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_valid_token_for_nonexistent_user_is_rejected(client: TestClient) -> None:
    token = create_access_token(
        uuid.uuid4(),
        Settings(jwt_secret_key=SecretStr(TEST_SECRET)),
    )
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_legacy_argon2_hash_is_upgraded_to_bcrypt_after_login(
    client: TestClient,
) -> None:
    password = PASSWORD
    legacy_hash = PasswordHasher().hash(password)
    with Session(_test_engine(client)) as session:
        session.add(
            User(
                email="legacy@example.com",
                full_name="Legacy User",
                hashed_password=legacy_hash,
                role=UserRole.VIEWER.value,
            )
        )
        session.commit()

    response = client.post(
        "/auth/login",
        json={"email": "legacy@example.com", "password": password},
    )
    assert response.status_code == 200
    with Session(_test_engine(client)) as session:
        user = session.scalar(
            select(User).where(User.email == "legacy@example.com")
        )
        assert user is not None
        assert user.hashed_password.startswith("$2")


def test_registration_reports_unexpected_integrity_error_as_server_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _admin_token(client)

    def fail_commit(_self) -> None:
        del _self
        raise IntegrityError(
            "INSERT INTO users",
            {},
            RuntimeError("unrelated database constraint"),
        )

    monkeypatch.setattr(Session, "commit", fail_commit)
    test_client = TestClient(_application(client), raise_server_exceptions=False)
    response = test_client.post(
        "/auth/register",
        json={
            "email": "database-error@example.com",
            "full_name": "Database Error",
            "role": UserRole.ANALYST.value,
            "password": PASSWORD,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 500
    assert "unrelated database constraint" not in response.text


def test_missing_jwt_secret_is_not_misreported_as_invalid_credentials(
    client: TestClient,
) -> None:
    from app.core.config import get_settings

    application = _application(client)
    application.dependency_overrides[get_settings] = lambda: Settings()
    test_client = TestClient(application, raise_server_exceptions=False)
    response = test_client.get(
        "/auth/me",
        headers={"Authorization": "Bearer valid-looking-token"},
    )
    assert response.status_code == 500


def test_weak_jwt_signing_secrets_are_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(jwt_secret_key=SecretStr("too-short-secret"))
