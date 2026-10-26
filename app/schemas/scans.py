import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ScanResponse(BaseModel):
    id: uuid.UUID
    domain_id: uuid.UUID
    status: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    error_message: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class ScanAcceptedResponse(BaseModel):
    scan_id: uuid.UUID
    status: str


class ScanHistoryResponse(BaseModel):
    items: List[ScanResponse]
    total: int
    page: int
    limit: int


class ScanPagination(BaseModel):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
