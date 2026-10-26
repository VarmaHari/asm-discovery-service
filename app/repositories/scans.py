import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Scan


def get_running_scan(
    db: Session,
    domain_id: uuid.UUID,
) -> Optional[Scan]:
    return db.scalar(
        select(Scan)
        .where(Scan.domain_id == domain_id, Scan.status == "RUNNING")
        .limit(1)
    )


def get_scan(
    db: Session,
    scan_id: uuid.UUID,
    *,
    domain_id: Optional[uuid.UUID] = None,
) -> Optional[Scan]:
    statement = select(Scan).where(Scan.id == scan_id)
    if domain_id is not None:
        statement = statement.where(Scan.domain_id == domain_id)
    return db.scalar(statement)


def list_scans(
    db: Session,
    *,
    domain_id: uuid.UUID,
    limit: int,
    offset: int,
) -> Tuple[List[Scan], int]:
    filters = [Scan.domain_id == domain_id]
    total = db.scalar(
        select(func.count()).select_from(Scan).where(*filters)
    ) or 0
    items = list(
        db.scalars(
            select(Scan)
            .where(*filters)
            .order_by(Scan.created_at.desc(), Scan.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total
