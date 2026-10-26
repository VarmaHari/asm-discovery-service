import uuid

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typing import cast

from app.core.config import Settings
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.models import Asset, Domain, Scan, User
from app.services.domains import PendingScanScheduler, get_initial_scan_scheduler


def create_role_token(engine: Engine, role: UserRole) -> tuple[User, str]:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex}@example.com",
        full_name=f"Test {role.value}",
        hashed_password="test-hash",
        role=role.value,
    )
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        token = create_access_token(
            user.id,
            Settings(
                jwt_secret_key=SecretStr(
                    "test-only-signing-key-32-bytes-long"
                )
            ),
        )
    return user, token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_domain(
    client: TestClient,
    token: str,
    name: str,
) -> dict:
    response = client.post(
        "/domains",
        json={"domain": name},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_normalizes_domain_and_persists_pending_scan(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.ANALYST)

    response = client.post(
        "/domains",
        json={"domain": "  ExAmPlE.COM.  "},
        headers=auth_headers(token),
    )

    assert response.status_code == 201
    created = response.json()
    assert created["domain"] == "example.com"
    assert created["created_by"] == str(owner.id)
    assert created["status"] == "PENDING"
    assert created["initial_scan"]["status"] == "PENDING"
    assert created["initial_scan"]["scheduled"] is True

    with Session(test_engine) as session:
        scan = session.get(Scan, uuid.UUID(created["initial_scan"]["id"]))
        assert scan is not None
        assert scan.started_at is None
        assert scan.completed_at is None


def test_unicode_domain_is_normalized_to_idna(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)

    created = create_domain(client, token, "bücher.example")

    assert created["domain"] == "xn--bcher-kva.example"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "localhost",
        "example..com",
        "-bad.example",
        "bad-.example",
        "https://example.com",
        "example.com/path",
        "192.0.2.1",
        "a" * 64 + ".example",
    ],
)
def test_invalid_domain_names_are_rejected(
    client: TestClient,
    test_engine: Engine,
    name: str,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)

    response = client.post(
        "/domains",
        json={"domain": name},
        headers=auth_headers(token),
    )

    assert response.status_code == 422


