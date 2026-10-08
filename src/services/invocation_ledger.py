"""Short committed attempt transactions, independent of SDK waits."""

import logging
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError

from src.models import ApiUsageLog
from src.models.provider_connection import now
from src.services.invocation_types import InvocationError
from src.services.openai_usage import normalize_usage

logger = logging.getLogger(__name__)


async def start_attempt(
    factory,
    *,
    request_id,
    owner_id,
    provider,
    model,
    role,
    operation,
    credential_revision,
    probe_step=None,
):
    async with factory() as db:
        entry = ApiUsageLog(
            invocation_id=str(uuid4()),
            request_id=request_id,
            owner_id=owner_id,
            provider=provider,
            model=model,
            role=role,
            operation=operation,
            credential_revision=credential_revision,
            probe_step=probe_step,
            attempt_no=1,
            status="running",
            timestamp=now(),
            started_at=now(),
            retry_wait_ms=0,
        )
        db.add(entry)
        try:
            await db.flush()
            entry_id = entry.id
            await db.commit()
            return entry_id
        except SQLAlchemyError:
            await db.rollback()
            raise InvocationError("configuration_unavailable") from None


async def finish_attempt(
    factory,
    entry_id,
    *,
    status,
    error_code=None,
    usage=None,
    first_output_at=None,
):
    async with factory() as db:
        try:
            changed = await db.execute(
                update(ApiUsageLog)
                .where(
                    ApiUsageLog.id == entry_id,
                    ApiUsageLog.status == "running",
                    ApiUsageLog.finished_at.is_(None),
                )
                .values(
                    status=status,
                    error_code=error_code,
                    first_output_at=first_output_at,
                    finished_at=now(),
                    **(usage or normalize_usage(None)),
                )
            )
            await db.commit()
            return changed.rowcount == 1
        except SQLAlchemyError:
            await db.rollback()
            logger.error("Invocation finalization failed")
            raise InvocationError("configuration_unavailable") from None
