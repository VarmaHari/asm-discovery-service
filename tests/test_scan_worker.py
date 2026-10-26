import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Tuple

import dns.exception
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import Asset, Domain, Scan, User
from app.services.dns_discovery import DNSAnswer, DNS_RECORD_TYPES
from app.workers.celery_app import celery_app
from app.workers.scheduler import CeleryScanScheduler
from app.workers.tasks import (
    execute_dns_scan,
    enqueue_pending_scans,
    fail_stale_running_scans,
)


class FakeAnswer:
    def __init__(self, values: List[object]) -> None:
        self._values: List[object] = values

    @property
    def rrset(self) -> object:
        return self._values

    def __iter__(self) -> Iterator[object]:
        return iter(self._values)


class FakeMX:
    def __init__(self, preference: int, exchange: str) -> None:
        self.preference = preference
        self.exchange = exchange

    def __str__(self) -> str:
        return f"{self.preference} {self.exchange}"


class FakeResolver:
    def __init__(self, results: Dict[str, object]) -> None:
        self.results = results
        self.calls: List[Tuple[str, str]] = []

    def resolve(
        self,
        qname: str,
        rdtype: str,
        *,
        lifetime: float,
        raise_on_no_answer: bool = True,
    ) -> DNSAnswer:
        del lifetime, raise_on_no_answer
        self.calls.append((qname, rdtype))
        result = self.results.get(rdtype, FakeAnswer([]))
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, FakeAnswer)
        assert isinstance(result, FakeAnswer)
        return result


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        del _record
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def _pending_scan(session_factory) -> uuid.UUID:
    with session_factory() as session:
        user = User(
            email=f"{uuid.uuid4().hex}@example.com",
            full_name="Worker Test User",
            hashed_password="hash",
            role="ANALYST",
        )
        domain = Domain(
            name=f"{uuid.uuid4().hex}.example.com",
            owner=user,
        )
        scan = Scan(domain=domain, status="PENDING")
        session.add(scan)
        session.commit()
        scan_id = scan.id
    return scan_id


def test_worker_completes_and_persists_all_supported_asset_types() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver(
        {
            "A": FakeAnswer(["192.0.2.10"]),
            "AAAA": FakeAnswer(["2001:db8::10"]),
            "NS": FakeAnswer(["ns1.example.com."]),
            "MX": FakeAnswer([FakeMX(5, "mail.example.com.")]),
        }
    )

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        domain = session.get(Domain, scan.domain_id)
        assert domain is not None
        domain_status = domain.status
        assets = list(
            session.scalars(select(Asset).where(Asset.scan_id == scan_id))
        )
    assert result == "completed"
    assert scan is not None
    assert scan.status == "COMPLETED"
    assert scan.started_at is not None
    assert scan.completed_at is not None
    assert scan.error_message is None
    assert domain_status == "COMPLETED"
    assert {asset.record_type for asset in assets} == set(DNS_RECORD_TYPES)
    engine.dispose()


def test_duplicate_delivery_does_not_repeat_completed_scan() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver({"A": FakeAnswer(["192.0.2.10"])})

    first = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )
    calls_after_first_delivery = len(resolver.calls)
    second = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        assets = list(session.scalars(select(Asset).where(Asset.scan_id == scan_id)))
    assert first == "completed"
    assert second == "duplicate"
    assert len(resolver.calls) == calls_after_first_delivery
    assert len(assets) == 1
    engine.dispose()


def test_dns_timeout_fails_scan_and_does_not_persist_partial_assets() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver(
        {
            "A": FakeAnswer(["192.0.2.11"]),
            "AAAA": dns.exception.Timeout("timeout"),
        }
    )

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        domain = session.get(Domain, scan.domain_id)
        assert domain is not None
        domain_status = domain.status
        assets = list(session.scalars(select(Asset).where(Asset.scan_id == scan_id)))
    assert result == "failed"
    assert scan is not None
    assert scan.status == "FAILED"
    assert scan.error_message == "DNS lookup timed out"
    assert scan.completed_at is not None
    assert domain_status == "FAILED"
    assert assets == []
    engine.dispose()


def test_dns_resolution_failure_does_not_mark_scan_successful() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver({"A": dns.exception.DNSException("resolver failure")})

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
    assert result == "failed"
    assert scan is not None
    assert scan.status == "FAILED"
    assert scan.error_message == "DNS resolution error"
    engine.dispose()


def test_asset_database_write_failure_does_not_mark_scan_completed() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)

    def fail_asset_flush(
        session: Session,
        _flush_context,
        _instances,
    ) -> None:
        del _flush_context, _instances
        if any(isinstance(item, Asset) for item in session.new):
            raise RuntimeError("simulated database write failure")

    event.listen(Session, "before_flush", fail_asset_flush)
    try:
        result = execute_dns_scan(
            scan_id,
            session_factory=session_factory,
            resolver=FakeResolver({"A": FakeAnswer(["192.0.2.13"])}),
            timeout_seconds=1,
        )
    finally:
        event.remove(Session, "before_flush", fail_asset_flush)

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assets = list(
            session.scalars(select(Asset).where(Asset.scan_id == scan_id))
        )
    assert result == "failed"
    assert scan is not None
    assert scan.status == "FAILED"
    assert scan.error_message == "Unexpected DNS scan failure"
    assert assets == []
    engine.dispose()


