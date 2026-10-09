"""Real authoring HTTP/CSRF/SQLite browser rehearsal, with no external sockets."""

import asyncio
import base64
import json
import os
import re
import socket
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
(ROOT / ".pytest_cache").mkdir(exist_ok=True)


async def check():
    with tempfile.TemporaryDirectory(dir=ROOT / ".pytest_cache") as directory:
        os.environ.update(
            TESTING="true",
            SESSION_SECRET="s2-browser-only",
            OPENAI_API_KEY="test-only",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/draft.db",
            PROVIDER_SECRET_ENCRYPTION_KEY=base64.b64encode(
                bytes(range(32))
            ).decode(),
            PROVIDER_SECRET_ENCRYPTION_KEY_VERSION="browser-v1",
        )
        import uvicorn
        from starlette_csrf import CSRFMiddleware

        from src.config import config
        from src.db.connection import AsyncSessionLocal
        from src.db.convert_scenarios import convert_copy
        from src.db.migrations.migrate import run_all_migrations
        from src.main import app
        from src.models import (
            AnalysisFramework,
            Message,
            ModelConfig,
            PromptTemplate,
            ProviderConnection,
            QuestionAnalysis,
            Scenario,
            Session,
            SessionSummary,
            User,
            UserGroup,
        )
        from src.models.model_config import AppSetting
        from src.models.scenario_group import ScenarioGroup
        from src.services.model_capabilities import capabilities
        from src.services.model_verification import ROLE_CONTRACT_VERSIONS
        from src.services.provider_secrets import encrypt_key

        await run_all_migrations()
        async with AsyncSessionLocal() as db:
            group = UserGroup(name="Draft browser group")
            db.add(group)
            await db.flush()
            for username, role in (
                ("draft_admin", "admin"),
                ("draft_teacher", "teacher"),
            ):
                user = User(
                    username=username,
                    nickname=username,
                    role=role,
                    group_id=group.id,
                )
                user.set_password("s2-browser-password")
                db.add(user)
            db.add(
                ModelConfig(
                    provider_connection_id=1,
                    model_id="gpt-5-mini",
                    display_name="Draft model",
                    default_options_json={
                        "max_output_tokens": 900,
                        "reasoning": {"effort": "medium"},
                    },
                )
            )
            connection = await db.get(ProviderConnection, 1)
            connection.encrypted_key, connection.nonce = encrypt_key(
                connection, "browser-synthetic-key", 1
            )
            connection.encryption_key_version = "browser-v1"
            connection.masked_hint = "****test"
            connection.credential_revision = 1
            connection.enabled = True
            definition = capabilities("openai", "gpt-5.2")["definition_version"]
            model = ModelConfig(
                provider_connection_id=1,
                model_id="gpt-5.2",
                display_name="Verified browser model",
                enabled=True,
                capability_definition_version=definition,
                default_options_json={
                    "max_output_tokens": 1600,
                    "reasoning": {"effort": "none"},
                    "temperature": 0.7,
                },
                verification_state={
                    role: dict(
                        status="succeeded",
                        credential_revision=1,
                        connection_version=connection.connection_version,
                        capability_definition_version=definition,
                        role_contract_version=ROLE_CONTRACT_VERSIONS[role],
                    )
                    for role in ("student", "mentor", "analysis")
                },
            )
            db.add(model)
            await db.flush()
            setting = await db.get(AppSetting, 1)
            setting.student_model_config_id = model.id
            setting.analysis_model_config_id = model.id
            review_config = json.loads(
                (ROOT / "tests/fixtures/s2_draft.json").read_text()
            )["config"]
            selection = dict(
                model_config_id=model.id,
                provider_connection_id=1,
                provider="openai",
                model_id=model.model_id,
                options=model.default_options_json,
            )
            review_config["problem"].update(
                public_text="", learning_objective="분수 비교"
            )
            review_config["student"].update(
                name="민수",
                misconception="분모 크기",
                behavior_instruction="생각을 말한다",
                resolved_model_config=selection,
            )
            review_config["analysis"].update(
                context="질문",
                expected_understanding="같은 전체",
                instruction="이유",
                resolved_model_config=selection,
            )
            db.add(
                Scenario(
                    title="Live conversion review",
                    status="draft",
                    config_json=review_config,
                    review_required=True,
                    review_reasons=[
                        dict(
                            path="problem.public_text",
                            code="required",
                            message="공개 문제 상황 보완 필요",
                            blocking=True,
                        ),
                        dict(
                            path="student.public_profile",
                            code="privacy_change",
                            message="공개 소개 검토",
                            blocking=False,
                        ),
                    ],
                    conversion_provenance_json=[
                        dict(
                            field="학생 지시",
                            source="PRIVATE-LEGACY",
                            target="생각을 말한다",
                        )
                    ],
                )
            )
            await db.commit()

            original = PromptTemplate(
                bot_type="student",
                template_name="Conversion student",
                template_text="{scenario_title}: {student_profile}; {prompt}; {{literal}}",
            )
            rubric = AnalysisFramework(
                name="Conversion rubric",
                description="Original rubric metadata",
                labels_json=json.dumps(
                    [
                        dict(
                            name="Explain",
                            criteria="PRIVATE RUBRIC criterion",
                            level="high",
                        ),
                        dict(
                            name="Recall",
                            criteria="Check remembered facts",
                            level="low",
                        ),
                    ]
                ),
            )
            db.add_all([original, rubric])
            await db.flush()
            converted = Scenario(
                title="Live real conversion",
                prompt="INTERNAL_CONVERSION_MISCONCEPTION",
                student_name="민수",
                student_profile="INTERNAL_CONVERSION_SENTINEL " + "x" * 50001,
                problem_situation=None,
                student_template_id=original.id,
                framework_id=rubric.id,
                chat_temperature=0.8,
                video_url="PRIVATE CONVERSION VIDEO",
                video_transcript="PRIVATE CONVERSION TRANSCRIPT",
            )
            db.add(converted)
            await db.flush()
            db.add(ScenarioGroup(scenario_id=converted.id, group_id=group.id))
            from sqlalchemy import select

            teacher = await db.scalar(
                select(User).where(User.username == "draft_teacher")
            )
            history = Session(
                scenario_id=converted.id,
                teacher_id=teacher.id,
                started_at=datetime(2026, 1, 1),
                ended_at=datetime(2026, 1, 2),
            )
            db.add(history)
            await db.flush()
            question = Message(
                session_id=history.id,
                role="teacher",
                content="Original history question <script>window.historyLeaked=true</script>",
            )
            db.add_all(
                [
                    question,
                    Message(
                        session_id=history.id,
                        role="student",
                        content="Original history answer",
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    QuestionAnalysis(
                        message_id=question.id,
                        label="Original label",
                        grade="우수",
                        confidence=0.9,
                        meta_json='{"summary":"Original history evidence"}',
                    ),
                    SessionSummary(
                        session_id=history.id,
                        distribution_json='{"Original label":1}',
                        feedback="Original history feedback",
                    ),
                ]
            )
            await db.commit()
            results = await convert_copy(
                Path(directory) / "draft.db",
                ROOT / "tests/fixtures/s2_legacy_effective.json",
                Path(directory) / "conversion_source.json",
                Path(directory) / "conversion_manifest.json",
                apply=True,
            )
            assert dict(id=converted.id, status="converted") in results

        original_connect = socket.socket.connect

        def localhost_only(sock, address):
            if not isinstance(address, tuple) or address[0] != "127.0.0.1":
                raise AssertionError(
                    "Browser rehearsal must not access external services"
                )
            return original_connect(sock, address)

        socket.socket.connect = localhost_only
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(
                CSRFMiddleware(
                    app,
                    secret=config.SESSION_SECRET,
                    exempt_urls=[re.compile(r"/login")],
                    header_name="x-csrf-token",
                ),
                host="127.0.0.1",
                port=listener.getsockname()[1],
                log_level="warning",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(30):
                while not server.started:
                    await asyncio.sleep(0.01)
            process = await asyncio.create_subprocess_exec(
                "node",
                "tests/check_s2_draft_browser.mjs",
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
            print("PASS: real draft form/HTTP/CSRF/SQLite/reopen")
        finally:
            server.should_exit = True
            await task
            socket.socket.connect = original_connect


if __name__ == "__main__":
    asyncio.run(check())
