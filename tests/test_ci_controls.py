"""Run dependency-free workflow and browser-runner contract checks."""

import subprocess
from pathlib import Path


def test_ci_controls_contract():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", "tests/ci_controls.test.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
