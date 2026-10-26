import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ASSET_RECORD_TYPES = frozenset({"A", "AAAA", "NS", "MX"})


class AssetResponse(BaseModel):
    id: uuid.UUID
    scan_id: uuid.UUID
    domain_id: uuid.UUID
    record_type: str
    value: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetListResponse(BaseModel):
    items: List[AssetResponse]
    total: int
    page: int
    limit: int


class AssetListQuery(BaseModel):
    domain_id: Optional[uuid.UUID] = None
    record_type: Optional[str] = Field(default=None, min_length=1, max_length=16)
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("record_type")
    @classmethod
    def validate_record_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.upper()
        if normalized not in ASSET_RECORD_TYPES:
            raise ValueError("record_type must be A, AAAA, NS, or MX")
        return normalized
