"""Fresh-process configuration checks avoid conftest's normal/test settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_ENV = {
    "APP_MODE": "demo",
    "DEMO_DATA_ROOT": "/tmp/isolated-demo-root",
    "DEMO_SEED_DIR": "/tmp/isolated-demo-seed",
    "DEMO_ORIGIN": "https://demo.invalid",
    "NORMAL_ORIGIN": "https://normal.invalid",
    "DEMO_SESSION_SECRET": "s" * 40,
    "DEMO_CSRF_SECRET": "c" * 40,
}


def run_python(code, *, env=None, cwd=None):
    clean = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("DEMO_", "OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_", "AZURE_")
        )
        and key
        not in {
            "APP_MODE",
            "TESTING",
            "DATABASE_URL",
            "SESSION_SECRET",
            "BOOTSTRAP_ADMIN_ON_STARTUP",
            "PROVIDER_SECRET_ENCRYPTION_KEY",
            "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
            "ENV",
        }
    }
    clean.update(PYTHONPATH=str(ROOT), **(DEMO_ENV if env is None else env))
    return subprocess.run(
        [sys.executable, "-c", code],
        env=clean,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_demo_defaults_mode_frozen_and_dotenv_ignored(tmp_path):
    (tmp_path / ".env").write_text(
        "APP_MODE=normal\nTESTING=true\nOPENAI_API_KEY=forbidden\nDEMO_WORKERS=2\n"
    )
    result = run_python(
        """
import os
from pydantic import ValidationError
from src.config import Config, config
assert config.APP_MODE == "demo"
assert config.DATABASE_URL == ""
defaults = {
    "WORKERS": 1, "IDLE_TTL_SECONDS": 1800, "ABSOLUTE_TTL_SECONDS": 7200,
    "MAX_VISITORS": 20, "MAX_DB_BYTES": 33554432, "MAX_DISK_BYTES": 1073741824,
    "DRAIN_SECONDS": 5, "CLEANUP_INTERVAL_SECONDS": 30,
    "MAX_RETIRED_GENERATIONS": 40, "MAX_TURNS": 10, "MAX_SESSIONS": 10,
    "MAX_USERS": 10, "MAX_GROUPS": 5, "MAX_SCENARIOS": 5, "MAX_CONTRIBUTORS": 5,
    "MAX_UPLOAD_BYTES": 65536, "MAX_UPLOAD_ROWS": 8, "MAX_ACTIVE_GLOBAL": 8,
    "MAX_ACTIVE_VISITOR": 2, "REQUESTS_PER_MINUTE": 60,
    "STARTS_PER_IP_PER_MINUTE": 10,
}
assert all(getattr(config, "DEMO_" + name) == value for name, value in defaults.items())
assert config.demo_settings.workers == 1
assert config.DEMO_SEED_DIR == "/tmp/isolated-demo-seed"
assert Config(_env_file=".env").APP_MODE == "demo"
os.environ["APP_MODE"] = "normal"
assert Config().APP_MODE == "demo"
try:
    config.APP_MODE = "normal"
except ValidationError:
    pass
else:
    raise AssertionError("mode must be frozen")
""",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_modes_fail_safely():
    for mode in ("", "invalid", "DEMO", " demo"):
        result = run_python("import src.config", env={"APP_MODE": mode})
        assert result.returncode != 0
        assert "DEMO_CONFIGURATION_INVALID" in result.stderr


def test_demo_configuration_rejection_matrix():
    result = run_python(
        """
