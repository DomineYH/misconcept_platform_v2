"""Real app server for the D4-02 browser checks (#204).

python tests/demo_d4_02_server.py [--normal]

Prints `READY <port>` once uvicorn serves on 127.0.0.1 and exits when
stdin closes. Demo mode runs the real FastAPI app with TESTING=false and
the demo session/CSRF security; --normal runs the same app in normal mode
(the demo paths must 404 there). Only loopback sockets are allowed and the
external AI SDK constructors fail.
"""

import asyncio
import os
import secrets
import socket
import sys
import tempfile
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


async def serve(listener, normal):
    import uvicorn

    from src import main

    if not normal:
        sys.path.insert(0, str(ROOT / "tests"))
        from test_demo_d1_04_app import install_seed, make_seed

        # ponytail: test-side seed until #197 load_seed is bound by #194 S4;
        # final G4 evidence must run with the product demo_seed().
        install_seed(main, await make_seed(Path(os.environ["DEMO_DATA_ROOT"])))
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="warning"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        if task.done():
            await task
            raise SystemExit("server did not start")
        await asyncio.sleep(0.05)
    print("READY", listener.getsockname()[1], flush=True)
    await asyncio.get_running_loop().run_in_executor(None, sys.stdin.read)
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
