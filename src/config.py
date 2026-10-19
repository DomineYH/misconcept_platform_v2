"""Configuration module for loading environment variables."""

import os
import re
from ipaddress import ip_address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    Field,
    SecretStr,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.demo.database import DemoError
from src.demo.visitors import DemoSettings

PROCESS_APP_MODE = os.getenv("APP_MODE", "normal")
if PROCESS_APP_MODE not in ("normal", "demo"):
    raise DemoError("CONFIGURATION_INVALID")
IS_TEST_ENV = os.getenv("TESTING", "").lower() == "true"
BLOCKED_SECRET_PATTERNS = (
    "change-this",
    "your-secret",
    "example",
    "insecure",
    "default",
    "placeholder",
    "todo",
    "fixme",
)
# §14 includes SDK credentials and endpoint overrides, even for unused clients.
DEMO_FORBIDDEN_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_ADMIN_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
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


def _url_host(value, *, production, paths):
    try:
        url = urlsplit(value)
        host = (url.hostname or "").rstrip(".")
        port = url.port
        try:
            address = ip_address(host)
            host = str(address)
            loopback = address.is_loopback
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
            loopback = host == "localhost"
        if (
            not host
            or url.username is not None
            or url.password is not None
            or url.path not in paths
            or "?" in value
            or "#" in value
            or any(char.isspace() or ord(char) < 32 for char in value)
            or any(char in value for char in "\\*,")
            or "%" in url.netloc
            or url.netloc.endswith(":")
            or (port is not None and port == 0)
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and not production and loopback)
            )
        ):
            raise ValueError
        return host
    except (ValueError, TypeError):
        raise DemoError("CONFIGURATION_INVALID") from None


