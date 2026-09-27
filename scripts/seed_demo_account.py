"""Create or verify the persistent demo account (idempotent seed).

Usage:
    python scripts/seed_demo_account.py
    python scripts/seed_demo_account.py --reset-password

The seed:

1. checks whether the configured demo email (default demo@medilens.ai)
   exists in the database;
2. creates it only when missing — with a securely hashed password and the
   non-admin role "demo";
3. creates the four synthetic demo reports (CBC, lipid, thyroid, metabolic)
   with full analyses when they are missing;
4. never duplicates the account and never overwrites its password, unless
   --reset-password is passed explicitly.

Run this as part of deployment / database setup. A persistent PostgreSQL
database is required for the account to survive deployments (see README).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import get_settings  # noqa: E402
from backend.services.demo_account_service import seed_demo_account  # noqa: E402
from database.session import dispose_engine, get_session_factory  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    session_factory = get_session_factory()
    async with session_factory() as db:
        result = await seed_demo_account(db, reset_password=args.reset_password)
    await dispose_engine()

    if not result["enabled"]:
        print("Demo account is disabled (DEMO_ACCOUNT_ENABLED=false) — nothing to do.")
        return 0

    print(f"Demo account: {result['email']}")
    print(f"  account created:  {'yes' if result['created_user'] else 'no (already existed)'}")
    print(f"  reports created:  {result['created_reports']} of {result['reports_total']} kinds")
    if args.reset_password:
        print("  password:         re-applied from configuration")
    elif result["password_matches"]:
        print("  password:         matches the configured credential")
    else:
        print(
            "  password:         MISMATCH — the stored password differs from "
            "DEMO_ACCOUNT_PASSWORD.\n"
            "                    Run again with --reset-password to restore the "
            "documented demo credential."
        )
    print()
    print("Demo login (public demo credential — never use for a real account):")
    print(f"  email:    {settings.demo_account_email}")
    print(f"  password: {settings.demo_account_password}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the persistent demo account (idempotent)")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="re-apply DEMO_ACCOUNT_PASSWORD to an existing demo account",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
