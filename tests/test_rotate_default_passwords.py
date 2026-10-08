"""The maintenance CLI rotates only accounts with the disclosed password."""

import json
import os
import subprocess
import sys

from src.models import User


async def test_rotation_cli_dry_run_then_rotates_only_default_accounts(data):
    data.owner.set_password("00000000")
    data.admin.set_password("00000000")
    data.other.set_password("keep-this-password")
    await data.db.commit()
    before = {
        u.id: u.password_hash for u in (data.owner, data.admin, data.other)
    }
    env = dict(os.environ, DATABASE_URL=str(data.engine.url))

    def run(*args):
        result = subprocess.run(
            [sys.executable, "-m", "src.db.rotate_default_passwords", *args],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        assert result.stderr == ""
        return json.loads(result.stdout)

    targets = run("--dry-run")
    assert targets == [
        {"username": "owner", "nickname": "Owner"},
        {"username": "admin", "nickname": "Admin"},
    ]
    for user in (data.owner, data.admin, data.other):
        await data.db.refresh(user)
        assert user.password_hash == before[user.id]

    credentials = run()
    assert [c["username"] for c in credentials] == ["owner", "admin"]
    assert len({c["initial_password"] for c in credentials}) == 2
    async with data.factory() as db:
        for user, credential in zip((data.owner, data.admin), credentials):
            rotated = await db.get(User, user.id)
            password = credential["initial_password"]
            assert len(password) >= 8
            assert rotated.verify_password(password)
            assert not rotated.verify_password("00000000")
            assert password not in rotated.password_hash
        unchanged = await db.get(User, data.other.id)
        assert unchanged.password_hash == before[data.other.id]
        assert unchanged.verify_password("keep-this-password")
    assert run("--dry-run") == []
    assert run() == []


async def test_rotation_failure_rolls_back_without_printing_credentials(data):
    data.owner.set_password("00000000")
    data.other.password_hash = "malformed-hash"
    await data.db.commit()
    original = data.owner.password_hash
    result = subprocess.run(
        [sys.executable, "-m", "src.db.rotate_default_passwords"],
        env=dict(os.environ, DATABASE_URL=str(data.engine.url)),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    await data.db.refresh(data.owner)
    assert data.owner.password_hash == original
    assert data.owner.verify_password("00000000")
