import uuid
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.roles import UserRole
from app.core.security import create_access_token
from app.models import Asset, Domain, Scan, User
from app.services.domains import (
    PendingScanScheduler,
    get_initial_scan_scheduler,
)


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


def add_domain(engine: Engine, owner: User, name: str = "api.example") -> Domain:
    with Session(engine) as session:
        domain = Domain(created_by=owner.id, name=name)
        session.add(domain)
        session.commit()
        session.refresh(domain)
        return domain


def test_manual_scan_is_queued_and_history_includes_worker_states(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.ANALYST)
    domain = add_domain(test_engine, owner)
    with Session(test_engine) as session:
        session.add_all(
            [
                Scan(domain_id=domain.id, status="COMPLETED"),
                Scan(domain_id=domain.id, status="FAILED"),
            ]
        )
        session.commit()

    response = client.post(
        f"/domains/{domain.id}/scan",
        headers=auth_headers(token),
    )

    assert response.status_code == 202, response.text
    scan_result = response.json()
    assert scan_result["status"] == "PENDING"
    assert set(scan_result) == {"scan_id", "status"}

    history = client.get(
        f"/domains/{domain.id}/scans?limit=20&page=1",
        headers=auth_headers(token),
    )
    assert history.status_code == 200
    result = history.json()
    assert result["total"] == 3
    assert result["limit"] == 20
    assert result["page"] == 1
    assert len(result["items"]) == 3
    assert {"PENDING", "COMPLETED", "FAILED"} == {
        item["status"] for item in result["items"]
    }

    page = client.get(
        f"/domains/{domain.id}/scans?limit=1&page=2",
        headers=auth_headers(token),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert page.json()["limit"] == 1
    assert page.json()["page"] == 2
    assert len(page.json()["items"]) == 1


def test_manual_scan_conflicts_when_domain_has_running_scan(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.ANALYST)
    domain = add_domain(test_engine, owner)
    with Session(test_engine) as session:
        session.add(Scan(domain_id=domain.id, status="RUNNING"))
        session.commit()

    response = client.post(
        f"/domains/{domain.id}/scan",
        headers=auth_headers(token),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "A scan is already running for this domain"
    )


def test_manual_scan_returns_503_when_worker_is_unavailable(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.ANALYST)
    domain = add_domain(test_engine, owner)
    application = cast(FastAPI, client.app)
    application.dependency_overrides[get_initial_scan_scheduler] = (
        lambda: PendingScanScheduler()
    )

    response = client.post(
        f"/domains/{domain.id}/scan",
        headers=auth_headers(token),
    )

    assert response.status_code == 503
    with Session(test_engine) as session:
        scans = list(
            session.query(Scan)
            .filter(Scan.domain_id == domain.id)
            .all()
        )
    assert len(scans) == 1
    assert scans[0].status == "PENDING"


def test_missing_and_malformed_domain_ids_are_handled(
    client: TestClient,
    test_engine: Engine,
) -> None:
    _, token = create_role_token(test_engine, UserRole.ANALYST)

    missing_scan = client.post(
        f"/domains/{uuid.uuid4()}/scan",
        headers=auth_headers(token),
    )
    missing_history = client.get(
        f"/domains/{uuid.uuid4()}/scans",
        headers=auth_headers(token),
    )
    malformed = client.get(
        "/domains/not-a-uuid/scans",
        headers=auth_headers(token),
    )

    assert missing_scan.status_code == 404
    assert missing_history.status_code == 404
    assert malformed.status_code == 422


def test_asset_filters_owner_scope_and_pagination(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, token = create_role_token(test_engine, UserRole.VIEWER)
    domain = add_domain(test_engine, owner)
    with Session(test_engine) as session:
        first_scan = Scan(domain_id=domain.id, status="COMPLETED")
        second_scan = Scan(domain_id=domain.id, status="COMPLETED")
        session.add_all([first_scan, second_scan])
        session.flush()
        session.add_all(
            [
                Asset(scan_id=first_scan.id, record_type="A", value="192.0.2.1"),
                Asset(scan_id=first_scan.id, record_type="AAAA", value="2001:db8::1"),
                Asset(scan_id=second_scan.id, record_type="A", value="192.0.2.2"),
            ]
        )
        session.commit()

    filtered = client.get(
        f"/assets?domain_id={domain.id}&record_type=a",
        headers=auth_headers(token),
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 2
    assert {asset["record_type"] for asset in filtered.json()["items"]} == {"A"}
    assert all(
        asset["domain_id"] == str(domain.id)
        for asset in filtered.json()["items"]
    )

    paginated = client.get(
        f"/assets?domain_id={domain.id}&limit=1&page=2",
        headers=auth_headers(token),
    )
    assert paginated.status_code == 200
    assert paginated.json()["total"] == 3
    assert paginated.json()["page"] == 2
    assert len(paginated.json()["items"]) == 1

    invalid_type = client.get(
        "/assets?record_type=TXT",
        headers=auth_headers(token),
    )
    assert invalid_type.status_code == 422


def test_scan_and_asset_route_permissions_distinguish_401_and_403(
    client: TestClient,
    test_engine: Engine,
) -> None:
    users = {
        role: create_role_token(test_engine, role)
        for role in UserRole
    }
    domain_by_role = {
        role: add_domain(test_engine, account[0], f"{role.value.lower()}.example")
        for role, account in users.items()
    }

    for role, (_, token) in users.items():
        domain = domain_by_role[role]
        scan_read = client.get(
            f"/domains/{domain.id}/scans",
            headers=auth_headers(token),
        )
        asset_read = client.get("/assets", headers=auth_headers(token))
        scan_create = client.post(
            f"/domains/{domain.id}/scan",
            headers=auth_headers(token),
        )
        assert scan_read.status_code == 200
        assert asset_read.status_code == 200
        assert scan_create.status_code == (
            403 if role is UserRole.VIEWER else 202
        )

    assert client.get("/assets").status_code == 401
    assert client.get(
        f"/domains/{domain_by_role[UserRole.ANALYST].id}/scans"
    ).status_code == 401


def test_asset_domain_filter_does_not_expose_other_owners_assets(
    client: TestClient,
    test_engine: Engine,
) -> None:
    owner, owner_token = create_role_token(test_engine, UserRole.ANALYST)
    other, other_token = create_role_token(test_engine, UserRole.ANALYST)
    owned_domain = add_domain(test_engine, owner, "owned.example")
    other_domain = add_domain(test_engine, other, "other.example")
    with Session(test_engine) as session:
        scan = Scan(domain_id=other_domain.id, status="COMPLETED")
        session.add(scan)
        session.flush()
        session.add(Asset(scan_id=scan.id, record_type="A", value="192.0.2.4"))
        session.commit()

    response = client.get(
        f"/assets?domain_id={other_domain.id}",
        headers=auth_headers(owner_token),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert owned_domain.id != other_domain.id
    assert client.get(
        f"/assets?domain_id={other_domain.id}",
        headers=auth_headers(other_token),
    ).json()["total"] == 1


@pytest.mark.parametrize("limit", ["0", "101"])
def test_invalid_asset_pagination_is_rejected(
    client: TestClient,
    test_engine: Engine,
    limit: str,
) -> None:
    _, token = create_role_token(test_engine, UserRole.VIEWER)

    response = client.get(
        f"/assets?limit={limit}",
        headers=auth_headers(token),
    )

    assert response.status_code == 422
