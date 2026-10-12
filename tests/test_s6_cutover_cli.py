"""S6 command-line status, private failure reports and interruption recovery."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def test_cli_has_explicit_paths_safe_reports_and_nonzero_failure(tmp_path):
    command = [sys.executable, "-m", "src.db.s6_cutover"]
    missing = subprocess.run(command, capture_output=True, text=True)
    assert missing.returncode == 2
    assert "--source-copy" in missing.stderr and "--workspace" in missing.stderr
    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    workspace = tmp_path / "workspace"
    arguments = ["--source-copy", str(source), "--workspace", str(workspace)]
    dry_run = subprocess.run(
        command + arguments, capture_output=True, text=True
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert json.loads(dry_run.stdout)["status"] == "dry_run"
    applied = subprocess.run(
        command + arguments + ["--apply"], capture_output=True, text=True
    )
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["status"] == "ready"
    assert json.loads(applied.stdout)["release_blocked"] is True
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SYNTHETIC_PRIVATE_ERROR_SENTINEL")
    broken.chmod(0o600)
    failed_workspace = tmp_path / "failed"
    failure = subprocess.run(
        command
        + [
            "--source-copy",
            str(broken),
            "--workspace",
            str(failed_workspace),
            "--apply",
        ],
        capture_output=True,
        text=True,
    )
    assert failure.returncode == 2
    result = json.loads(failure.stdout)
    assert result == {
        "status": "failed",
        "release_blocked": True,
        "error_code": "invalid_input_or_evidence",
    }
    assert (
        "SYNTHETIC_PRIVATE_ERROR_SENTINEL"
        not in failure.stdout + failure.stderr
    )
    assert json.loads((failed_workspace / "failure.json").read_text()) == result


@pytest.mark.parametrize(
    "stage", ["candidate.db", "report.json", "receipt.json"]
)
async def test_interruption_never_overwrites_partial_artifacts_and_new_workspace_can_retry(
    tmp_path, monkeypatch, stage
):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    original = source.read_bytes()
    workspace = tmp_path / "interrupted"
    open_file = os.open

    def interrupt(path, flags, *args, **kwargs):
        if Path(path).name == stage:
            raise OSError("SYNTHETIC_PRIVATE_INTERRUPTION")
        return open_file(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", interrupt)
        with pytest.raises(OSError, match="SYNTHETIC_PRIVATE_INTERRUPTION"):
            await rehearse(source, workspace, apply=True)
    before = {
        p.name: p.read_bytes()
        for p in workspace.iterdir()
        if p.suffix in {".db", ".json"}
    }
    with pytest.raises(ValueError, match="interrupted|incomplete|isolated"):
        await rehearse(source, workspace, apply=True)
    assert all(
        (workspace / name).read_bytes() == value
        for name, value in before.items()
    )
    assert source.read_bytes() == original
    assert (await rehearse(source, tmp_path / "retry", apply=True))[
        "status"
    ] == "ready"


async def test_cancellation_leaves_safe_failure_evidence_and_refuses_resume(
    tmp_path, monkeypatch
):
    from src.db.s6_cutover import rehearse

    source = tmp_path / "source.db"
    with sqlite3.connect(source):
        pass
    source.chmod(0o600)
    workspace = tmp_path / "cancelled"
    open_file = os.open

    def cancel(path, flags, *args, **kwargs):
        if Path(path).name == "report.json":
            raise asyncio.CancelledError("SYNTHETIC_PRIVATE_CANCELLATION")
        return open_file(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", cancel)
        with pytest.raises(asyncio.CancelledError):
            await rehearse(source, workspace, apply=True)
    evidence = (workspace / "failure.json").read_text()
    assert json.loads(evidence)["status"] == "failed"
    assert json.loads(evidence)["release_blocked"] is True
    assert "SYNTHETIC_PRIVATE_CANCELLATION" not in evidence
    with pytest.raises(ValueError, match="interrupted_workspace"):
        await rehearse(source, workspace, apply=True)
