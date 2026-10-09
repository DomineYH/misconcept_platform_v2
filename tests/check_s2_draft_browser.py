"""Real authoring HTTP/CSRF/SQLite browser rehearsal, with no external sockets."""

import asyncio
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
            SESSION_SECRET="s2-browser-only",
            OPENAI_API_KEY="test-only",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/draft.db",
        )
        import uvicorn
        from starlette_csrf import CSRFMiddleware

        from src.config import config
        from src.db.connection import AsyncSessionLocal
        from src.db.migrations.migrate import run_all_migrations
        from src.main import app
        from src.models import ModelConfig, User, UserGroup

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
            await db.commit()

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