def test_duplicate_domain_is_rejected_at_database_boundary(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)
    create_domain(client, token, "example.com")

    duplicate = client.post(
        "/domains",
        json={"domain": "EXAMPLE.COM."},
        headers=auth_headers(token),
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This domain is already registered"


def test_domain_duplicate_is_global_across_owners(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, first_token = create_role_token(test_engine, UserRole.ANALYST)
    _, second_token = create_role_token(test_engine, UserRole.ANALYST)
    create_domain(client, first_token, "globally-unique.example")

    duplicate = client.post(
        "/domains",
        json={"domain": "GLOBALLY-UNIQUE.EXAMPLE"},
        headers=auth_headers(second_token),
    )
    assert duplicate.status_code == 409


def test_unavailable_worker_returns_503_and_leaves_scan_pending(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)
    cast(FastAPI, client.app).dependency_overrides[get_initial_scan_scheduler] = (
        lambda: PendingScanScheduler()
    )

    response = client.post(
        "/domains",
        json={"domain": "queue-unavailable.example"},
        headers=auth_headers(token),
    )

    assert response.status_code == 503
    with Session(test_engine) as session:
        scan = session.scalar(
            select(Scan).join(Domain).where(
                Domain.name == "queue-unavailable.example"
            )
        )
    assert scan is not None
    assert scan.status == "PENDING"
    assert scan.started_at is None


def test_missing_domain_ids_return_not_found_or_validation_error(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)
    _, admin_token = create_role_token(test_engine, UserRole.ADMIN)

    missing = client.get(
        f"/domains/{uuid.uuid4()}",
        headers=auth_headers(token),
    )
    malformed = client.get(
        "/domains/not-a-uuid",
        headers=auth_headers(token),
    )
    missing_delete = client.delete(
        f"/domains/{uuid.uuid4()}",
        headers=auth_headers(admin_token),
    )

    assert missing.status_code == 404
    assert malformed.status_code == 422
    assert missing_delete.status_code == 404


def test_list_supports_search_pagination_and_owner_scope(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ADMIN)
    create_domain(client, token, "alpha-example.com")
    create_domain(client, token, "beta-example.com")
    create_domain(client, token, "unrelated.net")

    response = client.get(
        "/domains?search=EXAMPLE&limit=1&page=2",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["limit"] == 1
    assert result["page"] == 2
    assert "example.com" in result["items"][0]["domain"]

    _, analyst_token = create_role_token(test_engine, UserRole.ANALYST)
    analyst_list = client.get("/domains", headers=auth_headers(analyst_token))
    assert analyst_list.status_code == 200
    assert analyst_list.json()["total"] == 0


def test_domain_list_filters_status_and_rejects_invalid_status(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.ADMIN)
    created = create_domain(client, token, "status-filter.example")
    with Session(test_engine) as session:
        domain = session.get(Domain, uuid.UUID(created["id"]))
        assert domain is not None
        domain.status = "COMPLETED"
        session.commit()

    completed = client.get(
        "/domains?status=COMPLETED&page=1&limit=10",
        headers=auth_headers(token),
    )
    assert completed.status_code == 200
    assert completed.json()["total"] == 1
    assert completed.json()["items"][0]["status"] == "COMPLETED"

    pending = client.get(
        "/domains?status=PENDING",
        headers=auth_headers(token),
    )
    assert pending.status_code == 200
    assert pending.json()["total"] == 0

    invalid = client.get(
        "/domains?status=UNKNOWN",
        headers=auth_headers(token),
    )
    assert invalid.status_code == 422
    assert owner.role == UserRole.ADMIN.value


def test_role_permissions_for_domain_operations(
    client: TestClient,
    test_engine: Engine,
) -> None:
    tokens = {
        role: create_role_token(test_engine, role)[1]
        for role in UserRole
    }

    for role, token in tokens.items():
        unauthenticated = client.get("/domains")
        assert unauthenticated.status_code == 401

        list_response = client.get("/domains", headers=auth_headers(token))
        assert list_response.status_code == 200

        create_response = client.post(
            "/domains",
            json={"domain": f"{role.value.lower()}.example"},
            headers=auth_headers(token),
        )
        assert create_response.status_code == (
            403 if role is UserRole.VIEWER else 201
        )
        domain_id = (
            create_response.json()["id"]
            if create_response.status_code == 201
            else None
        )

        if role is UserRole.ADMIN:
            delete_response = client.delete(
                f"/domains/{domain_id}",
                headers=auth_headers(token),
            )
            assert delete_response.status_code == 204
        else:
            existing = client.get(
                "/domains",
                headers=auth_headers(token),
            ).json()["items"]
            if existing:
                denied = client.delete(
                    f"/domains/{existing[0]['id']}",
                    headers=auth_headers(token),
                )
                assert denied.status_code == 403


def test_delete_domain_cascades_scans_and_assets(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ADMIN)
    created = create_domain(client, token, "delete.example")
    scan_id = uuid.UUID(created["initial_scan"]["id"])

    with Session(test_engine) as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        session.add(
            Asset(
                scan=scan,
                record_type="A",
                value="192.0.2.25",
            )
        )
        session.commit()
        asset_id = session.scalar(select(Asset.id))

    response = client.delete(
        f"/domains/{created['id']}",
        headers=auth_headers(token),
    )

    assert response.status_code == 204
    with Session(test_engine) as session:
        assert session.get(Domain, uuid.UUID(created["id"])) is None
        assert session.get(Scan, scan_id) is None
        assert session.get(Asset, asset_id) is None


def test_non_admin_cannot_retrieve_another_users_domain(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, admin_token = create_role_token(test_engine, UserRole.ADMIN)
    created = create_domain(client, admin_token, "private.example")
    _, viewer_token = create_role_token(test_engine, UserRole.VIEWER)

    response = client.get(
        f"/domains/{created['id']}",
        headers=auth_headers(viewer_token),
    )

    assert response.status_code == 404


def test_analyst_and_viewer_cannot_delete_an_existing_domain(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, admin_token = create_role_token(test_engine, UserRole.ADMIN)
    created = create_domain(client, admin_token, "protected.example")

    for role in (UserRole.ANALYST, UserRole.VIEWER):
        _, token = create_role_token(test_engine, role)
        response = client.delete(
            f"/domains/{created['id']}",
            headers=auth_headers(token),
        )
        assert response.status_code == 403
