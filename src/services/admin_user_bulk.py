"""Bulk user upload service — CSV parsing, validation, registration."""

import csv
import io
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.user import (
    BulkFailure,
    BulkPreviewResponse,
    BulkPreviewRow,
    BulkRegisterResponse,
    BulkUserEntry,
)
from src.models.user import User
from src.models.user_group import UserGroup

MAX_FILE_SIZE = 1024 * 1024  # 1MB
MAX_ROWS = 100
DEFAULT_PASSWORD = "00000000"
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


def parse_csv(file_content: bytes) -> list[dict]:
    """Parse CSV bytes into list of row dicts.

    Tries UTF-8 first, falls back to EUC-KR.
    Validates file size, required columns, and row count.
    """
    if len(file_content) > MAX_FILE_SIZE:
        raise ValueError("파일 크기가 1MB를 초과합니다.")

    text = None
    for encoding in ("utf-8-sig", "utf-8", "euc-kr"):
        try:
            text = file_content.decode(encoding)
            break
        except (UnicodeDecodeError, ValueError):
            continue

    if text is None:
        raise ValueError(
            "파일 인코딩을 인식할 수 없습니다. "
            "UTF-8 또는 EUC-KR을 사용해주세요."
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
        raise ValueError("CSV 헤더를 읽을 수 없습니다.")

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
            "필수 컬럼이 누락되었습니다: "
            f"{', '.join(sorted(missing))}. "
            f"(발견된 컬럼: {', '.join(found)})"
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
        raise ValueError("CSV 파일이 비어 있습니다.")

    if len(rows) > MAX_ROWS:
        raise ValueError(
            f"최대 {MAX_ROWS}명까지 업로드할 수 있습니다. 현재 {len(rows)}명"
        )

    return rows


async def validate_bulk_users(
    rows: list[dict],
    db: AsyncSession,
) -> BulkPreviewResponse:
    """Validate parsed CSV rows against DB state."""
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
            errors.append("사용자 ID는 3자 이상 50자 이하여야 합니다.")
        elif not USERNAME_PATTERN.match(username):
            errors.append(
                "사용자 ID는 영문, 숫자, 언더스코어만 사용 가능합니다."
            )

        if len(nickname) < 2 or len(nickname) > 30:
            errors.append("닉네임은 2자 이상 30자 이하여야 합니다.")

        if role not in ("teacher", "admin"):
            errors.append(f"유효하지 않은 역할: {role}")

        if username in existing_usernames:
            errors.append("이미 존재하는 사용자 ID")

        if username in seen_usernames:
            errors.append("CSV 내 중복된 사용자 ID")

        seen_usernames.add(username)

        group_id = None
        resolved_group_name = None
        if group_name:
            group = group_map.get(group_name)
            if group:
                group_id = group.id
                resolved_group_name = group.name
            else:
                errors.append(f"존재하지 않는 그룹: {group_name}")
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
) -> BulkRegisterResponse:
    """Register validated users into the database.

    Processes each user individually — one failure
    does not affect others.
    """
    if not users:
        return BulkRegisterResponse(success_count=0, fail_count=0, failures=[])

    # Re-check existing usernames at registration time
    usernames = [u.username for u in users]
    result = await db.execute(
        select(User.username).where(User.username.in_(usernames))
    )
    existing = {r[0] for r in result.all()}

    success_count = 0
    failures: list[BulkFailure] = []

    for entry in users:
        if entry.username in existing:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason="이미 존재하는 사용자 ID",
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
                    reason="유효하지 않은 사용자 ID 형식",
                )
            )
            continue

        if len(entry.nickname) < 2 or len(entry.nickname) > 30:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason="닉네임은 2자 이상 30자 이하여야 합니다.",
                )
            )
            continue

        if entry.role not in ("teacher", "admin"):
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason="유효하지 않은 역할",
                )
            )
            continue

        new_user = User(
            username=entry.username,
            nickname=entry.nickname,
            role=entry.role,
            group_id=entry.group_id,
        )
        new_user.set_password(DEFAULT_PASSWORD)
        db.add(new_user)

        try:
            async with db.begin_nested():
                await db.flush()
            existing.add(entry.username)
            success_count += 1
        except IntegrityError:
            failures.append(
                BulkFailure(
                    username=entry.username,
                    nickname=entry.nickname,
                    reason="사용자 등록 중 오류가 발생했습니다.",
                )
            )

    return BulkRegisterResponse(
        success_count=success_count,
        fail_count=len(failures),
        failures=failures,
    )
