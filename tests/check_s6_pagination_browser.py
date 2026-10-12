"""Real administrator lists on migrated temporary SQLite, localhost only."""

import asyncio
import logging
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def check():
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(
            TESTING="true",
            SESSION_SECRET="s6-browser-only",
            OPENAI_API_KEY="test-only",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/lists.db",
        )
        import uvicorn
        from starlette_csrf import CSRFMiddleware

        from src.config import config
        from src.db.connection import AsyncSessionLocal, engine
        from src.db.migrations.migrate import run_all_migrations
        from src.main import app
        from src.models import Scenario, User, UserGroup

        logging.getLogger().setLevel(logging.WARNING)
        await run_all_migrations()
        async with AsyncSessionLocal() as db:
            group = UserGroup(id=1, name="Browser group")
            db.add(group)
            await db.flush()
            admin = User(
                id=41, username="admin", nickname="Admin", role="admin"
            )
            admin.set_password("s6-browser-password")
            db.add(admin)
            db.add_all(
                User(
                    id=i,
                    username=f"teacher_{i}",
                    nickname=f"Teacher {i}",
                    group_id=1,
                    password_hash=admin.password_hash,
                )
                for i in range(1, 41)
            )
            db.add_all(
                Scenario(
                    id=i,
                    title=f"Scenario {i}",
                    config_json={
                        "problem": {"public_text": "Synthetic problem"}
                    },
                )
                for i in range(1, 42)
            )
            await db.commit()

        original_connect = socket.socket.connect

        def localhost_only(sock, address):
            if not isinstance(address, tuple) or address[0] != "127.0.0.1":
                raise AssertionError("Browser rehearsal must stay on localhost")
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
                "tests/check_s6_pagination_browser.mjs",
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
            print("PASS: real admin pagination/HTTP/CSRF/SQLite/delete")
        finally:
            server.should_exit = True
            await task
            socket.socket.connect = original_connect
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(check())
