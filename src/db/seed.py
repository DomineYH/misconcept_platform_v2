"""Database seeding script with default data."""

import asyncio
import secrets
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from sqlalchemy import func, select, text

from src.api.schemas.scenario_config import ScenarioConfig
from src.config import config
from src.db.connection import AsyncSessionLocal
from src.db.init_schema import init_schema
from src.models import Scenario, ScenarioGroup


def _resolve_admin_password() -> str:
    """Return the configured default admin password."""
    admin_password = config.ADMIN_DEFAULT_PASSWORD
    if admin_password:
        return admin_password

    admin_password = secrets.token_urlsafe(16)
    print(
        f"WARNING: ADMIN_DEFAULT_PASSWORD not set. "
        f"Generated random password: {admin_password}"
    )
    return admin_password


def _hash_password(plain: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(
        plain.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


async def _ensure_default_group(session) -> int:
    """Ensure the default group exists and return its id."""
    result = await session.execute(
        text("SELECT id FROM user_group WHERE name = :name"),
        {"name": "default"},
    )
    group_id = result.scalar_one_or_none()
    if group_id is not None:
        return group_id

    # INSERT OR IGNORE: idempotent under concurrent worker startup —
    # a racing worker's UNIQUE(name) insert becomes a no-op instead of
    # raising IntegrityError and aborting boot.
    await session.execute(
        text(
            """
            INSERT OR IGNORE INTO user_group (name, description)
            VALUES (:name, :desc)
            """
        ),
        {
            "name": "default",
            "desc": "기본 그룹",
        },
    )
    result = await session.execute(
        text("SELECT id FROM user_group WHERE name = :name"),
        {"name": "default"},
    )
    return result.scalar_one()


async def ensure_default_admin_user(
    session, *, default_group_id: Optional[int] = None
) -> int:
    """Ensure the default admin account exists and is usable."""
    if default_group_id is None:
        default_group_id = await _ensure_default_group(session)

    result = await session.execute(
        text(
            """
            SELECT id, role, password_hash, group_id
            FROM user
            WHERE username = :username
            """
        ),
        {"username": "admin"},
    )
    admin_row = result.mappings().first()

    if admin_row is None:
        # INSERT OR IGNORE: idempotent under concurrent worker startup —
        # if another worker won the race, this is a no-op and the canonical
        # row is re-read below.
        await session.execute(
            text(
                """
                INSERT OR IGNORE INTO user
                (username, nickname, password_hash,
                 role, group_id, created_at)
                VALUES (:username, :nickname, :pw_hash,
                        :role, :group_id, :created_at)
                """
            ),
            {
                "username": "admin",
                "nickname": "Administrator",
                "pw_hash": _hash_password(_resolve_admin_password()),
                "role": "admin",
                "group_id": default_group_id,
                "created_at": datetime.now(timezone.utc),
            },
        )
        result = await session.execute(
            text(
                """
                SELECT id, role, password_hash, group_id
                FROM user
                WHERE username = :username
                """
            ),
            {"username": "admin"},
        )
        admin_row = result.mappings().first()
        # After INSERT OR IGNORE, the row is guaranteed to exist (ours
        # or the winner's). Fall through to the unified UPDATE-if-needed
        # path below so a race-losing worker still reconciles role /
        # password_hash / group_id like it does for any pre-existing row.

    # SECURITY: Never auto-promote a non-admin row to admin. If someone
    # created a regular user named "admin" (via the admin UI or a manual
    # DB edit), this seed path must NOT silently grant them admin
    # privileges or reset their password. Bootstrap only touches rows
    # that are already admins.
    if admin_row["role"] != "admin":
        return admin_row["id"]

    # Recovery path: the row is already an admin. If the password_hash
    # was cleared (operator empties it in the DB to trigger a reseed)
    # or the group_id is missing, repair in place.
    next_hash = admin_row["password_hash"]
    next_group_id = admin_row["group_id"] or default_group_id

    if not admin_row["password_hash"]:
        next_hash = _hash_password(_resolve_admin_password())

    if (
        next_hash != admin_row["password_hash"]
        or next_group_id != admin_row["group_id"]
    ):
        await session.execute(
            text(
                """
                UPDATE user
                SET password_hash = :password_hash,
                    group_id = :group_id
                WHERE id = :id
                """
            ),
            {
                "id": admin_row["id"],
                "password_hash": next_hash,
                "group_id": next_group_id,
            },
        )

    return admin_row["id"]


async def ensure_default_admin_account() -> None:
    """Create the default admin account when it is missing."""
    async with AsyncSessionLocal() as session:
        default_group_id = await _ensure_default_group(session)
        await ensure_default_admin_user(
            session, default_group_id=default_group_id
        )
        await session.commit()


async def seed_database():
    """Populate database with default data."""
    # First ensure schema exists
    await init_schema()

    async with AsyncSessionLocal() as session:
        default_group_id = await _ensure_default_group(session)
        admin_id = await ensure_default_admin_user(
            session, default_group_id=default_group_id
        )

        if await session.scalar(select(func.count(Scenario.id))):
            await session.commit()
            print("Database already seeded, ensured default admin only")
            return

        scenario = Scenario(
            title="Fraction Addition Misconception",
            subject="수학",
            target_grade="초등학교 5학년",
            created_by=admin_id,
            status="draft",
            config_json=ScenarioConfig(
                problem={
                    "public_text": "1/4 + 1/2은 얼마인가요?",
                    "learning_objective": "같은 전체를 기준으로 통분하여 분수를 더한다.",
                },
                student={
                    "name": "민수",
                    "public_profile": "분수의 덧셈을 배우는 학생",
                    "internal_profile": "자연수 계산에는 익숙하지만 분수 개념이 어렵다.",
                    "misconception": "분자와 분모를 각각 더하면 된다고 생각한다.",
                    "behavior_instruction": "자신의 계산 방법을 설명하고 교사의 질문에 응답한다.",
                },
                mentor={"mode": "off"},
                analysis={
                    "context": "분수 덧셈에서 학생의 생각을 확인하는 교사 질문을 평가한다.",
                    "expected_understanding": "같은 크기의 단위로 분수를 나타내어 더한다.",
                    "instruction": "교사의 질문에 대한 강점과 개선점을 제시한다.",
                    "classification_enabled": False,
                },
                runtime={},
            ).model_dump(),
        )
        session.add(scenario)
        await session.flush()
        session.add(
            ScenarioGroup(scenario_id=scenario.id, group_id=default_group_id)
        )
        await session.commit()
        print(
            "Database seeded with a unified draft; select verified role models before publishing"
        )


if __name__ == "__main__":
    asyncio.run(seed_database())
