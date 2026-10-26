import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Domain
from app.schemas.domains import DomainStatus


def get_domain(
    db: Session,
    domain_id: uuid.UUID,
    *,
    owner_id: Optional[uuid.UUID] = None,
) -> Optional[Domain]:
    statement = select(Domain).where(Domain.id == domain_id)
    if owner_id is not None:
        statement = statement.where(Domain.created_by == owner_id)
    return db.scalar(statement)


def list_domains(
    db: Session,
    *,
    owner_id: Optional[uuid.UUID],
    query: Optional[str],
    status: Optional[DomainStatus],
    limit: int,
    offset: int,
) -> Tuple[List[Domain], int]:
    filters = []
    if owner_id is not None:
        filters.append(Domain.created_by == owner_id)
    if status is not None:
        filters.append(Domain.status == status.value)
    if query:
        escaped_query = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        filters.append(
            func.lower(Domain.name).ilike(f"%{escaped_query}%", escape="\\")
        )

    total = db.scalar(
        select(func.count()).select_from(Domain).where(*filters)
    ) or 0
    items = list(
        db.scalars(
            select(Domain)
            .where(*filters)
            .order_by(Domain.created_at.desc(), Domain.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total
