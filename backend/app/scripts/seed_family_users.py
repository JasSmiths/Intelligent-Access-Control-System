import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import AsyncSessionLocal
from app.models import User
from app.models.enums import UserRole
from app.services.auth import create_user, generate_temporary_password


async def seed_family_users(password: str | None) -> None:
    accounts = [
        {"username": "steph", "first_name": "Steph", "last_name": "Smith", "email": None},
    ]

    async with AsyncSessionLocal() as session:
        for account in accounts:
            existing = await session.scalar(select(User).where(User.username == account["username"]))
            if existing:
                print(f"exists: {account['username']}")
                continue

            temporary_password = password or generate_temporary_password()
            await create_user(
                session,
                username=account["username"],
                first_name=account["first_name"],
                last_name=account["last_name"],
                email=account["email"],
                password=temporary_password,
                role=UserRole.STANDARD,
                is_active=True,
            )
            print(f"created: {account['username']} temporary_password={temporary_password}")

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed standard family user accounts.")
    parser.add_argument("--password", help="Optional shared temporary password for seeded accounts.")
    args = parser.parse_args()
    asyncio.run(seed_family_users(args.password))


if __name__ == "__main__":
    main()
