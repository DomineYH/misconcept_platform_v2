"""Durable whole-plan reservation and single-attempt synthetic invocations."""

import asyncio
import json
import os
from contextlib import aclosing
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic_ns

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.models import ApiUsageLog
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_ledger import finish_attempt, start_attempt
from src.services.invocation_types import InvocationError
from src.services.provider_smoke_plan import synthetic_request


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_sha256: str
    approver: str = Field(min_length=1, max_length=200)
    approved_at: str
    max_usd: str


def validate_approval(report, value):
    approval = Approval.model_validate(value)
    date = datetime.fromisoformat(approval.approved_at)
    budget = Decimal(approval.max_usd)
    if (
        not approval.approver.strip()
        or date.tzinfo is None
        or date > datetime.now(timezone.utc)
        or approval.plan_sha256 != report["plan_sha256"]
        or not budget.is_finite()
        or not Decimal(0) < budget <= Decimal(3)
        or Decimal(report["plan"]["reserved_usd"]) > budget
    ):
        raise InvocationError("approval_mismatch")
    return approval.model_dump()


def save(stream, report):
    stream.seek(0)
    json.dump(report, stream, ensure_ascii=False)
    stream.truncate()
    stream.flush()
    os.fsync(stream.fileno())


def safe_usage(entry):
    fields = (
        "id",
        "role",
        "operation",
        "status",
        "attempt_no",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "usage_complete",
        "estimated_cost_usd",
        "pricing_source",
        "pricing_as_of",
    )
    result = {name: getattr(entry, name) for name in fields}
    for name in ("started_at", "first_output_at", "finished_at"):
        value = getattr(entry, name)
        result[name] = value.isoformat() if value else None
    return result


async def execute_plan(factory, report, approval, output):
    if output is None:
        raise InvocationError("output_required")
    approval = validate_approval(report, approval)
    report.update(mode="execute", approval=approval, status="running")
    plan = report["plan"]
    # The unique ledger invocation consumes approval even if interrupted before SDK entry.
    with output.open("x+", encoding="utf-8") as stream:
        save(stream, report)
        reserving = asyncio.create_task(
            start_attempt(
                factory,
                request_id=report["plan_sha256"],
                invocation_id=report["plan_sha256"],
                attempt_no=0,
                owner_id=plan["owner_id"],
                provider=None,
                model=None,
                role=None,
                operation="smoke_reservation",
                credential_revision=None,
                context_budget_json=dict(plan=plan, approval=approval),
            )
        )
        try:
            reservation_id = await asyncio.shield(reserving)
        except asyncio.CancelledError:
            await reserving
            report["status"] = "failed"
            save(stream, report)
            raise
        await finish_attempt(factory, reservation_id, status="reserved")
        report["reservation_ledger_id"] = reservation_id
        save(stream, report)
        for index, call in enumerate(plan["calls"]):
            request_id = f'{report["plan_sha256"]}:{index}'
            kind, request = synthetic_request(call, request_id)
            request = replace(
                request,
                service_tier=(
                    "default" if call["provider"] == "openai" else None
                ),
                context_budget_json=dict(
                    smoke_plan_sha256=report["plan_sha256"],
                    reserved_usd=call["reserved_usd"],
                ),
            )
            started = monotonic_ns()
            terminal = None
            try:
                permit = await admit_call(
                    factory,
                    connection_id=call["connection_id"],
                    owner_id=plan["owner_id"],
                    operation=(
                        "analysis_unified"
                        if call["role"] == "analysis"
                        else call["role"]
                    ),
                    role=call["role"],
                    admin=True,
                    expected_connection_version=call["connection_version"],
                    model_config_id=call["model_config_id"],
                    expected_model_version=call["config_version"],
                    model_options=call["options"],
                )
                async with aclosing(
                    execute_call(
                        permit, request, kind=kind, usage_scope="comparison"
                    )
                ) as events:
                    async for terminal in events:
                        pass
            except InvocationError:
                pass
            ended = monotonic_ns()
            async with factory() as db:
                entries = (
                    await db.scalars(
                        select(ApiUsageLog).where(
                            ApiUsageLog.request_id == request_id
                        )
                    )
                ).all()
            known = bool(entries and entries[-1].estimated_cost_usd is not None)
            valid = bool(terminal and terminal.type == "completed" and known)
            if valid:
                entry = entries[-1]
                valid = (
                    len(entries) == 1
                    and entry.input_tokens is not None
                    and 0 <= entry.input_tokens <= call["input_tokens_upper"]
                    and entry.output_tokens is not None
                    and 0 <= entry.output_tokens <= call["output_tokens_upper"]
                    and Decimal(str(entry.estimated_cost_usd))
                    <= Decimal(call["reserved_usd"])
                    and (
                        entry.reasoning_tokens is None
                        or entry.reasoning_tokens
                        <= call["reasoning_tokens_upper"]
                    )
                    and (
                        terminal.provider_model_id is None
                        or terminal.provider_model_id == call["model"]
                    )
                )
            status = (
                "completed"
                if valid
                else (
                    "cancelled"
                    if terminal and terminal.type == "interrupted"
                    else "failed"
                )
            )
            report["samples"].append(
                dict(
                    workload="approved_provider_smoke",
                    repeat=1,
                    status=status,
                    conditions=dict(
                        provider=call["provider"],
                        model=call["model"],
                        role=call["role"],
                        model_options=call["options"],
                        input_sha256=call["input_sha256"],
                        runtime="native",
                        worker_count=1,
                    ),
                    clocks=dict(
                        server="python.monotonic_ns",
                        browser=None,
                        ledger="UTC wall clock; raw only",
                    ),
                    raw_ns=dict(
                        request_start=started,
                        response_end=ended,
                        provider_calls=[],
                    ),
                    raw_browser_ms=None,
                    durations_ms=dict(
                        request=(ended - started) / 1e6,
                        provider_first_body=None,
                        provider_end=None,
                        first_visible_body=None,
                        save_complete=None,
                        final_validated_result=(
                            (ended - started) / 1e6
                            if valid and kind == "structured"
                            else None
                        ),
                    ),
                    unknown_reason="SDK subintervals and browser visibility unobserved",
                    reserved_usd=call["reserved_usd"],
                    cost_status="known" if known else "unknown",
                    ledger=[safe_usage(entry) for entry in entries],
                )
            )
            save(stream, report)
            if not valid:
                report["status"] = "failed"
                break
        else:
            report["status"] = "completed"
        save(stream, report)
    return report
