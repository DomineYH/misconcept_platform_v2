"""AC-30: final schema and executable references, with narrow offline allowances."""

import ast
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from src.db.s6_verification import upgrade
from src.main import app

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {
    "analysis_framework",
    "prompt_template",
    "framework_id",
    "student_template_id",
    "tutor_template_id",
    "video_url",
    "video_transcript",
}
# Offline conversion/preservation and the official historical schema runner only.
HISTORICAL = {
    "src/db/migrations/migrate.py",
    "src/db/migrations/migrate_015.py",
    "src/db/s2_verification.py",
    "src/services/scenario_conversion.py",
    "src/services/legacy_conversion_values.py",
}
SDK_ADAPTERS = {
    "src/services/openai_generation.py",
    "src/services/openai_catalog.py",
    "src/services/anthropic_generation.py",
    "src/services/anthropic_catalog.py",
    "src/services/anthropic_errors.py",
    "src/services/google_client.py",
}
GENERATION_ADAPTERS = {
    "src/services/openai_generation.py",
    "src/services/anthropic_generation.py",
    "src/services/google_generation.py",
}


async def test_final_schema_has_no_retired_tables_columns_or_foreign_keys(
    tmp_path,
):
    database = tmp_path / "latest.db"
    await upgrade(database)
    with closing(sqlite3.connect(database)) as db:
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert not tables & RETIRED
        for table in tables:
            assert (
                not {
                    row[1]
                    for row in db.execute(f'PRAGMA table_info("{table}")')
                }
                & RETIRED
            )
            for foreign_key in db.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ):
                assert not set(foreign_key[2:5]) & RETIRED
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_registered_routes_have_no_retired_execution_or_management():
    for route in app.routes:
        path = getattr(route, "path", "")
        assert not re.search(
            r"/(frameworks?|prompts(?:-page)?|videos?)(?:/|$)", path
        )
        assert not path.startswith("/fixtures/")


def test_runtime_references_do_not_restore_legacy_joins_or_environment_keys():
    pattern = re.compile(
        r"\b(?:" + "|".join(sorted(RETIRED)) + r")\b", re.IGNORECASE
    )
    for path in (ROOT / "src").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text()
        if relative not in HISTORICAL:
            assert not pattern.search(source), relative
        if relative != "src/config.py":
            assert not re.search(
                r"\b(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY)\b",
                source,
            ), relative


def test_sdk_imports_and_generation_calls_stay_in_provider_adapters():
    for path in (ROOT / "src").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            if any(
                module.split(".")[0] in {"openai", "anthropic", "google"}
                for module in modules
            ):
                assert relative in SDK_ADAPTERS, relative
            if any(
                module.startswith(("legacy_models", "s2_analysis_invocations"))
                for module in modules
            ):
                raise AssertionError(relative)
            if isinstance(node, ast.Call) and isinstance(
                node.func, ast.Attribute
            ):
                call = ast.unparse(node.func)
                if call.endswith(
                    (
                        ".responses.create",
                        ".messages.create",
                        ".generate_content",
                        ".generate_content_stream",
                    )
                ):
                    assert relative in GENERATION_ADAPTERS, relative
