from enum import Enum
from typing import Callable, FrozenSet

from fastapi import Depends, HTTPException, status

from app.core.dependencies import get_current_user
from app.models import User
from app.core.roles import UserRole


class Permission(str, Enum):
    DOMAIN_READ = "domain:read"
    DOMAIN_CREATE = "domain:create"
    DOMAIN_UPDATE = "domain:update"
    DOMAIN_DELETE = "domain:delete"
    SCAN_READ = "scan:read"
    SCAN_CREATE = "scan:create"
    SCAN_UPDATE = "scan:update"
    SCAN_DELETE = "scan:delete"
    ASSET_READ = "asset:read"
    ASSET_CREATE = "asset:create"
    ASSET_UPDATE = "asset:update"
    ASSET_DELETE = "asset:delete"
    USER_ADMIN = "user:admin"


ALL_PERMISSIONS = frozenset(Permission)
READ_PERMISSIONS = frozenset(
    {
        Permission.DOMAIN_READ,
        Permission.SCAN_READ,
        Permission.ASSET_READ,
    }
)
ANALYST_PERMISSIONS = READ_PERMISSIONS | frozenset(
    {
        Permission.DOMAIN_CREATE,
        Permission.SCAN_CREATE,
    }
)

ROLE_PERMISSIONS: dict[UserRole, FrozenSet[Permission]] = {
    UserRole.ADMIN: ALL_PERMISSIONS,
    UserRole.ANALYST: ANALYST_PERMISSIONS,
    UserRole.VIEWER: READ_PERMISSIONS,
}


def require_permission(permission: Permission) -> Callable[..., User]:
    def permission_dependency(
        current_user: User = Depends(get_current_user),
    ) -> User:
        try:
            role = UserRole(current_user.role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role has no authorization policy",
            )

        if permission not in ROLE_PERMISSIONS[role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return permission_dependency


def require_role(*roles: UserRole) -> Callable[..., User]:
    permitted = frozenset(roles)

    def role_dependency(
        current_user: User = Depends(get_current_user),
    ) -> User:
        try:
            role = UserRole(current_user.role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role has no authorization policy",
            )

        if role not in permitted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_dependency