class Config(BaseSettings):
    """Application configuration from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None if IS_TEST_ENV or PROCESS_APP_MODE == "demo" else ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_MODE: Literal["normal", "demo"] = Field(
        default=PROCESS_APP_MODE, frozen=True
    )
    DEMO_DATA_ROOT: str = ""
    DEMO_ORIGIN: str = ""
    NORMAL_ORIGIN: str = ""
    DEMO_SESSION_SECRET: SecretStr = SecretStr("")
    DEMO_CSRF_SECRET: SecretStr = SecretStr("")
    DEMO_PUBLIC_URL: str = ""
    DEMO_WORKERS: int = 1
    DEMO_IDLE_TTL_SECONDS: int = 1800
    DEMO_ABSOLUTE_TTL_SECONDS: int = 7200
    DEMO_MAX_VISITORS: int = 20
    DEMO_MAX_DB_BYTES: int = 33554432
    DEMO_MAX_DISK_BYTES: int = 1073741824
    DEMO_DRAIN_SECONDS: int = 5
    DEMO_CLEANUP_INTERVAL_SECONDS: int = 30
    DEMO_MAX_RETIRED_GENERATIONS: int = 40
    DEMO_MAX_TURNS: int = 10
    DEMO_MAX_SESSIONS: int = 10
    DEMO_MAX_USERS: int = 10
    DEMO_MAX_GROUPS: int = 5
    DEMO_MAX_SCENARIOS: int = 5
    DEMO_MAX_CONTRIBUTORS: int = 5
    DEMO_MAX_UPLOAD_BYTES: int = 65536
    DEMO_MAX_UPLOAD_ROWS: int = 8
    DEMO_MAX_ACTIVE_GLOBAL: int = 8
    DEMO_MAX_ACTIVE_VISITOR: int = 2
    DEMO_REQUESTS_PER_MINUTE: int = 60
    DEMO_STARTS_PER_IP_PER_MINUTE: int = 10

    def __init__(self, **values):
        if PROCESS_APP_MODE == "demo":
            values["_env_file"] = None
        try:
            super().__init__(**values)
        except ValidationError:
            if PROCESS_APP_MODE == "demo":
                raise DemoError("CONFIGURATION_INVALID") from None
            raise

    @model_validator(mode="before")
    @classmethod
    def process_mode(cls, values):
        values["APP_MODE"] = PROCESS_APP_MODE
        if PROCESS_APP_MODE == "normal":
            for name in tuple(values):
                if name.startswith("DEMO_") and name != "DEMO_PUBLIC_URL":
                    values.pop(name)
            return values
        for name in ("DATABASE_URL", *DEMO_FORBIDDEN_ENV):
            value = values.get(name, "")
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if value or os.environ.get(name):
                raise DemoError("CONFIGURATION_INVALID")
        for name, value in values.items():
            if (
                name.startswith("DEMO_")
                and cls.model_fields.get(name)
                and cls.model_fields[name].annotation is int
            ):
                if type(value) is int:
                    continue
                if not isinstance(value, str) or not re.fullmatch(
                    r"[0-9]+", value
                ):
                    raise DemoError("CONFIGURATION_INVALID")
        return values

    @property
    def demo_settings(self) -> DemoSettings:
        return DemoSettings(
            **{
                name: getattr(self, "DEMO_" + name.upper())
                for name in DemoSettings.__dataclass_fields__
            }
        )

    @model_validator(mode="after")
    def validate_demo(self):
        production = self.is_production
        normal_host = None
        if self.NORMAL_ORIGIN:
            normal_host = _url_host(
                self.NORMAL_ORIGIN, production=production, paths=("",)
            )
        if self.DEMO_PUBLIC_URL:
            if normal_host is None:
                raise DemoError("CONFIGURATION_INVALID")
            if (
                _url_host(
                    self.DEMO_PUBLIC_URL,
                    production=production,
                    paths=("/", "/demo"),
                )
                == normal_host
            ):
                raise DemoError("CONFIGURATION_INVALID")
        if self.APP_MODE == "normal":
            return self
        if self.TESTING or self.BOOTSTRAP_ADMIN_ON_STARTUP:
            raise DemoError("CONFIGURATION_INVALID")
        path = Path(self.DEMO_DATA_ROOT)
        if not path.is_absolute() or ".." in path.parts:
            raise DemoError("CONFIGURATION_INVALID")
        if (
            normal_host is None
            or _url_host(self.DEMO_ORIGIN, production=production, paths=("",))
            == normal_host
        ):
            raise DemoError("CONFIGURATION_INVALID")
        secrets = [
            self.DEMO_SESSION_SECRET.get_secret_value(),
            self.DEMO_CSRF_SECRET.get_secret_value(),
        ]
        if len(set(secrets)) != 2 or self.SESSION_SECRET in secrets:
            raise DemoError("CONFIGURATION_INVALID")
        for secret in secrets:
            if len(secret) < 32 or any(
                pattern in secret.lower() for pattern in BLOCKED_SECRET_PATTERNS
            ):
                raise DemoError("CONFIGURATION_INVALID")
        # Root open revalidates with actual seed bytes before serving demo.
        self.demo_settings.validate(seed_bytes=0)
        bounds = {
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
        if (
            any(
                not low <= getattr(self, "DEMO_" + name) <= high
                for name, (low, high) in bounds.items()
            )
            or self.DEMO_MAX_ACTIVE_VISITOR > self.DEMO_MAX_ACTIVE_GLOBAL
        ):
            raise DemoError("CONFIGURATION_INVALID")
        return self

    # OpenAI API Configuration
    OPENAI_API_KEY: str = ""
    PROVIDER_SECRET_ENCRYPTION_KEY: SecretStr = SecretStr("")
    PROVIDER_SECRET_ENCRYPTION_KEY_VERSION: str = ""
    # ===== GPT-5 Reasoning Effort Configuration =====
    # GPT-5.6: none, low, medium, high, xhigh, max.
    # Earlier models may also support minimal; supported values vary by model.
    ANALYSIS_REASONING: str = "high"
    STUDENT_REASONING: str = "medium"
    TUTOR_REASONING: str = "low"

    TUTOR_INTERVENTION_THRESHOLD: int = 3

    # Session Security
    SESSION_SECRET: str = "change-this-insecure-default"

    # Database
    DATABASE_URL: str = (
        ""
        if PROCESS_APP_MODE == "demo"
        else "sqlite+aiosqlite:///./dialogue_sim.db"
    )

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # CORS - allowed frontend origins (comma-separated for multiple)
    # In production, this MUST be set to your actual frontend URL
    FRONTEND_URL: str = ""

    # Environment (T112: Security hardening)
    ENV: str = "development"  # development or production

    # Admin seed password (used by src/db/seed.py)
    ADMIN_DEFAULT_PASSWORD: str = ""

    # Run ensure_default_admin_account() during FastAPI lifespan startup.
    # Default off so production deployments with read-only DB roles or
    # external seed jobs don't fail to boot. Set to true in dev .env to
    # bootstrap the admin on first run.
    BOOTSTRAP_ADMIN_ON_STARTUP: bool = False

    # Testing
    TESTING: bool = False

    @computed_field
    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.ENV == "production"

    @field_validator("TESTING", "BOOTSTRAP_ADMIN_ON_STARTUP", mode="before")
    @classmethod
    def parse_bool(cls, v):
        """Parse boolean flags from string ("true"/"false") to bool."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() == "true"
        return False

    @field_validator("SESSION_SECRET")
    @classmethod
    def validate_session_secret(cls, v):
        """Validate session secret strength."""
        if IS_TEST_ENV or PROCESS_APP_MODE == "demo":
            return v
        v_lower = v.lower()
        for pattern in BLOCKED_SECRET_PATTERNS:
            if pattern in v_lower:
                raise ValueError(
                    "SESSION_SECRET contains blocked "
                    f"pattern '{pattern}'. "
                    "Set a strong secret in .env"
                )
        if len(v) < 32:
            raise ValueError(
                "SESSION_SECRET must be at least " "32 characters long"
            )
        return v

    @field_validator(
        "ANALYSIS_REASONING", "STUDENT_REASONING", "TUTOR_REASONING"
    )
    @classmethod
    def validate_reasoning(cls, v, info):
        """Validate reasoning effort values."""
        # Union of API values; each model supports a subset.
        # https://developers.openai.com/api/docs/guides/reasoning
        valid_reasoning = [
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ]
        if v not in valid_reasoning:
            raise ValueError(
                f"{info.field_name} must be one of {valid_reasoning}, "
                f"got {v}"
            )
        return v

    @field_validator("TUTOR_INTERVENTION_THRESHOLD")
    @classmethod
    def validate_intervention_threshold(cls, v):
        """Validate intervention threshold range."""
        if not (1 <= v <= 10):
            raise ValueError(
                f"TUTOR_INTERVENTION_THRESHOLD must be between 1 and "
                f"10, got {v}"
            )
        return v

    def validate(self) -> None:
        """No-op for backward compatibility.

        Pydantic validates all fields in __init__ automatically.
        This method exists only for callers that expect it.
        """
        pass


config = Config()
