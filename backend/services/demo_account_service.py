"""Persistent demo account: idempotent seed and public credentials.

The shared demo account (``role="demo"``) is a normal, database-backed
account. It signs in through exactly the same authentication pipeline as
every other user, is never an administrator, and can neither upload reports,
change its password, delete itself nor manage sessions.

The seed is idempotent — run it any number of times (deployments, restarts,
rebuilds); it never duplicates the account and never overwrites its password
unless explicitly asked (``reset_password=True``).

Run it with ``python scripts/seed_demo_account.py`` — or call
:func:`seed_demo_account` directly (the tests do).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_settings
from backend.core.security import hash_password, verify_password
from backend.schemas.demo import DemoAccountInfo
from backend.services import analysis_service, demo_service
from backend.services.demo_reports import DEMO_REPORTS
from database.models import Report, User

logger = logging.getLogger("medilens.demo.account")

DEMO_ROLE = "demo"

DEMO_ACCOUNT_NOTICE = (
    "Shared demo account for exploring MediLens AI — sample reports only, "
    "never use it for real medical data."
)


def get_public_demo_info() -> DemoAccountInfo:
    """Public credentials straight from settings (no database access).

    These values are displayed openly on the sign-in screen by design; the
    account behind them still authenticates normally against the database.
    """
    settings = get_settings()
    return DemoAccountInfo(
        enabled=settings.demo_account_enabled,
        email=settings.demo_account_email,
        password=settings.demo_account_password,
        name=settings.demo_account_name,
        notice=DEMO_ACCOUNT_NOTICE,
    )


async def get_demo_user(db: AsyncSession) -> User | None:
    email = get_settings().demo_account_email.strip().lower()
    return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def seed_demo_account(db: AsyncSession, *, reset_password: bool = False) -> dict:
    """Ensure the demo account and its analyzed synthetic reports exist.

    Idempotent by design:

    * the account is created only when missing — an existing demo account is
      left untouched (``reset_password=True`` is the explicit opt-in to
      re-apply the configured password);
    * each synthetic report is created only when missing, then analyzed with
      the real pipeline (completed analyses are reused, never duplicated).
    """
    settings = get_settings()
    if not settings.demo_account_enabled:
        logger.info("demo account disabled (DEMO_ACCOUNT_ENABLED=false) — nothing to seed")
        return {"enabled": False, "created_user": False, "created_reports": 0, "password_matches": None}

    email = settings.demo_account_email.strip().lower()
    user = await get_demo_user(db)
    created_user = False

    if user is None:
        user = User(
            name=settings.demo_account_name,
            email=email,
            password_hash=hash_password(settings.demo_account_password),
            role=DEMO_ROLE,
            locale="en",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        created_user = True
        logger.info("demo account created", extra={"event": "demo_account_created", "email": email})
    else:
        if user.role != DEMO_ROLE:
            previous_role = user.role
            user.role = DEMO_ROLE
            await db.commit()
            logger.warning(
                "existing demo account had role %r — reset to %r",
                previous_role,
                DEMO_ROLE,
                extra={"event": "demo_account_role_fixed"},
            )
        if reset_password:
            user.password_hash = hash_password(settings.demo_account_password)
            await db.commit()
            logger.info(
                "demo account password re-applied from configuration",
                extra={"event": "demo_account_password_reset"},
            )
        elif not verify_password(settings.demo_account_password, user.password_hash):
            logger.warning(
                "demo account password no longer matches DEMO_ACCOUNT_PASSWORD; "
                "run the seed with --reset-password to restore the documented credential",
                extra={"event": "demo_account_password_mismatch"},
            )

    created_reports = await ensure_demo_reports(db, user)
    result = {
        "enabled": True,
        "email": email,
        "created_user": created_user,
        "created_reports": created_reports,
        "reports_total": len(DEMO_REPORTS),
        "password_matches": verify_password(settings.demo_account_password, user.password_hash),
    }
    logger.info("demo account seed complete", extra={"event": "demo_account_seeded", **result})
    return result


async def ensure_demo_reports(db: AsyncSession, user: User) -> int:
    """Create any missing synthetic report for the demo account.

    Returns the number of reports created. Existing reports are left as-is;
    their analysis is re-run only when it is missing (completed analyses are
    reused by :func:`analysis_service.run_analysis`).
    """
    created = 0
    for kind in DEMO_REPORTS:
        report = (
            await db.execute(
                select(Report).where(
                    Report.user_id == user.id,
                    Report.is_demo.is_(True),
                    Report.storage_key == f"demo/{kind}",
                )
            )
        ).scalar_one_or_none()
        if report is None:
            report = await demo_service.build_demo_report(db, user, kind)
            created += 1
        try:
            await analysis_service.run_analysis(
                db, report, language="en", explanation_level="simple"
            )
        except Exception:  # noqa: BLE001 — a provider outage must not break the seed
            logger.warning(
                "demo report %r left without an analysis (provider unavailable); "
                "re-run the seed once the AI provider is reachable",
                kind,
                exc_info=True,
                extra={"event": "demo_report_analysis_deferred"},
            )
    return created
