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
            ComparisonRun,
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
        draft["config"]["mentor"].update(
            mode="manual",
            name="Mentor",
            behavior_instruction="Coach the teacher",
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
            def __init__(self):
                self.closed = False

            async def __aiter__(self):
                yield sse(
                    "response.output_text.delta",
                    delta="Live partial",
                    sequence_number=0,
                    item_id="msg",
                    output_index=0,
                    content_index=0,
                )
                await asyncio.sleep(0.1)
                yield sse(
                    "response.completed",
                    response=response_body(
                        'Live final <img src=x onerror="window.s5xss=1">'
                    ),
                    sequence_number=1,
                )

            async def aclose(self):
                self.closed = True

        async def upstream(request):
            body = json.loads(request.content)
            calls.append(body)
            if (
                body.get("text", {}).get("format", {}).get("name")
                == "MentorOutput"
            ):
                reply = dict(
                    should_intervene=True,
                    feedback='Live coaching <img src=x onerror="window.s5xss=1">',
                    reason_summary="PRIVATE MENTOR REASON",
                )
                return httpx2.Response(
                    200, json=response_body(json.dumps(reply))
                )
            if "text" in body:
                reply = analysis_reply(prompt_inputs(body)["messages"])
                return httpx2.Response(
                    200, json=response_body(json.dumps(reply))
                )
            stream = MockStream()
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
                "tests/check_conversation_browser.mjs",
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
            async with AsyncSessionLocal() as db:
                from src.models import ComparisonConversation

                parent = (
                    await db.scalars(select(ComparisonConversation))
                ).one()
                assert len(parent.state_json["pairs"]) == 2
                assert parent.state_json["dialogue_status"] == "ended"
                assert (await db.get(Scenario, saved.id)).config_version == 2
                assert (
                    await db.scalar(select(func.count()).select_from(Session))
                    == 0
                )
                assert (
                    await db.scalar(select(func.count()).select_from(Message))
                    == 0
                )
                children = (await db.scalars(select(ComparisonRun))).all()
                assert len(children) == 4 and all(
                    child.conversation_id == parent.id for child in children
                )
                assert all(
                    child.expires_at == parent.expires_at for child in children
                )
            assert len(calls) == 8
            assert calls[0]["input"] == calls[1]["input"]
            assert all(stream.closed for stream in streams)
            assert all(client.is_closed() for client in clients)
            assert not active_calls
            print("PASS: saved conversation HTTP/SSE/browser/SQLite")
        finally:
            server.should_exit = True
            await task
            socket.socket.connect = original_connect
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
