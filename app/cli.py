import argparse
from getpass import getpass

from pydantic import ValidationError
from pydantic import SecretStr
from sqlalchemy import select

from app.api.auth import RegistrationRequest
from app.core.roles import UserRole
from app.core.security import hash_password
from app.db.session import get_session_factory
from app.models import User


def bootstrap_admin() -> None:
    with get_session_factory()() as db:
        existing_admin = db.scalar(
            select(User.id).where(User.role == UserRole.ADMIN.value).limit(1)
        )
        if existing_admin is not None:
            raise SystemExit("An Admin account already exists.")

        full_name = input("Initial Admin full name: ").strip()
        email = input("Initial Admin email: ").strip()
        password = getpass("Initial Admin password: ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")

        try:
            credentials = RegistrationRequest(
                email=email,
                full_name=full_name,
                role=UserRole.ADMIN,
                password=SecretStr(password),
            )
        except ValidationError as error:
            raise SystemExit(
                "Invalid Admin name, email, or password policy."
            ) from error

        admin = User(
            email=str(credentials.email).strip().lower(),
            full_name=credentials.full_name,
            hashed_password=hash_password(password),
            role=UserRole.ADMIN.value,
        )
        db.add(admin)
        db.commit()

    print("Initial Admin account created.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ASM service administration")
    parser.add_argument("command", choices=["bootstrap-admin"])
    args = parser.parse_args()
    if args.command == "bootstrap-admin":
        bootstrap_admin()


if __name__ == "__main__":
    main()
