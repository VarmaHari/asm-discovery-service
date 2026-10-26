import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.authorization import Permission, require_permission
from app.db.session import get_db
from app.models import User
from app.schemas.scans import (
    ScanAcceptedResponse,
    ScanHistoryResponse,
    ScanPagination,
    ScanResponse,
)
from app.services.domains import (
    InitialScanScheduler,
    ScanQueueUnavailableError,
    get_initial_scan_scheduler,
)
from app.services.scans import (
    ScanAlreadyRunningError,
    ScanDomainNotFoundError,
    list_scan_history,
    trigger_scan,
)

router = APIRouter(tags=["scans"])


@router.post(
    "/domains/{domain_id}/scan",
    response_model=ScanAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_scan_route(
    domain_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission(Permission.SCAN_CREATE)
    ),
    scheduler: InitialScanScheduler = Depends(get_initial_scan_scheduler),
) -> ScanAcceptedResponse:
    try:
        scan, _ = trigger_scan(
            db,
            current_user,
            domain_id,
            scheduler,
        )
    except ScanDomainNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        ) from error
    except ScanAlreadyRunningError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A scan is already running for this domain",
        ) from error
    except ScanQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scan was saved but could not be queued",
        ) from error
    return ScanAcceptedResponse(scan_id=scan.id, status=scan.status)


@router.get("/domains/{domain_id}/scans", response_model=ScanHistoryResponse)
def scan_history_route(
    domain_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.SCAN_READ)),
) -> ScanHistoryResponse:
    pagination = ScanPagination(page=page, limit=limit)
    try:
        scans, total = list_scan_history(
            db,
            current_user,
            domain_id,
            limit=pagination.limit,
            offset=(pagination.page - 1) * pagination.limit,
        )
    except ScanDomainNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        ) from error
    return ScanHistoryResponse(
        items=[ScanResponse.model_validate(scan) for scan in scans],
        total=total,
        page=pagination.page,
        limit=pagination.limit,
    )