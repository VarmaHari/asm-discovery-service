import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.models import Asset, Domain, Scan
from app.services.dns_discovery import (
    DNSLookupTimeout,
    DNSResolutionError,
    DiscoveredRecord,
    Resolver,
    discover_dns_records,
)
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


def _refresh_domain_status(session: Session, domain_id: uuid.UUID) -> None:
    statuses = list(
        session.scalars(
            select(Scan.status)
            .where(Scan.domain_id == domain_id)
            .order_by(Scan.created_at.desc(), Scan.id.desc())
        )
    )
    domain = session.get(Domain, domain_id)
    if domain is None or not statuses:
        return
    if "RUNNING" in statuses:
        domain.status = "RUNNING"
    elif "PENDING" in statuses:
        domain.status = "PENDING"
    else:
        domain.status = statuses[0]


def _claim_scan(
    scan_id: uuid.UUID,
    session_factory: SessionFactory,
) -> tuple[str, Optional[str]]:
    try:
        with session_factory() as session:
            with session.begin():
                scan = session.scalar(
                    select(Scan)
                    .where(Scan.id == scan_id)
                    .with_for_update()
                )
                if scan is None:
                    return "missing", None
                if scan.status != "PENDING":
                    return "duplicate", None

                running_scan = session.scalar(
                    select(Scan.id)
                    .where(
                        Scan.domain_id == scan.domain_id,
                        Scan.status == "RUNNING",
                        Scan.id != scan.id,
                    )
                    .limit(1)
                )
                if running_scan is not None:
                    scan.status = "FAILED"
                    scan.error_message = (
                        "Another scan is already running for this domain"
                    )
                    scan.completed_at = datetime.now(timezone.utc)
                    _refresh_domain_status(session, scan.domain_id)
                    return "conflict", None

                domain = session.scalar(
                    select(Domain).where(Domain.id == scan.domain_id)
                )
                if domain is None:
                    scan.status = "FAILED"
                    scan.error_message = "Scan domain no longer exists"
                    scan.completed_at = datetime.now(timezone.utc)
                    return "failed", None

                scan.status = "RUNNING"
                scan.started_at = datetime.now(timezone.utc)
                scan.completed_at = None
                scan.error_message = None
                _refresh_domain_status(session, domain.id)
                return "claimed", domain.name
    except IntegrityError:
        _fail_pending_scan(
            scan_id,
            session_factory,
            "Another scan is already running for this domain",
        )
        return "conflict", None


def _fail_pending_scan(
    scan_id: uuid.UUID,
    session_factory: SessionFactory,
    reason: str,
) -> None:
    with session_factory() as session:
        with session.begin():
            scan = session.scalar(
                select(Scan)
                .where(Scan.id == scan_id, Scan.status == "PENDING")
                .with_for_update()
            )
            if scan is not None:
                scan.status = "FAILED"
                scan.error_message = reason[:1024]
                scan.completed_at = datetime.now(timezone.utc)
                _refresh_domain_status(session, scan.domain_id)


def _mark_failed(
    scan_id: uuid.UUID,
    session_factory: SessionFactory,
    reason: str,
) -> None:
    with session_factory() as session:
        with session.begin():
            scan = session.scalar(
                select(Scan)
                .where(Scan.id == scan_id, Scan.status == "RUNNING")
                .with_for_update()
            )
            if scan is not None:
                scan.status = "FAILED"
                scan.error_message = reason[:1024]
                scan.completed_at = datetime.now(timezone.utc)
                _refresh_domain_status(session, scan.domain_id)


def _save_success(
    scan_id: uuid.UUID,
    records: list[DiscoveredRecord],
    session_factory: SessionFactory,
) -> str:
    with session_factory() as session:
        with session.begin():
            scan = session.scalar(
                select(Scan)
                .where(Scan.id == scan_id)
                .with_for_update()
            )
            if scan is None:
                return "missing"
            if scan.status != "RUNNING":
                return "duplicate"

            existing = {
                (record_type, value)
                for record_type, value in session.execute(
                    select(Asset.record_type, Asset.value).where(
                        Asset.scan_id == scan_id
                    )
                )
            }
            for record in records:
                key = (record.record_type, record.value)
                if key not in existing:
                    session.add(
                        Asset(
                            scan_id=scan_id,
                            record_type=record.record_type,
                            value=record.value,
                        )
                    )
                    existing.add(key)
            scan.status = "COMPLETED"
            scan.completed_at = datetime.now(timezone.utc)
            scan.error_message = None
            _refresh_domain_status(session, scan.domain_id)
            return "completed"