def test_unexpected_lookup_failure_is_recorded_and_not_successful() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver({"A": RuntimeError("unexpected")})

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
    assert result == "failed"
    assert scan is not None
    assert scan.status == "FAILED"
    assert scan.error_message == "Unexpected DNS scan failure"
    engine.dispose()


def test_missing_records_complete_successfully_with_no_assets() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    resolver = FakeResolver({})

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assets = list(session.scalars(select(Asset).where(Asset.scan_id == scan_id)))
    assert result == "completed"
    assert scan is not None
    assert scan.status == "COMPLETED"
    assert assets == []
    engine.dispose()


def test_duplicate_delivery_during_running_scan_is_ignored() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        scan.status = "RUNNING"
        scan.started_at = datetime.now(timezone.utc)
        session.commit()
    resolver = FakeResolver({})

    result = execute_dns_scan(
        scan_id,
        session_factory=session_factory,
        resolver=resolver,
        timeout_seconds=1,
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
    assert result == "duplicate"
    assert resolver.calls == []
    assert scan is not None
    assert scan.status == "RUNNING"
    engine.dispose()


def test_second_scan_for_running_domain_is_failed_not_started() -> None:
    engine, session_factory = _database()
    first_scan_id = _pending_scan(session_factory)
    with Session(engine) as session:
        first_scan = session.get(Scan, first_scan_id)
        assert first_scan is not None
        first_scan.status = "RUNNING"
        first_scan.started_at = datetime.now(timezone.utc)
        first_domain_id = first_scan.domain_id
        second_scan = Scan(domain_id=first_scan.domain_id, status="PENDING")
        session.add(second_scan)
        session.commit()
        second_scan_id = second_scan.id

    result = execute_dns_scan(
        second_scan_id,
        session_factory=session_factory,
        resolver=FakeResolver({}),
        timeout_seconds=1,
    )

    with Session(engine) as session:
        statuses = list(
            session.scalars(
                select(Scan.status).where(
                    Scan.domain_id == first_domain_id
                )
            )
        )
        second_scan = session.get(Scan, second_scan_id)
    assert result == "conflict"
    assert statuses.count("RUNNING") == 1
    assert second_scan is not None
    assert second_scan.status == "FAILED"
    engine.dispose()


def test_database_prevents_two_running_scans_for_one_domain() -> None:
    engine, session_factory = _database()
    first_scan_id = _pending_scan(session_factory)
    with Session(engine) as session:
        first_scan = session.get(Scan, first_scan_id)
        assert first_scan is not None
        second_scan = Scan(domain_id=first_scan.domain_id, status="PENDING")
        session.add(second_scan)
        session.commit()
        first_scan.status = "RUNNING"
        session.commit()
        second_scan.status = "RUNNING"
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("Second running scan was accepted")
    engine.dispose()


def test_stale_running_scan_is_marked_failed() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    started = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
        assert scan is not None
        scan.status = "RUNNING"
        scan.started_at = started
        session.commit()

    count = fail_stale_running_scans(
        session_factory,
        max_runtime_seconds=60,
        now=started + timedelta(minutes=5),
    )

    with Session(engine) as session:
        scan = session.get(Scan, scan_id)
    assert count == 1
    assert scan is not None
    assert scan.status == "FAILED"
    assert scan.error_message == "Scan exceeded maximum runtime"
    engine.dispose()


def test_pending_scan_dispatcher_retries_after_queue_recovery() -> None:
    engine, session_factory = _database()
    scan_id = _pending_scan(session_factory)
    enqueued = []

    def enqueue(scan_id: uuid.UUID) -> bool:
        enqueued.append(scan_id)
        return True

    count = enqueue_pending_scans(session_factory, enqueue)

    assert count == 1
    assert enqueued == [scan_id]
    engine.dispose()


def test_pending_scan_dispatcher_logs_queue_errors_and_continues(
    caplog,
) -> None:
    engine, session_factory = _database()
    first_scan = _pending_scan(session_factory)
    second_scan = _pending_scan(session_factory)
    attempts = []

    def enqueue(scan_id: uuid.UUID) -> bool:
        attempts.append(scan_id)
        if scan_id == first_scan:
            raise RuntimeError("broker unavailable")
        return True

    count = enqueue_pending_scans(session_factory, enqueue)

    assert count == 1
    assert set(attempts) == {first_scan, second_scan}
    assert "Could not enqueue pending scan" in caplog.text
    engine.dispose()


def test_celery_scheduler_uses_deterministic_scan_task_id(monkeypatch) -> None:
    import importlib

    importlib.import_module("app.workers.tasks")
    task = celery_app.tasks["app.workers.tasks.run_dns_scan"]
    captured = {}

    def fake_apply_async(*, args, task_id):
        captured["args"] = args
        captured["task_id"] = task_id
        return type("Result", (), {"id": task_id})()

    monkeypatch.setattr(task, "apply_async", fake_apply_async)
    scan_id = uuid.uuid4()

    accepted = CeleryScanScheduler().enqueue_scan(scan_id)

    assert accepted is True
    assert captured == {
        "args": [str(scan_id)],
        "task_id": f"dns-scan-{scan_id}",
    }
