"""Create a user account or promote an existing user to admin.

Usage:
    python scripts/create_admin.py --email admin@example.com --password "Str0ngPass" --name "Admin"
    python scripts/create_admin.py --email existing@example.com --promote
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.security import hash_password  # noqa: E402
from database.models import User  # noqa: E402
from database.session import dispose_engine, get_session_factory  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    email = args.email.strip().lower()
    session_factory = get_session_factory()
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()

        if user is None:
            if not args.password:
                print("User does not exist; provide --password to create it.")
                return 1
            user = User(
                name=args.name or email.split("@")[0],
                email=email,
                password_hash=hash_password(args.password),
                role="admin",
                is_active=True,
            )
            db.add(user)
            await db.commit()
            print(f"Created admin user {email} (id={user.id}).")
        else:
            user.role = "admin"
            if args.password:
                user.password_hash = hash_password(args.password)
                print(f"Promoted {email} to admin and reset the password.")
            else:
                print(f"Promoted existing user {email} to admin.")
            await db.commit()
    await dispose_engine()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create or promote an admin user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=None)
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--promote", action="store_true", help="promote an existing account to admin"
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
