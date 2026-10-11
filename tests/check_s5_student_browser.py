"""Real comparison form, CSRF and SQLite over a mocked provider SDK."""

import asyncio
import base64
import json
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
(ROOT / ".pytest_cache").mkdir(exist_ok=True)


async def check():
    with tempfile.TemporaryDirectory(dir=ROOT / ".pytest_cache") as directory:
        os.environ.update(
            TESTING="true",
            SESSION_SECRET="comparison-browser",
            OPENAI_API_KEY="test",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/comparison.db",
            PROVIDER_SECRET_ENCRYPTION_KEY=base64.b64encode(b"k" * 32).decode(),
            PROVIDER_SECRET_ENCRYPTION_KEY_VERSION="browser-v1",
        )
        import httpx2
        import uvicorn
        from openai import AsyncOpenAI
        from s4_analysis_fixtures import analysis_reply, prompt_inputs
        from sqlalchemy import func, select
        from starlette_csrf import CSRFMiddleware
        from test_student_probe import response_body, sse

        from src.config import config
        from src.db.connection import AsyncSessionLocal, engine
        from src.db.migrations.migrate import run_all_migrations
        from src.main import app
        from src.models import (
            ApiUsageLog,
            ComparisonRun,
            GenerationRun,
            Message,
            ModelConfig,
            ProviderConnection,
            Scenario,
            Session,
            User,
        )
        from src.services import openai_generation
        from src.services.call_admission import active_calls
        from src.services.model_capabilities import DEFINITION_VERSION
        from src.services.provider_secrets import encrypt_key

        await run_all_migrations()
        draft = json.loads((ROOT / "tests/fixtures/s2_draft.json").read_text())
        draft["config"]["problem"].update(
            learning_objective="Compare fractions"
        )
        draft["config"]["student"].update(
            name="Student",
            misconception="Larger denominator",
            behavior_instruction="Explain",
        )
        draft["config"]["analysis"].update(
            context="Analysis context",
            expected_understanding="Correct fractions",
            instruction="Review the full dialogue",
            classification_enabled=True,
            rubric_name="Questions",
            rubric=[
                dict(
                    id="A",
                    name="Explore",
                    criteria="Explore thinking",
                    level="high",
                )
            ],
        )
        async with AsyncSessionLocal() as db:
            admin = User(
                username="comparison_admin", nickname="Admin", role="admin"
            )
            admin.set_password("comparison-password")
            db.add(admin)
            connection = await db.get(ProviderConnection, 1)
            connection.credential_revision = 1
            connection.enabled = True
            connection.encrypted_key, connection.nonce = encrypt_key(
                connection, "sk-browser-mock", 1
            )
            connection.encryption_key_version = "browser-v1"
            connection.masked_hint = "mock"
            model = ModelConfig(
                provider_connection_id=1,
                model_id="gpt-5-mini",
                display_name="Comparison model",
                enabled=True,
                capability_definition_version=DEFINITION_VERSION,
                default_options_json={
                    "max_output_tokens": 1500,
                    "reasoning": {"effort": "medium"},
                },
                verification_state={
                    "student": dict(
                        status="succeeded",
                        credential_revision=1,
                        connection_version=connection.connection_version,
                        capability_definition_version=DEFINITION_VERSION,
                        role_contract_version="s1-v1",
                    ),
                    "mentor": dict(
                        status="succeeded",
                        credential_revision=1,
                        connection_version=connection.connection_version,
                        capability_definition_version=DEFINITION_VERSION,
                        role_contract_version="s3-v1",
                    ),
                },
            )
            model.verification_state = {
                **model.verification_state,
                "analysis": {
                    **model.verification_state["student"],
                    "role_contract_version": "s4-v2",
                },
            }
            db.add(model)
            saved = Scenario(
                title="Saved comparison draft",
                config_json=draft["config"],
                status="draft",
            )
            db.add(saved)
            await db.commit()
        calls, clients, streams = [], [], []

        class MockStream(httpx2.AsyncByteStream):
            def __init__(self, budget):
                self.budget, self.closed = budget, False

            async def __aiter__(self):
                yield sse(
                    "response.output_text.delta",
                    delta=(
                        f"Panel {self.budget - 1799} partial"
                        if self.budget >= 1800
                        else "Live partial"
                    ),
                    sequence_number=0,
                    item_id="msg",
                    output_index=0,
                    content_index=0,
                )
                retried = (
                    self.budget == 1801
                    and sum(call["max_output_tokens"] == 1801 for call in calls)
                    == 2
                )
                if self.budget in (1801, 1802) and not retried:
                    await asyncio.Event().wait()
                await asyncio.sleep(0.5)
                yield sse(
                    "response.completed",
                    response=response_body(
                        "Fast final"
                        if self.budget == 1800
                        else 'Live final <img src=x onerror="window.s5xss=1">'
                    ),
                    sequence_number=1,
                )

            async def aclose(self):
                self.closed = True

        async def upstream(request):
            body = json.loads(request.content)
            calls.append(body)
            if (
                "text" in body
                and body["text"]["format"]["name"] == "MentorOutput"
            ):
                budget = body["max_output_tokens"]
                if budget == 1801:
                    await asyncio.Event().wait()
                await asyncio.sleep(0.15)
                intervene = not (
                    "자동 검사입니다" in body["instructions"] and budget == 1500
                )
                return httpx2.Response(
                    200,
                    json=response_body(
                        json.dumps(
                            dict(
                                should_intervene=intervene,
                                feedback=(
                                    'Live coaching <img src=x onerror="window.s5xss=1">'
                                    if intervene
                                    else ""
                                ),
                                reason_summary="PRIVATE MENTOR REASON",
                            )
                        ),
                    ),
                )
            if "text" in body:
                reply = analysis_reply(prompt_inputs(body)["messages"])
                reply["strengths"] = [
                    dict(
                        message_id=1,
                        quote=reply["message_classifications"][0]["quote"],
                        reason="Evidence",
                    )
                ]
                if body["max_output_tokens"] == 8193:
                    reply["message_classifications"][0][
                        "quote"
                    ] = "PRIVATE_UNVERIFIED_QUOTE"
                await asyncio.sleep(0.5)
                return httpx2.Response(
                    200,
                    json=response_body(
                        "PRIVATE_UNVERIFIED_JSON"
                        if body["max_output_tokens"] == 8194
                        else json.dumps(reply)
                    ),
                )
            stream = MockStream(body["max_output_tokens"])
            streams.append(stream)
            return httpx2.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=stream,
            )

        def sdk(**kwargs):
            client = AsyncOpenAI(
                **kwargs,
                http_client=httpx2.AsyncClient(
                    transport=httpx2.MockTransport(upstream)
                ),
            )
            clients.append(client)
            return client

        openai_generation.AsyncOpenAI = sdk
        original_connect = socket.socket.connect

        def localhost_only(sock, address):
            if not isinstance(address, tuple) or address[0] != "127.0.0.1":
                raise AssertionError("External socket forbidden")
            return original_connect(sock, address)

        socket.socket.connect = localhost_only
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(
                CSRFMiddleware(
                    app,
                    secret=config.SESSION_SECRET,
                    exempt_urls=[re.compile("/login")],
                    header_name="x-csrf-token",
                ),
                log_level="warning",
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    await asyncio.sleep(0.01)
            process = await asyncio.create_subprocess_exec(
                "node",
                "tests/check_s5_student_browser.mjs",
                str(listener.getsockname()[1]),
                str(saved.id),
                cwd=ROOT,
            )
            try:
                async with asyncio.timeout(90):
                    assert await process.wait() == 0
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert len(calls) == 11
            assert all(call["stream"] and "text" not in call for call in calls)
            async with AsyncSessionLocal() as db:
                assert (
                    await db.scalar(select(func.count()).select_from(Scenario))
                    == 1
                )
                assert (
                    await db.scalar(select(func.count()).select_from(Session))
                    == 0
                )
                assert (
                    await db.scalar(select(func.count()).select_from(Message))
                    == 0
                )
                assert (
                    await db.scalar(
                        select(func.count()).select_from(ComparisonRun)
                    )
                    == 5
                )
                records = (
                    await db.scalars(
                        select(ComparisonRun).order_by(ComparisonRun.created_at)
                    )
                ).all()
                assert all(record.status == "finished" for record in records)
                assert [
                    run.status
                    for run in (
                        await db.scalars(
                            select(GenerationRun)
                            .where(
                                GenerationRun.comparison_run_id
                                == records[-1].id
                            )
                            .order_by(GenerationRun.panel_id)
                        )
                    ).all()
                ] == ["completed", "interrupted", "interrupted"]
                ledger = (await db.scalars(select(ApiUsageLog))).all()
                assert len(ledger) == 11 and all(
                    entry.status != "running" and entry.finished_at is not None
                    for entry in ledger
                )
                assert (await db.get(Scenario, saved.id)).config_version == 1
            assert not active_calls
            assert all(stream.closed for stream in streams)
            assert all(client.is_closed() for client in clients)
            print("PASS: real student comparison HTTP/SSE/CSRF/SQLite/recovery")
            process = await asyncio.create_subprocess_exec(
                "node",
                "tests/check_s5_analysis_browser.mjs",
                str(listener.getsockname()[1]),
                str(saved.id),
                cwd=ROOT,
            )
            try:
                async with asyncio.timeout(90):
                    assert await process.wait() == 0
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert len(calls) == 14
            assert len([call for call in calls if "text" in call]) == 3
            assert all(client.is_closed() for client in clients)
            assert not active_calls
            async with AsyncSessionLocal() as db:
                from src.models import (
                    QuestionAnalysis,
                    SessionFeedbackReport,
                    SessionSummary,
                )

                for model in (
                    Session,
                    Message,
                    QuestionAnalysis,
                    SessionFeedbackReport,
                    SessionSummary,
                ):
                    assert (
                        await db.scalar(select(func.count()).select_from(model))
                        == 0
                    )
                assert (
                    await db.scalar(
                        select(func.count()).select_from(ComparisonRun)
                    )
                    == 6
                )
            process = await asyncio.create_subprocess_exec(
                "node",
                "tests/check_s5_mentor_browser.mjs",
                str(listener.getsockname()[1]),
                cwd=ROOT,
            )
            try:
                async with asyncio.timeout(90):
                    assert await process.wait() == 0
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            mentor_calls = calls[14:]
            assert len(mentor_calls) == 6
            assert all(
                "stream" not in call
                and call["text"]["format"]["name"] == "MentorOutput"
                for call in mentor_calls
            )
            async with AsyncSessionLocal() as db:
                for model in (Session, Message):
                    assert (
                        await db.scalar(select(func.count()).select_from(model))
                        == 0
                    )
                assert (
                    await db.scalar(select(func.count()).select_from(Scenario))
                    == 1
                )
                assert (await db.get(Scenario, saved.id)).config_version == 1
                runs = (
                    await db.scalars(
                        select(GenerationRun)
                        .where(GenerationRun.operation == "mentor")
                        .order_by(
                            GenerationRun.started_at, GenerationRun.panel_id
                        )
                    )
                ).all()
                assert [run.status for run in runs] == ["completed"] * 4 + [
                    "cancelled"
                ] * 2
                assert all(run.session_id is None for run in runs)
                assert all(
                    run.mentor_reason_summary == "PRIVATE MENTOR REASON"
                    for run in runs[:4]
                )
                ledger = (
                    await db.scalars(
                        select(ApiUsageLog).where(ApiUsageLog.role == "mentor")
                    )
                ).all()
                assert len(ledger) == 6 and all(
                    entry.status != "running"
                    and entry.usage_scope == "comparison"
                    and entry.attempt_no == 1
                    for entry in ledger
                )
            assert not active_calls and all(
                client.is_closed() for client in clients
            )
            print(
                "PASS: real mentor comparison HTTP/SSE/CSRF/SQLite/final-only"
            )
        finally:
            server.should_exit = True
            await task
            socket.socket.connect = original_connect
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