def execute_dns_scan(
    scan_id: uuid.UUID,
    *,
    session_factory: SessionFactory,
    resolver: Optional[Resolver] = None,
    timeout_seconds: Optional[float] = None,
) -> str:
    claim_status, domain_name = _claim_scan(scan_id, session_factory)
    if claim_status != "claimed" or domain_name is None:
        return claim_status

    logger.info(
        "DNS scan worker started",
        extra={
            "event": "scan.worker.started",
            "scan_id": str(scan_id),
        },
    )
    timeout = (
        get_settings().dns_lookup_timeout_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    try:
        records = discover_dns_records(
            domain_name,
            timeout_seconds=timeout,
            resolver=resolver,
        )
        result = _save_success(scan_id, records, session_factory)
        logger.info(
            "DNS scan worker finished",
            extra={
                "event": "scan.worker.finished",
                "scan_id": str(scan_id),
                "outcome": result,
            },
        )
        return result
    except DNSLookupTimeout:
        _mark_failed(scan_id, session_factory, "DNS lookup timed out")
        logger.warning(
            "DNS scan worker failed",
            extra={
                "event": "scan.worker.failed",
                "scan_id": str(scan_id),
                "outcome": "timeout",
            },
        )
        return "failed"
    except DNSResolutionError:
        _mark_failed(scan_id, session_factory, "DNS resolution error")
        logger.warning(
            "DNS scan worker failed",
            extra={
                "event": "scan.worker.failed",
                "scan_id": str(scan_id),
                "outcome": "dns_error",
            },
        )
        return "failed"
    except Exception:
        logger.exception(
            "Unexpected error while processing DNS scan",
            extra={
                "event": "scan.worker.error",
                "scan_id": str(scan_id),
                "error_type": "unexpected",
            },
        )
        _mark_failed(scan_id, session_factory, "Unexpected DNS scan failure")
        return "failed"


def fail_stale_running_scans(
    session_factory: SessionFactory,
    *,
    max_runtime_seconds: int,
    now: Optional[datetime] = None,
) -> int:
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(seconds=max_runtime_seconds)
    with session_factory() as session:
        with session.begin():
            stale_scans = list(
                session.scalars(
                    select(Scan)
                    .where(
                        Scan.status == "RUNNING",
                        (Scan.started_at.is_(None)) | (Scan.started_at < cutoff),
                    )
                    .with_for_update()
                )
            )
            for scan in stale_scans:
                scan.status = "FAILED"
                scan.error_message = "Scan exceeded maximum runtime"
                scan.completed_at = current_time
                _refresh_domain_status(session, scan.domain_id)
            return len(stale_scans)


def enqueue_pending_scans(
    session_factory: SessionFactory,
    enqueue_scan: Callable[[uuid.UUID], bool],
) -> int:
    with session_factory() as session:
        scan_ids = list(
            session.scalars(
                select(Scan.id)
                .where(Scan.status == "PENDING")
                .order_by(Scan.created_at, Scan.id)
                .limit(100)
            )
        )

    enqueued = 0
    for scan_id in scan_ids:
        try:
            if enqueue_scan(scan_id):
                enqueued += 1
        except Exception as error:
            logger.error(
                "Could not enqueue pending scan %s (%s)",
                scan_id,
                type(error).__name__,
            )
    return enqueued


@celery_app.task(
    name="app.workers.tasks.run_dns_scan",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,
    soft_time_limit=60,
    time_limit=75,
)
def run_dns_scan(scan_id: str) -> str:
    try:
        parsed_id = uuid.UUID(scan_id)
    except ValueError:
        logger.error("Ignoring DNS scan task with invalid scan ID")
        return "invalid"
    try:
        return execute_dns_scan(
            parsed_id,
            session_factory=get_session_factory(),
        )
    except Exception:
        logger.exception("Unhandled failure in DNS scan task %s", parsed_id)
        _mark_failed(
            parsed_id,
            get_session_factory(),
            "Unexpected DNS scan worker failure",
        )
        raise


@celery_app.task(name="app.workers.tasks.fail_stale_scans")
def fail_stale_scans() -> int:
    settings = get_settings()
    return fail_stale_running_scans(
        get_session_factory(),
        max_runtime_seconds=settings.scan_max_runtime_seconds,
    )


@celery_app.task(name="app.workers.tasks.dispatch_pending_scans")
def dispatch_pending_scans() -> int:
    from app.workers.scheduler import CeleryScanScheduler

    return enqueue_pending_scans(
        get_session_factory(),
        CeleryScanScheduler().enqueue_scan,
    )
