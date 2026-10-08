"""Standalone localhost/real SDK check; no external sockets or paid calls.

Run: uv run --frozen python tests/check_student_live.py
"""

import asyncio
import base64
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
(ROOT / ".pytest_cache").mkdir(exist_ok=True)


async def check():
    with tempfile.TemporaryDirectory(dir=ROOT / ".pytest_cache") as directory:
        os.environ.update(
            TESTING="true",
            OPENAI_API_KEY="test-only",
            SESSION_SECRET="live-test",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/live.db",
        )
        import httpx
        import uvicorn
        from itsdangerous import TimestampSigner
        from openai import AsyncOpenAI
        from sqlalchemy import select
        from starlette_csrf import CSRFMiddleware

        from src.config import config
        from src.db.connection import AsyncSessionLocal
        from src.db.migrations.migrate import run_all_migrations
        from src.main import app
        from src.models import (
            AnalysisFramework,
            GenerationRun,
            PromptTemplate,
            Scenario,
            ScenarioGroup,
            Session,
            User,
            UserGroup,
        )
        from src.services import base

        await run_all_migrations()
        async with AsyncSessionLocal() as db:
            group = UserGroup(name="Live group")
            template = PromptTemplate(
                bot_type="student",
                template_name="Live",
                template_text="Student context: {prompt}",
            )
            mentor = PromptTemplate(
                bot_type="tutor",
                template_name="Enabled mentor",
                template_text="Coach this completed turn: {prompt}",
            )
            framework = AnalysisFramework(name="Live", labels_json='["A","B"]')
            db.add_all([group, template, mentor, framework])
            await db.flush()
            owner = User(
                username="live-owner", nickname="Teacher", group_id=group.id
            )
            scenario = Scenario(
                title="Live",
                prompt="Internal instruction",
                problem_situation="Public problem",
                framework_id=framework.id,
                student_template_id=template.id,
                tutor_template_id=mentor.id,
                tutor_sensitivity="high",
            )
            db.add_all([owner, scenario])
            await db.flush()
            session = Session(teacher_id=owner.id, scenario_id=scenario.id)
            db.add_all(
                [
                    session,
                    ScenarioGroup(scenario_id=scenario.id, group_id=group.id),
                ]
            )
            await db.commit()
            session_id, owner_id = session.id, owner.id

        release = asyncio.Event()
        calls, owned_clients = [], []

        class Upstream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"event: response.output_text.delta\ndata: " + json.dumps(
                    {
                        "type": "response.output_text.delta",
                        "delta": "First body",
                        "item_id": "msg",
                        "output_index": 0,
                        "content_index": 0,
                        "sequence_number": 1,
                        "logprobs": [],
                    }
                ).encode() + b"\n\n"
                await release.wait()
                yield b"event: response.completed\ndata: " + json.dumps(
                    {
                        "type": "response.completed",
                        "sequence_number": 2,
                        "response": {
                            "id": "resp",
                            "object": "response",
                            "created_at": 1,
                            "status": "completed",
                            "model": "gpt-5-mini",
                            "output": [
                                {
                                    "id": "msg",
                                    "type": "message",
                                    "role": "assistant",
                                    "status": "completed",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "Saved body",
                                            "annotations": [],
                                            "logprobs": [],
                                        }
                                    ],
                                }
                            ],
                            "usage": {
                                "input_tokens": 10,
                                "output_tokens": 2,
                                "total_tokens": 12,
                            },
                        },
                    }
                ).encode() + b"\n\n"

        async def upstream(request):
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=Upstream(),
            )

        def client_factory(**kwargs):
            assert kwargs["max_retries"] == 0
            sdk = AsyncOpenAI(
                **kwargs,
                http_client=httpx.AsyncClient(
                    transport=httpx.MockTransport(upstream)
                ),
            )
            owned_clients.append(sdk)
            return sdk

        base.AsyncOpenAI = client_factory
        original_connect = socket.socket.connect

        def localhost_only(sock, address):
            assert address[0] in {
                "127.0.0.1",
                "::1",
            }, "External network forbidden"
            return original_connect(sock, address)

        socket.socket.connect = localhost_only
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                CSRFMiddleware(
                    app,
                    secret=config.SESSION_SECRET,
                    cookie_name="csrftoken",
                    header_name="x-csrf-token",
                ),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    await asyncio.sleep(0.01)
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", timeout=30
            ) as http:
                cookie = (
                    TimestampSigner(config.SESSION_SECRET)
                    .sign(
                        base64.b64encode(
                            json.dumps({"user_id": owner_id}).encode()
                        )
                    )
                    .decode()
                )
                http.cookies.set("session_id", cookie)
                await http.get("/health")
                payload = {
                    "request_id": str(uuid4()),
                    "content": "Live question",
                }
                path = f"/sessions/{session_id}/turns/stream"
                denied = await http.post(path, json=payload)
                assert denied.status_code == 403 and len(calls) == 0
                headers = {"x-csrf-token": http.cookies["csrftoken"]}
                async with http.stream(
                    "POST", path, json=payload, headers=headers
                ) as response:
                    assert response.status_code == 200
                    lines = response.aiter_lines()

                    async def next_frame():
                        data = []
                        while True:
                            line = await anext(lines)
                            if not line and data:
                                return data
                            if line:
                                data.append(line)

                    async with asyncio.timeout(30):
                        accepted = await next_frame()
                        delta = await next_frame()
                    assert accepted[0] == "event: run.accepted"
                    accepted_data = json.loads(accepted[1][6:])
                    assert delta[0] == "event: output.delta"
                    assert json.loads(delta[1][6:])["text"] == "First body"
                    assert (
                        not release.is_set()
                    ), "Delta arrived before completion"
                    snapshot = await http.get(
                        f"/runs/{accepted_data['run_id']}"
                    )
                    assert snapshot.json()["status"] == "running"
                    assert snapshot.json()["message"] is None
                    release.set()
                    completed = await next_frame()
                    assert completed[0] == "event: output.completed"
                    assert (
                        json.loads(completed[1][6:])["message"]["content"]
                        == "Saved body"
                    )
                replay = await http.post(path, json=payload, headers=headers)
                assert (
                    replay.json()["status"] == "completed" and len(calls) == 1
                )
                assert calls[0]["stream"] is True
                assert all(sdk.is_closed() for sdk in owned_clients)
                async with AsyncSessionLocal() as db:
                    run = (await db.scalars(select(GenerationRun))).one()
                    assert (
                        run.started_at <= run.first_output_at <= run.finished_at
                    )
                    timings = {
                        "first_output_ms": (
                            run.first_output_at - run.started_at
                        ).total_seconds()
                        * 1000,
                        "completed_ms": (
                            run.finished_at - run.started_at
                        ).total_seconds()
                        * 1000,
                    }
                print(
                    json.dumps(
                        {
                            "student_calls": len(calls),
                            "mentor_calls_in_student_path": 0,
                            "misconception_calls": 0,
                            **timings,
                        }
                    )
                )
                print(
                    "PASS: real localhost incremental delta, active CSRF, "
                    "real SDK parsing/max_retries=0/close, "
                    "durable replay; 1 call"
                )
        finally:
            release.set()
            server.should_exit = True
            await task
            listener.close()
            socket.socket.connect = original_connect


if __name__ == "__main__":
    asyncio.run(check())
