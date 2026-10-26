import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.authorization import Permission, require_permission
from app.db.session import get_db
from app.models import Domain, User
from app.schemas.domains import (
    DomainCreate,
    DomainCreatedResponse,
    DomainListQuery,
    DomainListResponse,
    DomainResponse,
    DomainStatus,
    InitialScanResponse,
)
from app.services.domains import (
    DomainNotFoundError,
    DuplicateDomainError,
    ScanQueueUnavailableError,
    InitialScanScheduler,
    create_domain,
    delete_domain_for_user,
    get_domain_for_user,
    get_initial_scan_scheduler,
    list_domains_for_user,
)

router = APIRouter(prefix="/domains", tags=["domains"])


@router.post(
    "",
    response_model=DomainCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_domain_route(
    payload: DomainCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission(Permission.DOMAIN_CREATE)
    ),
    scheduler: InitialScanScheduler = Depends(get_initial_scan_scheduler),
) -> DomainCreatedResponse:
    try:
        domain, scan, scheduled = create_domain(
            db,
            current_user,
            payload.domain,
            scheduler,
        )
    except DuplicateDomainError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This domain is already registered",
        ) from error
    except ScanQueueUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Domain was saved, but its initial scan could not be queued",
        ) from error
    return DomainCreatedResponse(
        id=domain.id,
        domain=domain.name,
        created_by=domain.created_by,
        status=DomainStatus(domain.status),
        created_at=domain.created_at,
        updated_at=domain.updated_at,
        initial_scan=InitialScanResponse(
            id=scan.id,
            status=scan.status,
            scheduled=scheduled,
        ),
    )


@router.get("", response_model=DomainListResponse)
def list_domains_route(
    search: Optional[str] = Query(default=None, max_length=253),
    domain_status: Optional[DomainStatus] = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DOMAIN_READ)),
) -> DomainListResponse:
    query = DomainListQuery(
        search=search,
        status=(
            DomainStatus(domain_status)
            if domain_status is not None
            else None
        ),
        page=page,
        limit=limit,
    )
    domains, total = list_domains_for_user(
        db,
        current_user,
        query=query.search,
        status=query.status,
        limit=query.limit,
        offset=(query.page - 1) * query.limit,
    )
    return DomainListResponse(
        items=[DomainResponse.model_validate(domain) for domain in domains],
        total=total,
        page=query.page,
        limit=query.limit,
    )


@router.get("/{domain_id}", response_model=DomainResponse)
def retrieve_domain_route(
    domain_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.DOMAIN_READ)),
) -> Domain:
    try:
        return get_domain_for_user(db, domain_id, current_user)
    except DomainNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        ) from error


@router.delete(
    "/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_domain_route(
    domain_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_permission(Permission.DOMAIN_DELETE)
    ),
) -> Response:
    try:
        delete_domain_for_user(db, domain_id, current_user)
    except DomainNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)