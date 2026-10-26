import logging
import uuid
from typing import List, Optional, Protocol, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.roles import UserRole
from app.models import Domain, Scan, User
from app.repositories import domains as domain_repository
from app.schemas.domains import DomainStatus, normalize_domain_name

logger = logging.getLogger(__name__)


class DomainNotFoundError(Exception):
    pass


class DuplicateDomainError(Exception):
    pass


class ScanQueueUnavailableError(Exception):
    pass


class InitialScanScheduler(Protocol):
    """Create a durable scan record and submit its worker job."""

    def create_pending_scan(self, db: Session, domain: Domain) -> Scan:
        """Create a durable pending scan record without claiming it ran."""
        ...

    def enqueue_scan(self, scan_id: uuid.UUID) -> bool:
        """Queue the persisted scan; return whether a worker job was accepted."""
        ...


class PendingScanScheduler:
    """Test/local no-broker implementation that leaves scans pending."""

    def create_pending_scan(self, db: Session, domain: Domain) -> Scan:
        scan = Scan(
            domain=domain,
            status="PENDING",
            triggered_by=domain.created_by,
        )
        db.add(scan)
        return scan

    def enqueue_scan(self, scan_id: uuid.UUID) -> bool:
        del scan_id
        return False


def get_initial_scan_scheduler() -> InitialScanScheduler:
    from app.workers.scheduler import CeleryScanScheduler

    return CeleryScanScheduler()


def _owner_scope(user: User) -> Optional[uuid.UUID]:
    if user.role == UserRole.ADMIN.value:
        return None
    return user.id


def create_domain(
    db: Session,
    user: User,
    name: str,
    scheduler: InitialScanScheduler,
) -> Tuple[Domain, Scan, bool]:
    normalized_name = normalize_domain_name(name)
    domain = Domain(
        name=normalized_name,
        created_by=user.id,
        status=DomainStatus.PENDING.value,
    )
    db.add(domain)
    scan = scheduler.create_pending_scan(db, domain)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if _is_duplicate_domain_error(error):
            raise DuplicateDomainError(normalized_name) from error
        raise
    db.refresh(domain)
    db.refresh(scan)
    try:
        scheduled = scheduler.enqueue_scan(scan.id)
    except Exception as error:
        logger.error(
            "Could not enqueue scan %s (%s)",
            scan.id,
            type(error).__name__,
        )
        raise ScanQueueUnavailableError(
            "Could not enqueue initial scan"
        ) from error
    if not scheduled:
        raise ScanQueueUnavailableError("Initial scan worker is unavailable")
    return domain, scan, scheduled


def _is_duplicate_domain_error(error: IntegrityError) -> bool:
    constraint_name = getattr(
        getattr(error.orig, "diag", None),
        "constraint_name",
        None,
    )
    if constraint_name == "uq_domains_name_ci":
        return True
    message = str(error.orig)
    return (
        "uq_domains_name_ci" in message
        or "UNIQUE constraint failed: domains.name" in message
    )


def get_domain_for_user(
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
        raise DomainNotFoundError(str(domain_id))
    return domain


def list_domains_for_user(
    db: Session,
    user: User,
    *,
    query: Optional[str],
    status: Optional[DomainStatus],
    limit: int,
    offset: int,
) -> Tuple[List[Domain], int]:
    return domain_repository.list_domains(
        db,
        owner_id=_owner_scope(user),
        query=query,
        status=status,
        limit=limit,
        offset=offset,
    )


def delete_domain_for_user(
    db: Session,
    domain_id: uuid.UUID,
    user: User,
) -> None:
    domain = get_domain_for_user(db, domain_id, user)
    db.delete(domain)
    db.commit()
