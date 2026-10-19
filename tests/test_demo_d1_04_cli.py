"""Run real module entrypoints with forbidden side effects replaced by spies."""

import json

import pytest
from test_demo_config import DEMO_ENV, run_python

MODULES = (
    "src.db.seed",
    "src.db.init_schema",
    "src.db.rotate_default_passwords",
    "src.db.convert_scenarios",
    "src.db.s2_cutover",
    "src.db.s6_cutover",
    "src.db.s6_credentials",
    "src.db.migrations.migrate_015",
)


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize("flag", ["--dry-run", "--apply"])
def test_demo_cli_blocks_before_files_db_parser_or_testing(module, flag):
    result = run_python(
        f"""
import argparse
import builtins
import os
import runpy
import sqlite3
import sys
from unittest.mock import patch
import sqlalchemy.ext.asyncio

def forbidden(*args, **kwargs):
    raise AssertionError("CLI side effect before mode guard")
sys.argv = [{module!r}, {flag!r}, "--database", "/nonexistent/never-open.db"]
with patch("builtins.open", forbidden), patch("pathlib.Path.open", forbidden), patch("sqlite3.connect", forbidden), patch("sqlalchemy.ext.asyncio.create_async_engine", forbidden), patch.object(argparse.ArgumentParser, "__init__", forbidden):
    try:
        runpy.run_module({module!r}, run_name="__main__")
    except SystemExit as error:
        assert error.code == 1
    else:
        raise AssertionError("CLI did not exit")
assert os.environ.get("TESTING") is None
""",
        env={"APP_MODE": "demo"},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "code": "DEMO_ACTION_FORBIDDEN",
        "exit_status": 1,
    }


@pytest.mark.parametrize("module", MODULES)
def test_normal_cli_path_is_reachable(module):
    result = run_python(
        f"""
import argparse
import asyncio
import runpy
import sqlite3
import sys
from unittest.mock import patch
class Reached(Exception):
    pass
def reached(*args, **kwargs):
    for arg in args:
        if hasattr(arg, "close"):
            arg.close()
    raise Reached()
sys.argv = [{module!r}, "--help"]
with patch.object(argparse.ArgumentParser, "parse_args", reached), patch("asyncio.run", reached), patch("sqlite3.connect", reached):
    try:
        runpy.run_module({module!r}, run_name="__main__")
    except Reached:
        pass
    else:
        raise AssertionError("normal CLI unreachable")
""",
        env={
            "APP_MODE": "normal",
            "TESTING": "true",
            "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        },
    )
    assert result.returncode == 0, result.stderr


def test_forbidden_demo_environment_still_reports_cli_action_first():
    result = run_python(
        """
import runpy
runpy.run_module("src.db.seed", run_name="__main__")
""",
        env=DEMO_ENV
        | {"TESTING": "true", "DATABASE_URL": "sqlite:///forbidden.db"},
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "DEMO_ACTION_FORBIDDEN"
    assert "DEMO_CONFIGURATION_INVALID" not in result.stderr