from src.config import Config, config
from src.demo.database import DemoError
cases = [
    {"TESTING": True}, {"BOOTSTRAP_ADMIN_ON_STARTUP": True},
    {"DATABASE_URL": "sqlite:///sensitive.db"},
    {"DEMO_DATA_ROOT": ""}, {"DEMO_DATA_ROOT": "relative"},
    {"DEMO_DATA_ROOT": "/tmp/../demo"},
    {"DEMO_SEED_DIR": ""}, {"DEMO_SEED_DIR": "relative"},
    {"DEMO_SEED_DIR": "/tmp/../seed"},
    {"DEMO_SESSION_SECRET": "short"},
    {"DEMO_SESSION_SECRET": ""}, {"DEMO_CSRF_SECRET": ""},
    {"DEMO_SESSION_SECRET": "example" + "x" * 40},
    {"DEMO_CSRF_SECRET": "s" * 40},
    {"SESSION_SECRET": "s" * 40},
    {"DEMO_ORIGIN": ""}, {"NORMAL_ORIGIN": ""},
    {"DEMO_ORIGIN": "https://normal.invalid:8443"},
    {"DEMO_ORIGIN": "https://normal.invalid."},
    {"DEMO_ORIGIN": "http://demo.invalid"},
    {"ENV": "production", "DEMO_ORIGIN": "http://127.0.0.1"},
    {"DEMO_MAX_ACTIVE_GLOBAL": 2, "DEMO_MAX_ACTIVE_VISITOR": 3},
    {"DEMO_IDLE_TTL_SECONDS": 7200, "DEMO_ABSOLUTE_TTL_SECONDS": 1800},
    {"DEMO_MAX_DB_BYTES": 268435456, "DEMO_MAX_DISK_BYTES": 67108864},
]
for field in ("DEMO_ORIGIN", "NORMAL_ORIGIN"):
    for value in ("https://user@host.invalid", "https://host.invalid/", "https://host.invalid?q=1", "https://host.invalid#x", "https://*.invalid", "https://a.invalid,https://b.invalid"):
        cases.append({field: value})
for value in ("https://normal.invalid/demo", "https://normal.invalid./demo", "https://%6eormal.invalid/demo", "https://user@demo.invalid/demo", "https://demo.invalid/other", "https://demo.invalid/demo?q=1", "https://demo.invalid/demo#x", "http://demo.invalid/demo"):
    cases.append({"DEMO_PUBLIC_URL": value})
for case in cases:
    try:
        Config(**case)
    except DemoError as error:
        assert str(error) == "DEMO_CONFIGURATION_INVALID"
    else:
        raise AssertionError(case)
"""
    )
    assert result.returncode == 0, result.stderr


# Contract §8 bounds, independent of the implementation's validation tables.
BOUNDS = {
    "WORKERS": (1, 1),
    "IDLE_TTL_SECONDS": (300, 7200),
    "ABSOLUTE_TTL_SECONDS": (1800, 28800),
    "MAX_VISITORS": (1, 1000),
    "MAX_DB_BYTES": (1048576, 268435456),
    "MAX_DISK_BYTES": (67108864, 10737418240),
    "DRAIN_SECONDS": (1, 30),
    "CLEANUP_INTERVAL_SECONDS": (5, 300),
    "MAX_RETIRED_GENERATIONS": (1, 2000),
    "MAX_TURNS": (2, 100),
    "MAX_SESSIONS": (2, 100),
    "MAX_USERS": (2, 100),
    "MAX_GROUPS": (1, 20),
    "MAX_SCENARIOS": (1, 20),
    "MAX_CONTRIBUTORS": (1, 20),
    "MAX_UPLOAD_BYTES": (1024, 1048576),
    "MAX_UPLOAD_ROWS": (1, 98),
    "MAX_ACTIVE_GLOBAL": (2, 64),
    "MAX_ACTIVE_VISITOR": (1, 4),
    "REQUESTS_PER_MINUTE": (10, 600),
    "STARTS_PER_IP_PER_MINUTE": (1, 60),
}


def test_demo_numeric_matrix():
    result = run_python(
        f"""
from src.config import Config
from src.demo.database import DemoError
bounds = {BOUNDS!r}
for suffix, (low, high) in bounds.items():
    field = "DEMO_" + suffix
    for value in (True, False, 1.0, 1.5, "1.0", "true", "", -1, low - 1, high + 1):
        try:
            Config(**{{field: value}})
        except DemoError as error:
            assert str(error) == "DEMO_CONFIGURATION_INVALID"
        else:
            raise AssertionError((field, value))
    for value in (low, high, str(low), str(high)):
        settings = Config(**{{
            "DEMO_IDLE_TTL_SECONDS": 300,
            "DEMO_ABSOLUTE_TTL_SECONDS": 28800,
            "DEMO_MAX_DB_BYTES": 1048576,
            "DEMO_MAX_DISK_BYTES": 10737418240,
            "DEMO_MAX_ACTIVE_GLOBAL": 64,
            field: value,
        }})
        assert getattr(settings, field) == int(value)
