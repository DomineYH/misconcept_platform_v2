"""Lesson analysis bindings to the common admission/execution boundary."""

import asyncio
from contextlib import aclosing
from uuid import uuid4

from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError, StructuredRequest
from src.services.lesson_connections import resolve_frozen_model


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
    ):
        self.factory = factory
        self.session_id = session_id
        self.owner_id = owner_id
        self.request_id = request_id or str(uuid4())
        self.selection = selection
        self.model = selection.model_id
        self.actor_id = actor_id or owner_id

    async def structured(
        self,
        prompt,
        schema,
        operation,
        *,
        normalize=None,
        validation_context=None,
    ):
        # No database transaction spans the provider call.
        async with self.factory() as db:
            connection, model, options = await resolve_frozen_model(
                db, self.selection, "analysis"
            )
            from src.services.analysis_pipeline import load_analysis_lesson

            await load_analysis_lesson(db, self.session_id, self.actor_id)
        validation = (
            validation_context if validation_context is not None else {}
        )
        validation["actor_id"] = self.actor_id
        if normalize:
            validation["normalize"] = normalize
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
        )
        async with aclosing(
            execute_call(
                permit, request, kind="structured", session_id=self.session_id
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
                    validation.get("normalized", event.structured),
                    legacy_usage,
                )
