import uuid
from typing import cast

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.authorization import (
    Permission,
    ROLE_PERMISSIONS,
    require_permission,
    require_role,
)
from app.core.config import Settings
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.models import User
from pydantic import SecretStr


EXPECTED_PERMISSIONS = {
    UserRole.ADMIN: frozenset(Permission),
    UserRole.ANALYST: frozenset(
        {
            Permission.DOMAIN_READ,
            Permission.DOMAIN_CREATE,
            Permission.SCAN_READ,
            Permission.SCAN_CREATE,
            Permission.ASSET_READ,
        }
    ),
    UserRole.VIEWER: frozenset(
        {
            Permission.DOMAIN_READ,
            Permission.SCAN_READ,
            Permission.ASSET_READ,
        }
    ),
}


def _permission_endpoint(permission: Permission):
    def endpoint(
        current_user: User = Depends(require_permission(permission)),
    ) -> dict[str, str]:
        return {"role": current_user.role, "permission": permission.value}

    return endpoint


def _test_engine(client: TestClient) -> Engine:
    application = cast(FastAPI, client.app)
    return cast(Engine, getattr(application.state, "test_engine"))


@pytest.fixture
def role_tokens(client: TestClient) -> dict[UserRole, str]:
    engine = _test_engine(client)
    settings = Settings(
        jwt_secret_key=SecretStr("test-only-signing-key-32-bytes-long")
    )
    tokens = {}
    with Session(engine) as session:
        for role in UserRole:
            user = User(
                id=uuid.uuid4(),
                email=f"{role.value.lower()}@example.com",
                full_name=f"Test {role.value}",
                hashed_password="test-hash",
                role=role.value,
            )
            session.add(user)
            session.flush()
            tokens[role] = create_access_token(user.id, settings)
        session.commit()
    return tokens


def test_documented_matrix_covers_every_permission() -> None:
    assert ROLE_PERMISSIONS == EXPECTED_PERMISSIONS
    assert set().union(*ROLE_PERMISSIONS.values()) == set(Permission)


def test_each_role_is_checked_against_every_restricted_operation(
    client: TestClient,
    role_tokens: dict[UserRole, str],
) -> None:
    for permission in Permission:
        path = f"/authorization-tests/{permission.value.replace(':', '-')}"
        cast(FastAPI, client.app).add_api_route(
            path,
            _permission_endpoint(permission),
            methods=["GET"],
        )

        unauthenticated = client.get(path)
        assert unauthenticated.status_code == 401, permission.value

        for role in UserRole:
            response = client.get(
                path,
                headers={"Authorization": f"Bearer {role_tokens[role]}"},
            )
            expected_status = (
                200
                if permission in EXPECTED_PERMISSIONS[role]
                else 403
            )
            assert response.status_code == expected_status, (
                f"{role.value} against {permission.value}"
            )


def test_require_role_returns_401_then_403_for_non_admins(
    client: TestClient,
    role_tokens: dict[UserRole, str],
) -> None:
    def admin_endpoint(
        current_user: User = Depends(require_role(UserRole.ADMIN)),
    ) -> dict[str, str]:
        return {"role": current_user.role}

    cast(FastAPI, client.app).add_api_route(
        "/authorization-tests/admin-role",
        admin_endpoint,
        methods=["GET"],
    )

    assert client.get("/authorization-tests/admin-role").status_code == 401
    for role, token in role_tokens.items():
        response = client.get(
            "/authorization-tests/admin-role",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == (200 if role is UserRole.ADMIN else 403)
