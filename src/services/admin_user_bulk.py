"""Bulk user upload service — CSV parsing, validation, registration."""

import csv
import io
import re
import secrets

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.user import (
    BulkCredential,
    BulkFailure,
    BulkPreviewResponse,
    BulkPreviewRow,
    BulkRegisterResponse,
    BulkUserEntry,
)
from src.config import config
from src.demo.database import DemoError
from src.i18n import translate
from src.models.user import User
from src.models.user_group import UserGroup

MAX_FILE_SIZE = 1024 * 1024  # 1MB
MAX_ROWS = 100
REQUIRED_COLUMNS = {"username", "nickname"}
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
COLUMN_ALIASES: dict[str, str] = {
    "사용자": "username",
    "사용자id": "username",
    "사용자 id": "username",
    "닉네임": "nickname",
    "역할": "role",
    "그룹": "group",
}


async def demo_quota(db, model, limit, *where):
    """§21: take the visitor writer before counting current rows, so two
    creates cannot both use the last slot and deletes free theirs."""
    if config.APP_MODE != "demo":
        return None
    await db.execute(text("BEGIN IMMEDIATE"))
    used = await db.scalar(
        select(func.count()).select_from(model).where(*where)
    )
    if used >= limit:
        raise DemoError("LIMIT_REACHED")
    return limit - used


async def demo_bulk_quota(db, count):
    """§8: preview and register both cap rows at min(upload rows, users left)."""
    remaining = await demo_quota(db, User, config.DEMO_MAX_USERS)
    if remaining is not None and count > min(
        config.DEMO_MAX_UPLOAD_ROWS, remaining
    ):
        raise DemoError("LIMIT_REACHED")


def parse_csv(file_content: bytes, language="ko") -> list[dict]:
    """Parse CSV bytes into list of row dicts.

    Tries UTF-8 first, falls back to EUC-KR.
    Validates file size, required columns, and row count.
    """
    if len(file_content) > MAX_FILE_SIZE:
        raise ValueError(translate("admin.the_file_exceeds_1mb", language))

    text = None
    for encoding in ("utf-8-sig", "utf-8", "euc-kr"):
        try:
            text = file_content.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue

    if text is None:
        raise ValueError(
            translate(
                "admin.unrecognized_file_encoding_use_utf_8_or_euc_kr", language
            )
        )

    # Strip any remaining BOM characters after decoding
    text = text.lstrip("\ufeff")

    # Auto-detect delimiter (comma, tab, semicolon)
    first_line = text.split("\n", 1)[0]
    delimiter = ","
    for candidate in ("\t", ";"):
        if candidate in first_line:
            delimiter = candidate
            break

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    if reader.fieldnames is None:
        raise ValueError(
            translate("admin.could_not_read_csv_headers", language)
        )

    # Normalize: strip, lowercase, apply Korean aliases
    cleaned_fields = []
    for f in reader.fieldnames:
        key = f.strip().lower()
        key = COLUMN_ALIASES.get(key, key)
        cleaned_fields.append(key)

    missing = REQUIRED_COLUMNS - set(cleaned_fields)
    if missing:
        found = [f.strip() for f in reader.fieldnames]
        raise ValueError(
            translate(
                "admin.required_columns_missing",
                language,
                missing_columns=", ".join(sorted(missing)),
                found_columns=", ".join(found),
            )
        )

    rows = []
    for raw_row in reader:
        row = {}
        for orig_key, clean_key in zip(reader.fieldnames, cleaned_fields):
            val = (raw_row.get(orig_key) or "").strip()
            row[clean_key] = val
        rows.append(
            {
                "username": row.get("username", ""),
                "nickname": row.get("nickname", ""),
                "role": row.get("role", ""),
                "group": row.get("group", ""),
            }
        )

    if len(rows) == 0:
        raise ValueError(translate("admin.the_csv_file_is_empty", language))

    if len(rows) > MAX_ROWS:
        raise ValueError(
            translate(
                "admin.upload_user_limit",
                language,
                limit=MAX_ROWS,
                count=len(rows),
            )
        )

    return rows


