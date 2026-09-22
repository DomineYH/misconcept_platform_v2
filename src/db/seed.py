"""Database seeding script with default data."""

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from sqlalchemy import text

from src.config import config
from src.db.connection import AsyncSessionLocal
from src.db.init_schema import init_schema


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

        # Check if data already exists
        result = await session.execute(
            text("SELECT COUNT(*) FROM analysis_framework")
        )
        count = result.scalar()

        if count > 0:
            await session.commit()
            print("Database already seeded, ensured default admin only")
            return

        # Seed default analysis framework
        framework_labels = json.dumps(
            [
                {"name": "Pressing", "criteria": ""},
                {"name": "Linking", "criteria": ""},
                {"name": "Directing", "criteria": ""},
                {"name": "Recall", "criteria": ""},
            ],
            ensure_ascii=False,
        )

        await session.execute(
            text(
                """
                INSERT INTO analysis_framework
                (name, description, labels_json)
                VALUES (:name, :desc, :labels)
                """
            ),
            {
                "name": "High/Low Leverage",
                "desc": (
                    "Pedagogical move classification framework "
                    "distinguishing high-leverage (Pressing, "
                    "Linking) from low-leverage (Directing, "
                    "Recall) questions"
                ),
                "labels": framework_labels,
            },
        )

        # Get framework_id and admin user_id
        framework_result = await session.execute(
            text("SELECT id FROM analysis_framework " "WHERE name = :name"),
            {"name": "High/Low Leverage"},
        )
        framework_id = framework_result.scalar()

        # Seed prompt templates (before scenario)
        student_template_id = await _seed_prompt_templates(session, admin_id)

        # Seed sample scenario with student_template_id
        await session.execute(
            text(
                """
                INSERT INTO scenario (
                    title, prompt, student_profile,
                    student_name,
                    is_active, framework_id, created_by,
                    student_template_id,
                    chat_model, chat_temperature,
                    tutor_intervention_threshold
                )
                VALUES (
                    :title, :prompt, :profile,
                    :student_name,
                    :active, :fid, :created,
                    :student_tid,
                    :chat_model, :chat_temp,
                    :tutor_threshold
                )
                """
            ),
            {
                "title": "Fraction Addition Misconception",
                "prompt": (
                    "You are a 5th grade student who believes "
                    "that when adding fractions, you add both "
                    "numerators and denominators directly "
                    "(e.g., 1/2 + 1/3 = 2/5). "
                    "You are working on the problem: "
                    "What is 1/4 + 1/2?"
                ),
                "profile": (
                    "Grade 5 student, strong in whole number "
                    "arithmetic but struggles with fraction "
                    "concepts"
                ),
                "student_name": "민수",
                "active": 1,
                "fid": framework_id,
                "created": admin_id,
                "student_tid": student_template_id,
                "chat_model": None,
                "chat_temp": None,
                "tutor_threshold": None,
            },
        )

        # Seed default chatbot configuration
        chatbot_configs = [
            {
                "key": "student_bot.model",
                "value": "gpt-5-mini",
                "type": "string",
                "desc": "StudentBot LLM model",
            },
            {
                "key": "student_bot.temperature",
                "value": "0.7",
                "type": "float",
                "desc": "StudentBot response creativity",
            },
            {
                "key": "student_bot.max_tokens",
                "value": "150",
                "type": "int",
                "desc": "StudentBot response length limit",
            },
            {
                "key": "tutor_bot.model",
                "value": "gpt-5-mini",
                "type": "string",
                "desc": "TutorBot LLM model",
            },
            {
                "key": "tutor_bot.temperature",
                "value": "0.3",
                "type": "float",
                "desc": "TutorBot response consistency",
            },
            {
                "key": "tutor_bot.max_tokens",
                "value": "100",
                "type": "int",
                "desc": "TutorBot response length limit",
            },
            {
                "key": "tutor_bot.intervention_threshold",
                "value": "3",
                "type": "int",
                "desc": "Interventions per 10 questions",
            },
        ]

        for cfg in chatbot_configs:
            await session.execute(
                text(
                    """
                    INSERT INTO chatbot_config
                    (config_key, config_value,
                     config_type, description)
                    VALUES (:key, :value, :type, :desc)
                    """
                ),
                cfg,
            )

        await session.commit()
        print("Database seeded with default data successfully")


async def _seed_prompt_templates(session, admin_id):
    """Seed prompt templates, return student template id."""
    prompts_dir = Path(__file__).parent.parent / "prompts"

    # Load StudentBot prompt
    student_path = prompts_dir / "student_system.txt"
    if student_path.exists():
        student_text = student_path.read_text(encoding="utf-8")
    else:
        print(f"Warning: {student_path} not found")
        student_text = (
            "당신은 오개념을 가진 학생입니다. " "(기본 폴백 프롬프트)"
        )

    await session.execute(
        text(
            """
            INSERT INTO prompt_template
            (bot_type, template_name, template_text,
             version, updated_by)
            VALUES (:bot_type, :name, :text,
                    :version, :updated_by)
            """
        ),
        {
            "bot_type": "student",
            "name": "Default",
            "text": student_text,
            "version": 1,
            "updated_by": admin_id,
        },
    )

    # Get student template id
    result = await session.execute(
        text(
            "SELECT id FROM prompt_template "
            "WHERE bot_type = 'student' AND "
            "template_name = 'Default'"
        )
    )
    student_template_id = result.scalar()

    # Load TutorBot prompt
    tutor_path = prompts_dir / "tutor_system.txt"
    if tutor_path.exists():
        tutor_text = tutor_path.read_text(encoding="utf-8")
    else:
        print(f"Warning: {tutor_path} not found")
        tutor_text = (
            "당신은 교수법 피드백을 제공하는 튜터입니다. "
            "(기본 폴백 프롬프트)"
        )

    await session.execute(
        text(
            """
            INSERT INTO prompt_template
            (bot_type, template_name, template_text,
             version, updated_by)
            VALUES (:bot_type, :name, :text,
                    :version, :updated_by)
            """
        ),
        {
            "bot_type": "tutor",
            "name": "Default",
            "text": tutor_text,
            "version": 1,
            "updated_by": admin_id,
        },
    )

    print(f"  - StudentBot prompt: {len(student_text)} chars")
    print(f"  - TutorBot prompt: {len(tutor_text)} chars")

    return student_template_id


async def seed_prompts():
    """Migrate prompt files to DB (legacy, now in seed_database)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM prompt_template")
        )
        count = result.scalar()

        if count > 0:
            print("Prompt templates already seeded, skipping")
            return

        admin_id = await ensure_default_admin_user(session)
        admin_result = await session.execute(
            text("SELECT id FROM user WHERE id = :id"),
            {"id": admin_id},
        )
        admin_id = admin_result.scalar()
        await _seed_prompt_templates(session, admin_id)
        await session.commit()
        print("Prompt templates seeded successfully")


if __name__ == "__main__":
    asyncio.run(seed_database())
    asyncio.run(seed_prompts())
