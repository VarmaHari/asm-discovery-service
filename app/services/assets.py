import uuid
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.roles import UserRole
from app.models import Asset, User
from app.repositories import assets as asset_repository


def list_assets(
    db: Session,
    user: User,
    *,
    domain_id: Optional[uuid.UUID],
    record_type: Optional[str],
    limit: int,
    offset: int,
) -> Tuple[List[Asset], int]:
    normalized_record_type = (
        record_type.upper() if record_type is not None else None
    )
    owner_id = None if user.role == UserRole.ADMIN.value else user.id
    return asset_repository.list_assets(
        db,
        owner_id=owner_id,
        domain_id=domain_id,
        record_type=normalized_record_type,
        limit=limit,
        offset=offset,
    )
