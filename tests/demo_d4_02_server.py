"""Real app server for the D4-02 browser checks (#204).

python tests/demo_d4_02_server.py [--normal]

Demo mode first prints `RECEIPT <json>` (the #197 seed build receipt), then
`READY <port>` once uvicorn serves on 127.0.0.1; it exits when stdin
closes. A stdin line `ADVANCE <seconds>` moves the demo registry clock
forward (the idle TTL is at least 300 s) and answers `ADVANCED <seconds>`. Demo mode runs the real FastAPI app with TESTING=false and the
demo session/CSRF security; --normal runs the same app in normal mode
(the demo paths must 404 there). Only loopback sockets are allowed and the
external AI SDK constructors fail.
"""

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INHERITED = ("DEMO_", "OPENAI_", "ANTHROPIC_", "AZURE_", "GOOGLE_", "GEMINI_")
RESET = {
    "APP_MODE",
    "DATABASE_URL",
    "TESTING",
    "SESSION_SECRET",
    "ENV",
    "BOOTSTRAP_ADMIN_ON_STARTUP",
    "PROVIDER_SECRET_ENCRYPTION_KEY",
    "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
}


def configure(normal, tmp, port, other_port):
    for key in list(os.environ):
        if key.startswith(INHERITED) or key in RESET:
            del os.environ[key]
    os.environ.update(
        ENV="development",
        TESTING="false",
        SESSION_SECRET=secrets.token_urlsafe(48),
    )
    if normal:
        os.environ.update(
            APP_MODE="normal",
            DATABASE_URL=f"sqlite+aiosqlite:///{tmp / 'normal.db'}",
        )
        return
    # Different hosts: loopback HTTP is accepted in development only.
    os.environ.update(
        APP_MODE="demo",
        DEMO_DATA_ROOT=str(tmp / "demo"),
        DEMO_SEED_DIR=str(tmp / "seed"),
        DEMO_ORIGIN=f"http://127.0.0.1:{port}",
        NORMAL_ORIGIN=f"http://localhost:{other_port}",
        DEMO_SESSION_SECRET=secrets.token_urlsafe(48),
        DEMO_CSRF_SECRET=secrets.token_urlsafe(48),
    )


def guard():
    import anthropic
    import openai
    from google import genai

    connect = socket.socket.connect

    def localhost_only(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            assert address[0] in {
                "127.0.0.1",
                "::1",
            }, "External network forbidden"
        return connect(sock, address)

    def refused(*args, **kwargs):
        raise AssertionError("External AI SDK forbidden")

    socket.socket.connect = localhost_only
    for owner in (
        openai.OpenAI,
        openai.AsyncOpenAI,
        anthropic.Anthropic,
        anthropic.AsyncAnthropic,
        genai.Client,
    ):
        owner.__init__ = refused


class TestClock:
    """Brief substitution 2: a test clock for expiry; visible in evidence."""

    offset = 0.0

    def monotonic(self):
        return time.monotonic() + TestClock.offset

    def utcnow(self):
        return datetime.now(timezone.utc) + timedelta(seconds=TestClock.offset)


async def serve(listener, normal):
    import uvicorn

    from src import main
    from src.demo import visitors

    visitors.SystemClock = TestClock
    if not normal:
        # The #197 product seed, built by its own CLI into DEMO_SEED_DIR,
        # which src.main.demo_seed loads (#194 S4).
        seed_dir = Path(os.environ["DEMO_SEED_DIR"])
        built = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.demo.seed",
                "build",
                "--out",
                str(seed_dir),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if built.returncode != 0:
            raise SystemExit(f"seed CLI exited {built.returncode}")
        receipt = json.loads(built.stdout.strip().splitlines()[-1])
        # Redact only the disposable directory; every other field is as built.
        receipt["command"] = receipt["command"].replace(
            str(seed_dir), "<tmp>/seed"
        )
        print("RECEIPT", json.dumps(receipt, sort_keys=True), flush=True)
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        if task.done():
            await task
            raise SystemExit("server did not start")
        await asyncio.sleep(0.05)
    print("READY", listener.getsockname()[1], flush=True)
    loop = asyncio.get_running_loop()
    while line := await loop.run_in_executor(None, sys.stdin.readline):
        command, _, seconds = line.partition(" ")
        if command == "ADVANCE":
            TestClock.offset += float(seconds)
            print("ADVANCED", float(seconds), flush=True)
    server.should_exit = True
    await task


def main():
    normal = sys.argv[1:] == ["--normal"]
    if sys.argv[1:] not in ([], ["--normal"]):
        raise SystemExit("usage: demo_d4_02_server.py [--normal]")
    listener, other = socket.socket(), socket.socket()
    listener.bind(("127.0.0.1", 0))
    other.bind(("127.0.0.1", 0))
    port, other_port = listener.getsockname()[1], other.getsockname()[1]
    other.close()
    with tempfile.TemporaryDirectory() as tmp:
        configure(normal, Path(tmp), port, other_port)
        sys.path.insert(0, str(ROOT))
        os.chdir(ROOT)
        guard()
        asyncio.run(serve(listener, normal))


if __name__ == "__main__":
    main()