"""
    )
    assert result.returncode == 0, result.stderr


def test_demo_forbidden_environment_and_secret_matrix():
    # SDK credentials and URL overrides supported by the installed providers.
    keys = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_ADMIN_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_AD_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "AZURE_OPENAI_ENDPOINT",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
        "OPENAI_CUSTOM_HEADERS",
        "ANTHROPIC_CUSTOM_HEADERS",
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION",
    )
    result = run_python(
        f"""
import os
from src.config import Config
from src.demo.database import DemoError
for key in {keys!r}:
    os.environ[key] = "must-never-appear-in-errors"
    try:
        Config()
    except DemoError as error:
        assert str(error) == "DEMO_CONFIGURATION_INVALID"
    else:
        raise AssertionError(key)
    del os.environ[key]
for key, value in (("TESTING", "true"), ("BOOTSTRAP_ADMIN_ON_STARTUP", "true"), ("DATABASE_URL", "sqlite:///forbidden.db")):
    os.environ[key] = value
    try:
        Config()
    except DemoError:
        pass
    else:
        raise AssertionError(key)
    del os.environ[key]
for field in ("DEMO_SESSION_SECRET", "DEMO_CSRF_SECRET"):
    for pattern in ("change-this", "your-secret", "example", "insecure", "default", "placeholder", "todo", "fixme"):
        try:
            Config(**{{field: pattern.upper() + "x" * 40}})
        except DemoError:
            pass
        else:
            raise AssertionError((field, pattern))
"""
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "key",
    [
        "ANTHROPIC_IDENTITY_TOKEN",
        "ANTHROPIC_IDENTITY_TOKEN_FILE",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_CONFIG_DIR",
    ],
)
def test_demo_rejects_anthropic_identity_and_profile_inputs(key):
    result = run_python("import src.config", env=DEMO_ENV | {key: "forbidden"})
    assert result.returncode != 0
    assert "DEMO_CONFIGURATION_INVALID" in result.stderr


@pytest.mark.parametrize(
    "field", ["DEMO_ORIGIN", "NORMAL_ORIGIN", "DEMO_PUBLIC_URL"]
)
def test_demo_rejects_browser_numeric_host_aliases(field):
    result = run_python(
        f"""
from src.config import Config
from src.demo.database import DemoError
for host in ("127.1", "2130706433", "0x7f000001", "0177.0.0.1", "api.123", "api.0x"):
    value = "https://" + host + ("/demo" if {field!r} == "DEMO_PUBLIC_URL" else "")
    try:
        Config(**{{{field!r}: value}})
    except DemoError as error:
        assert error.code == "DEMO_CONFIGURATION_INVALID"
    else:
        raise AssertionError(({field!r}, host))
"""
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("environment", ["staging", "", "developmnt"])
def test_demo_rejects_loopback_http_outside_development(environment):
    result = run_python(
        f"""
from src.config import Config
from src.demo.database import DemoError
for field in ("DEMO_ORIGIN", "NORMAL_ORIGIN", "DEMO_PUBLIC_URL"):
    value = "http://127.0.0.1" + ("/demo" if field == "DEMO_PUBLIC_URL" else "")
    try:
        Config(ENV={environment!r}, **{{field: value}})
    except DemoError as error:
        assert error.code == "DEMO_CONFIGURATION_INVALID"
    else:
        raise AssertionError((field, {environment!r}))
"""
    )
    assert result.returncode == 0, result.stderr


def test_demo_accepts_canonical_hosts_and_development_loopback():
    result = run_python(
        """
from src.config import Config
for field in ("DEMO_ORIGIN", "NORMAL_ORIGIN", "DEMO_PUBLIC_URL"):
    for host in ("127.0.0.1", "example42.invalid"):
        for scheme in ("http", "https") if host == "127.0.0.1" else ("https",):
            value = scheme + "://" + host + ("/demo" if field == "DEMO_PUBLIC_URL" else "")
            Config(ENV="development", **{field: value})
"""
    )
    assert result.returncode == 0, result.stderr


def test_normal_link_numeric_aliases_and_loopback_environment_validation():
    result = run_python(
        """
