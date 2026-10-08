"""Run the dependency-free JavaScript transport checks with pytest."""

import subprocess
from pathlib import Path


def test_student_sse_contract():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", "--test", "tests/student_sse.test.mjs"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
