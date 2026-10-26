from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(
        default="ASM Asset Discovery Service",
        validation_alias="APP_NAME",
    )
    app_version: str = Field(default="1.0.0", validation_alias="APP_VERSION")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="APP_LOG_LEVEL",
    )
    database_url: Optional[SecretStr] = Field(
        default=None,
        validation_alias="DATABASE_URL",
    )
    jwt_secret_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="JWT_SECRET_KEY",
    )
    jwt_access_token_expire_minutes: int = Field(
        default=30,
        gt=0,
        validation_alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="CELERY_BROKER_URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/1",
        validation_alias="CELERY_RESULT_BACKEND",
    )
    dns_lookup_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=30,
        validation_alias="DNS_LOOKUP_TIMEOUT_SECONDS",
    )
    scan_max_runtime_seconds: int = Field(
        default=900,
        gt=0,
        validation_alias="SCAN_MAX_RUNTIME_SECONDS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(
        cls,
        value: Optional[SecretStr],
    ) -> Optional[SecretStr]:
        if value is not None:
            secret = value.get_secret_value()
            if len(secret.encode("utf-8")) < 32:
                raise ValueError("JWT_SECRET_KEY must be at least 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()