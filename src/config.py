"""Configuration module for loading environment variables."""

import os

from pydantic import SecretStr, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IS_TEST_ENV = os.getenv("TESTING", "").lower() == "true"


class Config(BaseSettings):
    """Application configuration from environment variables."""

    model_config = SettingsConfigDict(
        env_file=None if IS_TEST_ENV else ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

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

    # Number of completed teacher–student pairs preceding the current turn.
    CONTEXT_WINDOW_TURNS: int = 10

    # Session Security
    SESSION_SECRET: str = "change-this-insecure-default"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./dialogue_sim.db"

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
        if IS_TEST_ENV:
            return v
        blocked = [
            "change-this",
            "your-secret",
            "example",
            "insecure",
            "default",
            "placeholder",
            "todo",
            "fixme",
        ]
        v_lower = v.lower()
        for pattern in blocked:
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

    @field_validator("CONTEXT_WINDOW_TURNS")
    @classmethod
    def validate_context_window(cls, v):
        """Validate context window size range."""
        if not (4 <= v <= 200):
            raise ValueError(
                "CONTEXT_WINDOW_TURNS must be between " f"4 and 200, got {v}"
            )
        return v

    def validate(self) -> None:
        """No-op for backward compatibility.

        Pydantic validates all fields in __init__ automatically.
        This method exists only for callers that expect it.
        """
        pass


config = Config()
