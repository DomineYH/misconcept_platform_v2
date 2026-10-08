"""Real non-test startup/login with no provider or encryption keys."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("master", ["", "invalid-master"])
def test_keyless_startup_and_login(tmp_path, master):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "static").symlink_to(root / "static", target_is_directory=True)
    (tmp_path / "src").symlink_to(root / "src", target_is_directory=True)
    script = """
import asyncio, socket
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError("Network forbidden"))
import httpx
from src.main import app, lifespan
from src.db.connection import AsyncSessionLocal
from src.models import User
async def check():
    async with lifespan(app):
        async with AsyncSessionLocal() as db:
            admin = User(username="keyless_admin", nickname="Admin", role="admin")
            admin.set_password("startup-password")
            db.add(admin)
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/login")).status_code == 200
            assert (await client.post("/login", data={"username":"keyless_admin", "password":"startup-password"})).status_code == 303
            assert (await client.get("/admin/ai")).status_code == 200
            snapshot = await client.get("/admin/ai/state")
            assert snapshot.status_code == 200 and not snapshot.json()["master_key_available"]
            denied = await client.post("/admin/ai/providers/openai/key", json={"expected_version":1,"current_password":"startup-password","api_key":"no-call-test-key"}, headers={"x-csrf-token":client.cookies["csrftoken"]})
            assert denied.status_code == 503
    print("KEYLESS PASS")
asyncio.run(check())
"""
    env = {
        **os.environ,
        "PYTHONPATH": str(root),
        "TESTING": "false",
        "OPENAI_API_KEY": "",
        "SESSION_SECRET": "a4eb7299f91c73348593d228f28abed97fb7fa28",
        "PROVIDER_SECRET_ENCRYPTION_KEY": master,
        "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION": "v1",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}",
        "BOOTSTRAP_ADMIN_ON_STARTUP": "false",
        "ENV": "development",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "KEYLESS PASS" in result.stdout
    env["SESSION_SECRET"] = "insecure"
    invalid = subprocess.run(
        [sys.executable, "-c", "import src.main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert invalid.returncode != 0
    assert "SESSION_SECRET" in invalid.stderr
