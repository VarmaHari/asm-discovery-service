import ipaddress
import re
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


_DOMAIN_LABEL = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class DomainStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def normalize_domain_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Domain name must be a string")

    name = value.strip()
    if name.endswith("."):
        name = name[:-1]
    if not name or any(character in name for character in ("/", "\\", ":", "@")):
        raise ValueError("Enter a fully qualified domain name, not a URL")

    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not domain names")

    try:
        normalized = name.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("Domain name contains invalid international characters") from error

    if len(normalized) > 253:
        raise ValueError("Domain name must be at most 253 characters")
    labels = normalized.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels):
        raise ValueError("Enter a valid fully qualified domain name")
    return normalized


class DomainCreate(BaseModel):
    domain: str = Field(min_length=1, max_length=253)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        return normalize_domain_name(value)


class DomainResponse(BaseModel):
    id: uuid.UUID
    domain: str = Field(validation_alias=AliasChoices("name", "domain"))
    created_by: uuid.UUID
    status: DomainStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InitialScanResponse(BaseModel):
    id: uuid.UUID
    status: str
    scheduled: bool = False

    model_config = ConfigDict(from_attributes=True)


class DomainCreatedResponse(DomainResponse):
    initial_scan: InitialScanResponse


class DomainListResponse(BaseModel):
    items: List[DomainResponse]
    total: int
    page: int
    limit: int


class DomainListQuery(BaseModel):
    search: Optional[str] = Field(default=None, max_length=253)
    status: Optional[DomainStatus] = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("search")
    @classmethod
    def normalize_search(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        query = value.strip().lower()
        return query or None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value
