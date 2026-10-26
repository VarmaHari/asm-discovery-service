from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.authorization import Permission, require_permission
from app.db.session import get_db
from app.models import User
from app.schemas.assets import AssetListQuery, AssetListResponse, AssetResponse
from app.services.assets import list_assets

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=AssetListResponse)
def list_assets_route(
    domain_id: Optional[uuid.UUID] = Query(default=None),
    record_type: Optional[str] = Query(default=None, min_length=1, max_length=16),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.ASSET_READ)),
) -> AssetListResponse:
    try:
        query = AssetListQuery(
            domain_id=domain_id,
            record_type=record_type,
            page=page,
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    assets, total = list_assets(
        db,
        current_user,
        domain_id=query.domain_id,
        record_type=query.record_type,
        limit=query.limit,
        offset=(query.page - 1) * query.limit,
    )
    items = [
        AssetResponse(
            id=asset.id,
            scan_id=asset.scan_id,
            domain_id=asset.scan.domain_id,
            record_type=asset.record_type,
            value=asset.value,
            created_at=asset.created_at,
        )
        for asset in assets
    ]
    return AssetListResponse(
        items=items,
        total=total,
        page=query.page,
        limit=query.limit,
    )