from src.config import Config
from src.demo.database import DemoError
for field in ("NORMAL_ORIGIN", "DEMO_PUBLIC_URL"):
    for host in ("127.1", "2130706433", "0x7f000001", "0177.0.0.1"):
        value = "https://" + host + ("/demo" if field == "DEMO_PUBLIC_URL" else "")
        try:
            Config(**{field: value})
        except DemoError:
            pass
        else:
            raise AssertionError((field, host))
    value = "http://127.0.0.1" + ("/demo" if field == "DEMO_PUBLIC_URL" else "")
    for environment in ("staging", "", "developmnt"):
        try:
            Config(ENV=environment, **{field: value})
        except DemoError:
            pass
        else:
            raise AssertionError((field, environment))
    Config(ENV="development", **{field: value})
""",
        env={"TESTING": "true", "NORMAL_ORIGIN": "https://normal.invalid"},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field", ["DEMO_SESSION_SECRET", "DEMO_CSRF_SECRET"])
def test_demo_secret_31_and_32_character_boundary(field):
    result = run_python(
        f"""
from src.config import Config
from src.demo.database import DemoError
try:
    Config(**{{{field!r}: "b" * 31}})
except DemoError as error:
    assert error.code == "DEMO_CONFIGURATION_INVALID"
else:
    raise AssertionError("31 characters accepted")
assert getattr(Config(**{{{field!r}: "b" * 32}}), {field!r}).get_secret_value() == "b" * 32
"""
    )
    assert result.returncode == 0, result.stderr


def test_valid_demo_origins_and_public_urls():
    for overrides in (
        {"ENV": "production", "DEMO_PUBLIC_URL": "https://demo.invalid/demo"},
        {"DEMO_PUBLIC_URL": "https://demo.invalid/"},
        {
            "DEMO_ORIGIN": "http://127.0.0.1:8001",
            "NORMAL_ORIGIN": "http://localhost:8000",
            "DEMO_PUBLIC_URL": "http://127.0.0.1:8001/demo",
        },
    ):
        result = run_python("import src.config", env=DEMO_ENV | overrides)
        assert result.returncode == 0, result.stderr


def test_normal_dotenv_mode_and_demo_settings_preservation(tmp_path):
    (tmp_path / ".env").write_text(
        "APP_MODE=demo\nSESSION_SECRET="
        + "n" * 40
        + "\nDEMO_WORKERS=bad\nDEMO_DATA_ROOT=relative\nOPENAI_API_KEY=normal-key\n"
        + "DEMO_SEED_DIR=relative\n"
    )
    result = run_python(
        """
from src.config import config
assert config.APP_MODE == "normal"
assert config.DEMO_SEED_DIR == ""
assert config.OPENAI_API_KEY == "normal-key"
assert config.DATABASE_URL == "sqlite+aiosqlite:///./dialogue_sim.db"
""",
        env={},
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr


def test_normal_public_link_validation():
    normal = {"TESTING": "true", "NORMAL_ORIGIN": "https://normal.invalid"}
    for url in ("https://demo.invalid/demo", "", "https://demo.invalid/"):
        result = run_python(
            "import src.config", env=normal | {"DEMO_PUBLIC_URL": url}
        )
        assert result.returncode == 0, result.stderr
    for url in (
        "https://normal.invalid/demo",
        "https://demo.invalid/other",
        "https://u@demo.invalid/",
        "https://demo.invalid/demo?q=x",
        "https://demo.invalid/demo#f",
    ):
        result = run_python(
            "import src.config", env=normal | {"DEMO_PUBLIC_URL": url}
        )
        assert result.returncode != 0
        assert "DEMO_CONFIGURATION_INVALID" in result.stderr


def test_demo_normal_connection_and_startup_fail_before_side_effects():
    result = run_python(
        """
import asyncio
from unittest.mock import patch
from src.demo.database import DemoError
with patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=AssertionError("engine opened")):
    from src.db import connection
    assert connection.engine is None
    try:
        connection.AsyncSessionLocal()
    except DemoError:
        pass
    else:
        raise AssertionError("normal factory allowed")
    from src import main
async def check():
    with patch.object(main, "init_db", side_effect=AssertionError("normal startup")):
        try:
            async with main.lifespan(main.app):
                raise AssertionError("demo startup allowed")
        except DemoError as error:
            assert error.code == "DEMO_CONFIGURATION_INVALID"
    for action in (connection.init_db, connection.close_db):
        try:
            await action()
        except DemoError:
            pass
        else:
            raise AssertionError("normal DB action allowed")
asyncio.run(check())
"""
    )
    assert result.returncode == 0, result.stderr
