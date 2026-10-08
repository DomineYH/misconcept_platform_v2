"""Lesson analysis bindings to the common admission/execution boundary."""

import asyncio
from contextlib import aclosing
from uuid import uuid4

from src.config import config
from src.services.call_admission import admit_call
from src.services.call_execution import execute_call
from src.services.invocation_types import InvocationError, StructuredRequest
from src.services.lesson_connections import resolve_lesson_model


class AnalysisCaller:
    def __init__(
        self, factory, *, session_id=None, owner_id=None, request_id=None
    ):
        self.factory = factory
        self.session_id = session_id
        self.owner_id = owner_id
        self.request_id = request_id or str(uuid4())
        self.model = config.ANALYSIS_MODEL or "gpt-5"
        self.reasoning_effort = config.ANALYSIS_REASONING

    async def structured(
        self, prompt, schema, operation, max_tokens, *, normalize=None
    ):
        # Each parallel call gets its own short-lived DB session.
        async with self.factory() as db:
            connection, model, options = await resolve_lesson_model(
                db,
                self.model,
                "analysis",
                {
                    "max_output_tokens": max_tokens,
                    "reasoning": {"effort": self.reasoning_effort},
                },
            )
        validation = {"normalize": normalize} if normalize else {}
        request = StructuredRequest(
            "openai",
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
                self.last_usage = legacy_usage
                if event.type == "interrupted":
                    raise asyncio.CancelledError
                if event.type != "completed":
                    raise InvocationError(event.error_code)
                return (
                    validation.get("normalized", event.structured),
                    legacy_usage,
                )
