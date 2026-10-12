"""Offline S6 report format and runner; never imports product configuration."""

import argparse
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ("completed", "failed", "refused", "cancelled")


def summarize(rows):
    counts = Counter(row["status"] for row in rows)
    metrics = sorted({key for row in rows for key in row["durations_ms"]})
    distributions = {}
    for status in OUTCOMES:
        group = [row for row in rows if row["status"] == status]
        distributions[status] = {}
        for metric in metrics:
            values = sorted(
                row["durations_ms"][metric]
                for row in group
                if row["durations_ms"].get(metric) is not None
            )
            distributions[status][metric] = dict(
                known=len(values),
                unknown=len(group) - len(values),
                median_ms=median(values) if values else None,
                p95_ms=(
                    values[math.ceil(0.95 * len(values)) - 1]
                    if values
                    else None
                ),
            )
    return dict(
        counts={
            "attempted": len(rows),
            "admitted": len(rows) - counts["refused"],
            **{status: counts[status] for status in OUTCOMES},
        },
        distributions=distributions,
    )


def write_sample(sample):
    """Only explicitly constructed, payload-free samples enter this format."""
    output = os.environ.get("S6_MEASUREMENT_SAMPLES")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(sample, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    with tempfile.TemporaryDirectory() as temporary:
        samples = Path(temporary) / "samples.jsonl"
        env = {
            **os.environ,
            "TESTING": "true",
            "S6_MEASUREMENT_SAMPLES": str(samples),
            "S6_MEASUREMENT_REPEATS": "6",
        }
        commands = [
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_s6_workloads.py",
                "tests/test_s3_load.py",
                "tests/test_student_failures.py",
                "tests/test_comparison_streaming.py",
            ],
            [sys.executable, "tests/check_student_live.py", "--measure"],
        ]
        checks = []
        for command in commands:
            result = subprocess.run(
                command, cwd=ROOT, env=env, capture_output=True
            )
            checks.append(
                dict(command=command[1:], passed=result.returncode == 0)
            )
            # Do not copy assertion messages, HTTP responses or provider data.
            print(
                f"{'PASS' if result.returncode == 0 else 'FAIL'} {command[1:]}"
            )
        rows = (
            [json.loads(line) for line in samples.read_text().splitlines()]
            if samples.exists()
            else []
        )
        measured = [row for row in rows if row["repeat"] != 0]
        expected = {
            name: 6
            for name in (
                "student",
                "student_browser",
                "mentor_final",
                "analysis_final",
                "admin_read",
                "admin_save",
                "comparison_failed",
                "comparison_cancelled",
            )
        }
        expected.update(
            {
                f"capacity_{capacity}_{order}": 6 * 23
                for capacity in ("default", "24")
                for order in ("classroom_first", "comparison_first")
            }
        )
        observed = Counter(row["workload"] for row in rows)
        coverage = {
            name: dict(
                expected_samples=count,
                observed_samples=observed[name],
                missing_samples=max(0, count - observed[name]),
            )
            for name, count in expected.items()
        }
        complete = all(
            observed[name] == count for name, count in expected.items()
        )
        workloads = sorted(expected)
        disk = shutil.disk_usage(ROOT)
        report = dict(
            schema_version=1,
            synthetic=True,
            created_at=datetime.now(timezone.utc).isoformat(),
            environment=dict(
                os=platform.platform(),
                cpu=platform.processor() or "unknown",
                architecture=platform.machine(),
                cpu_count=os.cpu_count(),
                memory_bytes=os.sysconf("SC_PAGE_SIZE")
                * os.sysconf("SC_PHYS_PAGES"),
                disk_total_bytes=disk.total,
                disk_free_bytes=disk.free,
                python=platform.python_version(),
                versions={
                    name: version(name)
                    for name in (
                        "openai",
                        "anthropic",
                        "google-genai",
                        "sqlalchemy",
                    )
                },
                playwright=json.loads((ROOT / "package-lock.json").read_text())[
                    "packages"
                ]["node_modules/playwright"]["version"],
                revision=subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                dirty=bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=ROOT, text=True
                    )
                ),
                workers=1,
            ),
            conditions=dict(
                warmup=1,
                measured_repeats=5,
                small_sample=True,
                numeric_ci_gate=False,
            ),
            checks=checks,
            coverage=coverage,
            complete=complete,
            samples=rows,
            summaries={
                workload: summarize(
                    [row for row in measured if row["workload"] == workload]
                )
                for workload in workloads
            },
        )
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        )
        return 0 if complete and all(check["passed"] for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
