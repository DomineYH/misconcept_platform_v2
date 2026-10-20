"""Lesson analysis bindings to the common admission/execution boundary."""

import asyncio
import hashlib
import json
from contextlib import aclosing
from datetime import timezone
from uuid import uuid4

from src.config import config
from src.demo.lifecycle import NormalContext, lifecycle
from src.demo.provider import Selection, select_content
from src.models import GenerationRun
from src.models.provider_connection import now
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError, StructuredRequest
from src.services.lesson_connections import resolve_frozen_model

PHASES = dict(
    analysis_unified="single", analysis_chunk="chunk", analysis_merge="merge"
)


class AnalysisCaller:
    def __init__(
        self,
        factory,
        *,
        selection,
        session_id,
        owner_id,
        actor_id=None,
        request_id=None,
        run_id=None,
        ctx=None,
        registry=lifecycle,
        demo_adapter=None,
        messages=(),
    ):
        self.ctx = ctx if ctx is not None else NormalContext(factory)
        self.registry = registry
        self.demo_adapter = demo_adapter
        self.messages = messages
        self.factory = factory
        self.session_id = session_id
        self.owner_id = owner_id
        self.request_id = request_id or str(uuid4())
        self.run_id = run_id
        self.selection = selection
        self.model = selection.model_id
        self.actor_id = actor_id or owner_id

    def manifest(self):
        return self.demo_adapter.manifest_path.read_bytes()

    def provenance(self):
        """§19: the server's manifest decides; None keeps normal JSON unchanged."""
        if self.demo_adapter is None:
            return None
        return self.demo_adapter.provenance(json.loads(self.manifest()))

    def demo_selection(self, operation):
        """§17.1: analysis keys follow the whole session's completed pairs."""
        students = {
            m.turn_id
            for m in self.messages
            if m.role == "student" and m.turn_id
        }
        turns = [
            dict(teacher=dict(content=m.content))
            for m in self.messages
            if m.role == "teacher" and m.turn_id in students
        ]
        state, branch, _ = select_content(turns, "", self.demo_adapter.inputs)
        return Selection(
            role="analysis",
            content_state=state,
            branch=branch,
            input_kind=(
                "common"
                if state == "after_compare"
                else branch or "unsupported"
            ),
            manifest_sha256=hashlib.sha256(self.manifest()).hexdigest(),
            analysis_phase=PHASES[operation],
        )

    async def structured(
        self,
        prompt,
        schema,
        operation,
        *,
        validation_context=None,
        context_budget=None,
    ):
        # A dedicated child lease keeps the visitor DB for this call only (§6).
        async with self.registry.child_context(self.ctx) as child:
            return await self.call(
                child,
                prompt,
                schema,
                operation,
                validation_context=validation_context,
                context_budget=context_budget,
            )

    async def call(
        self,
        ctx,
        prompt,
        schema,
        operation,
        *,
        validation_context=None,
        context_budget=None,
    ):
        # No database transaction spans the provider call.
        async with self.factory() as db:
            connection, model, options = await resolve_frozen_model(
                db, self.selection, "analysis", app_mode=config.APP_MODE
            )
            from src.services.analysis_pipeline import load_analysis_lesson

            await load_analysis_lesson(db, self.session_id, self.actor_id)
            run = (
                await db.get(GenerationRun, self.run_id)
                if self.run_id
                else None
            )
            if self.run_id and (run is None or run.status != "running"):
                raise asyncio.CancelledError
        validation = (
            validation_context if validation_context is not None else {}
        )
        validation["actor_id"] = self.actor_id
        request = StructuredRequest(
            connection.provider,
            self.model,
            "analysis",
            "",
            [{"role": "user", "content": prompt}],
            options,
            self.request_id,
            schema,
            validation,
            context_budget_json=context_budget,
            **(
                dict(
                    demo_context=ctx,
                    demo_selection=self.demo_selection(operation),
                )
                if self.demo_adapter is not None
                else {}
            ),
        )
        permit = await admit_call(
            self.factory,
            connection_id=connection.id,
            owner_id=self.owner_id,
            operation=operation,
            role="analysis",
            admin=False,
            expected_connection_version=connection.connection_version,
            model_config_id=model.id,
            expected_model_version=model.config_version,
            model_options=options,
            # Demo admission validates this call's own child context.
            **(
                dict(ctx=ctx, registry=self.registry)
                if self.demo_adapter is not None
                else {}
            ),
        )
        if run is not None:
            from src.services.analysis_runs import RUN_SECONDS

            remaining = (
                RUN_SECONDS
                - (
                    now() - run.started_at.replace(tzinfo=timezone.utc)
                ).total_seconds()
            )
            permit.timeouts = {
                **permit.timeouts,
                "analysis_total": min(
                    permit.timeouts["analysis_total"], max(0, remaining)
                ),
            }
        async with aclosing(
            execute_call(
                permit,
                request,
                kind="structured",
                session_id=self.session_id,
                run_id=self.run_id,
                **(
                    dict(app_mode=ctx.app_mode, demo_adapter=self.demo_adapter)
                    if self.demo_adapter is not None
                    else {}
                ),
            )
        ) as events:
            async for event in events:
                usage = event.usage
                legacy_usage = (
                    {
                        "prompt_tokens": usage["input_tokens"],
                        "completion_tokens": usage["output_tokens"],
                        "total_tokens": usage["total_tokens"],
                    }
                    if usage
                    and all(
                        usage.get(k) is not None
                        for k in (
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                        )
                    )
                    else None
                )
                if event.type == "interrupted":
                    raise asyncio.CancelledError
                if event.type != "completed":
                    # The ledger fails validation; only verified items remain usable.
                    if validation.get(
                        "analysis_errors"
                    ) and event.error_code in {
                        "invalid_output",
                        "invalid_reference",
                    }:
                        return validation["validated_analysis"], legacy_usage
                    raise InvocationError(event.error_code)
                return (
                    event.structured,
                    legacy_usage,
                )
