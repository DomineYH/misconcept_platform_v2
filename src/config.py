"""Configuration module for loading environment variables."""

import os

from pydantic import computed_field, field_validator
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
    # Supported: gpt-5 (Responses API with reasoning)
    # Recommended: gpt-5 (latest, Aug 2025)
    CHAT_MODEL: str = "gpt-5-mini"
    ANALYSIS_MODEL: str = "gpt-5.2"
    # Model for dialogue similarity analysis
    DIALOGUE_ANALYSIS_MODEL: str = "gpt-5.2"

    # ===== GPT-5 Reasoning Effort Configuration =====
    # Valid values: minimal, low, medium, high
    ANALYSIS_REASONING: str = "high"
    STUDENT_REASONING: str = "medium"
    TUTOR_REASONING: str = "low"

    # ===== Bot Token Limits =====
    # Note: GPT-5 reasoning tokens count toward max_output_tokens.
    # gpt-5-mini "low" can use ~450 reasoning tokens alone.
    # Minimum recommended: 500 (reasoning) + 500 (text) = 1000
    STUDENT_MAX_TOKENS: int = 1500
    TUTOR_MAX_TOKENS: int = 1500
    TUTOR_INTERVENTION_THRESHOLD: int = 3

    # Context Window
    CONTEXT_WINDOW_TURNS: int = 20

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

    @field_validator("OPENAI_API_KEY")
    @classmethod
    def validate_openai_key(cls, v):
        """Validate OpenAI API key is set."""
        if IS_TEST_ENV:
            return v
        if not v or v.startswith("sk-your"):
            raise ValueError("OPENAI_API_KEY must be set in .env file")
        return v

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
        # GPT-5: minimal, low, medium, high
        # GPT-5.1: none, low, medium, high
        valid_reasoning = ["none", "minimal", "low", "medium", "high"]
        if v not in valid_reasoning:
            raise ValueError(
                f"{info.field_name} must be one of {valid_reasoning}, "
                f"got {v}"
            )
        return v

    @field_validator("STUDENT_MAX_TOKENS", "TUTOR_MAX_TOKENS")
    @classmethod
    def validate_positive_tokens(cls, v, info):
        """Validate token limits are positive."""
        if v <= 0:
            raise ValueError(f"{info.field_name} must be positive, got {v}")
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

    @field_validator("CHAT_MODEL", "ANALYSIS_MODEL", "DIALOGUE_ANALYSIS_MODEL")
    @classmethod
    def validate_model_name(cls, v, info):
        """Validate model names are from supported families."""
        if not v:
            return v
        if v.startswith("gpt-4") or v.startswith("gpt-5"):
            return v
        raise ValueError(
            f"{info.field_name} must be a gpt-4 or gpt-5 " f"model, got {v}"
        )

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