async def validate_bulk_users(
    rows: list[dict],
    db: AsyncSession,
    language="ko",
) -> BulkPreviewResponse:
    """Validate parsed CSV rows against DB state."""
    await demo_bulk_quota(db, len(rows))
    # Load all existing usernames
    result = await db.execute(select(User.username))
    existing_usernames = {r[0] for r in result.all()}

    # Load all groups
    result = await db.execute(select(UserGroup).order_by(UserGroup.name))
    all_groups = result.scalars().all()
    group_map = {g.name: g for g in all_groups}
    groups_list = [{"id": g.id, "name": g.name} for g in all_groups]

    preview_rows = []
    seen_usernames: set[str] = set()
    valid_count = 0
    error_count = 0

    for i, row in enumerate(rows, start=1):
        errors: list[str] = []
        username = row.get("username", "")
        nickname = row.get("nickname", "")
        role = row.get("role", "").strip()
        group_name = row.get("group", "").strip()

        if not role:
            role = "teacher"

        if len(username) < 3 or len(username) > 50:
            errors.append(
                translate(
                    "admin.user_ids_must_contain_3_to_50_characters", language
                )
            )
        elif not USERNAME_PATTERN.match(username):
            errors.append(
                translate(
                    "admin.user_ids_may_contain_only_letters_numbers_and_underscores",
                    language,
                )
            )

        if len(nickname) < 2 or len(nickname) > 30:
            errors.append(
                translate(
                    "admin.nicknames_must_contain_2_to_30_characters", language
                )
            )

        if role not in ("teacher", "admin"):
            errors.append(
                translate("admin.invalid_role_value", language, role=role)
            )

        if username in existing_usernames:
            errors.append(translate("admin.bulk_user_id_exists", language))

        if username in seen_usernames:
            errors.append(translate("admin.duplicate_user_id_in_csv", language))

        seen_usernames.add(username)

        group_id = None
        resolved_group_name = None
        if group_name:
            group = group_map.get(group_name)
            if group:
                group_id = group.id
                resolved_group_name = group.name
            else:
                errors.append(
                    translate(
                        "admin.group_not_found_value",
                        language,
                        group_name=group_name,
                    )
                )
                resolved_group_name = group_name

        if errors:
            error_count += 1
        else:
            valid_count += 1

        preview_rows.append(
            BulkPreviewRow(
                row_num=i,
                username=username,
                nickname=nickname,
                role=role,
                group_name=resolved_group_name,
                group_id=group_id,
                errors=errors,
            )
        )

    return BulkPreviewResponse(
        rows=preview_rows,
        groups=groups_list,
        summary={
            "total": len(rows),
            "valid": valid_count,
            "error": error_count,
        },
    )


async def register_bulk_users(
    users: list[BulkUserEntry],
    db: AsyncSession,
    language="ko",
) -> BulkRegisterResponse:
    """Register validated users into the database.

    Processes each user individually — one failure
    does not affect others.
    """
    if not users:
        return BulkRegisterResponse(success_count=0, fail_count=0, failures=[])
    await demo_bulk_quota(db, len(users))

    # Re-check existing usernames at registration time
    usernames = [u.username for u in users]
    result = await db.execute(
        select(User.username).where(User.username.in_(usernames))
    )
    existing = {r[0] for r in result.all()}

    success_count = 0
    failures: list[BulkFailure] = []
    credentials: list[BulkCredential] = []

    for entry in users:
        if entry.username in existing:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason=translate("admin.bulk_user_id_exists", language),
                )
            )
            continue

        # Validate fields
        if (
            len(entry.username) < 3
            or len(entry.username) > 50
            or not USERNAME_PATTERN.match(entry.username)
        ):
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason=translate("admin.invalid_user_id_format", language),
                )
            )
            continue

        if len(entry.nickname) < 2 or len(entry.nickname) > 30:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason=translate(
                        "admin.nicknames_must_contain_2_to_30_characters",
                        language,
                    ),
                )
            )
            continue

        if entry.role not in ("teacher", "admin"):
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason=translate("admin.bulk_invalid_role", language),
                )
            )
            continue

        new_user = User(
            username=entry.username,
            nickname=entry.nickname,
            role=entry.role,
            group_id=entry.group_id,
        )
        initial_password = secrets.token_hex(16)
        new_user.set_password(initial_password)
        try:
            async with db.begin_nested():
                db.add(new_user)
                await db.flush()
            existing.add(entry.username)
            success_count += 1
            credentials.append(
                BulkCredential(
                    username=entry.username,
                    nickname=entry.nickname,
                    initial_password=initial_password,
                )
            )
        except IntegrityError:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason=translate(
                        "admin.an_error_occurred_while_registering_the_user",
                        language,
                    ),
                )
            )

    return BulkRegisterResponse(
        success_count=success_count,
        fail_count=len(failures),
        failures=failures,
        credentials=credentials,
    )
