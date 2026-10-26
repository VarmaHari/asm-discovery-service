import logging
from functools import lru_cache
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.authorization import require_role
from app.core.dependencies import get_current_user
from app.core.roles import UserRole
from app.core.security import (
    create_access_token,
    hash_password,
    is_bcrypt_hash,
    validate_new_password,
    verify_password,
)
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class Credentials(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class RegistrationRequest(Credentials):
    full_name: str = Field(min_length=1, max_length=150)
    role: UserRole
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name must not be empty")
        return normalized

    @field_validator("password")
    @classmethod
    def enforce_password_policy(cls, value: SecretStr) -> SecretStr:
        validate_new_password(value.get_secret_value())
        return value


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegistrationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
) -> UserResponse:
    del current_user
    user = User(
        email=str(payload.email).strip().lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password.get_secret_value()),
        role=payload.role.value,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if _is_duplicate_email_error(error):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists",
            ) from error
        logger.error(
            "Unexpected integrity error during registration (%s)",
            type(error.orig).__name__,
        )
        raise
    db.refresh(user)
    logger.info(
        "User registration completed",
        extra={
            "event": "auth.registration.success",
            "user_id": str(user.id),
            "role": user.role,
        },
    )
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: Credentials,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    password = payload.password.get_secret_value()
    if user is None:
        # Keep the password-hash work similar for known and unknown accounts.
        verify_password(password, _dummy_password_hash())
    if user is None or not verify_password(password, user.hashed_password):
        logger.warning(
            "Authentication rejected",
            extra={"event": "auth.login.failure", "outcome": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.warning(
            "Authentication rejected",
            extra={"event": "auth.login.failure", "outcome": "inactive_account"},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive account",
        )
    if not is_bcrypt_hash(user.hashed_password):
        user.hashed_password = hash_password(password)
        db.commit()
    logger.info(
        "Authentication succeeded",
        extra={
            "event": "auth.login.success",
            "user_id": str(user.id),
            "role": user.role,
        },
    )
    token = create_access_token(user.id, settings)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserResponse)
def read_current_user(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


def _dummy_password_hash() -> str:
    return _cached_dummy_password_hash()


@lru_cache(maxsize=1)
def _cached_dummy_password_hash() -> str:
    return hash_password("nonexistent-account-password")


def _is_duplicate_email_error(error: IntegrityError) -> bool:
    constraint_name = getattr(
        getattr(error.orig, "diag", None),
        "constraint_name",
        None,
    )
    if constraint_name in {"uq_users_email", "uq_users_email_ci"}:
        return True
    message = str(error.orig)
    return (
        "UNIQUE constraint failed: users.email" in message
        or "uq_users_email_ci" in message
    )