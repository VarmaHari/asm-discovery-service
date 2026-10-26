from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

import jwt
import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from app.core.config import Settings, get_settings

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "asm-discovery-service"
ACCESS_TOKEN_TYPE = "access"

legacy_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(rounds=12),
    ).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                password_hash.encode("ascii"),
            )
        except (ValueError, TypeError):
            return False
    try:
        return legacy_password_hasher.verify(password_hash, password)
    except VerificationError:
        return False


def is_bcrypt_hash(password_hash: str) -> bool:
    return password_hash.startswith(("$2a$", "$2b$", "$2y$"))


def validate_new_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    if not any(character.islower() for character in password):
        raise ValueError("Password must include a lowercase letter")
    if not any(character.isupper() for character in password):
        raise ValueError("Password must include an uppercase letter")
    if not any(character.isdigit() for character in password):
        raise ValueError("Password must include a number")
    if not any(not character.isalnum() for character in password):
        raise ValueError("Password must include a special character")
    return password


def _jwt_secret(settings: Settings) -> str:
    if settings.jwt_secret_key is None:
        raise RuntimeError(
            "JWT_SECRET_KEY is not configured; set it in the environment or .env."
        )
    return settings.jwt_secret_key.get_secret_value()


def create_access_token(
    subject: uuid.UUID,
    settings: Optional[Settings] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    token_settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=token_settings.jwt_access_token_expire_minutes)
    )
    claims = {
        "sub": str(subject),
        "iat": now,
        "exp": expires_at,
        "iss": JWT_ISSUER,
        "token_type": ACCESS_TOKEN_TYPE,
    }
    return jwt.encode(claims, _jwt_secret(token_settings), algorithm=JWT_ALGORITHM)


def decode_access_token(
    token: str,
    settings: Optional[Settings] = None,
) -> uuid.UUID:
    token_settings = settings or get_settings()
    claims = jwt.decode(
        token,
        _jwt_secret(token_settings),
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        options={"require": ["sub", "iat", "exp", "iss", "token_type"]},
    )
    if claims.get("token_type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Invalid token type")
    try:
        return uuid.UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError) as error:
        raise jwt.InvalidTokenError("Invalid subject") from error