import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Asset, Domain, Scan


def list_assets(
    db: Session,
    *,
    owner_id: Optional[uuid.UUID],
    domain_id: Optional[uuid.UUID],
    record_type: Optional[str],
    limit: int,
    offset: int,
) -> Tuple[List[Asset], int]:
    statement = (
        select(Asset)
        .options(selectinload(Asset.scan))
        .join(Scan, Asset.scan_id == Scan.id)
        .join(Domain, Scan.domain_id == Domain.id)
    )
    count_statement = (
        select(func.count())
        .select_from(Asset)
        .join(Scan, Asset.scan_id == Scan.id)
        .join(Domain, Scan.domain_id == Domain.id)
    )
    filters = []
    if owner_id is not None:
        filters.append(Domain.created_by == owner_id)
    if domain_id is not None:
        filters.append(Domain.id == domain_id)
    if record_type is not None:
        filters.append(Asset.record_type == record_type)

    total = db.scalar(count_statement.where(*filters)) or 0
    items = list(
        db.scalars(
            statement.where(*filters)
            .order_by(Asset.created_at.desc(), Asset.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total
