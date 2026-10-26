import logging
import uuid
from typing import List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import UserRole
from app.models import Domain, Scan, User
from app.repositories import domains as domain_repository
from app.repositories import scans as scan_repository
from app.services.domains import (
    InitialScanScheduler,
    ScanQueueUnavailableError,
)

logger = logging.getLogger(__name__)


class ScanDomainNotFoundError(Exception):
    pass


class ScanAlreadyRunningError(Exception):
    pass


def _owner_scope(user: User) -> Optional[uuid.UUID]:
    return None if user.role == UserRole.ADMIN.value else user.id


def _get_domain(
    db: Session,
    domain_id: uuid.UUID,
    user: User,
) -> Domain:
    domain = domain_repository.get_domain(
        db,
        domain_id,
        owner_id=_owner_scope(user),
    )
    if domain is None:
        raise ScanDomainNotFoundError(str(domain_id))
    return domain


def trigger_scan(
    db: Session,
    user: User,
    domain_id: uuid.UUID,
    scheduler: InitialScanScheduler,
) -> Tuple[Scan, bool]:
    domain = _get_domain(db, domain_id, user)
    if scan_repository.get_running_scan(db, domain.id) is not None:
        raise ScanAlreadyRunningError(str(domain.id))

    scan = Scan(
        domain_id=domain.id,
        status="PENDING",
        triggered_by=user.id,
    )
    domain.status = "PENDING"
    db.add(scan)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if scan_repository.get_running_scan(db, domain.id) is not None:
            raise ScanAlreadyRunningError(str(domain.id)) from error
        raise
    db.refresh(scan)
    try:
        scheduled = scheduler.enqueue_scan(scan.id)
    except Exception as error:
        logger.error(
            "Could not enqueue manual scan %s (%s)",
            scan.id,
            type(error).__name__,
        )
        raise ScanQueueUnavailableError(
            "Could not enqueue scan"
        ) from error
    if not scheduled:
        logger.error("Worker unavailable for manual scan %s", scan.id)
        raise ScanQueueUnavailableError("Scan worker is unavailable")
    return scan, scheduled


def list_scan_history(
    db: Session,
    user: User,
    domain_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> Tuple[List[Scan], int]:
    domain = _get_domain(db, domain_id, user)
    return scan_repository.list_scans(
        db,
        domain_id=domain.id,
        limit=limit,
        offset=offset,
    )
