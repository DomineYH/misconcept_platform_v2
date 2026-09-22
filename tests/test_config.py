import pytest
from pydantic import ValidationError

from src.config import Config


REASONING_FIELDS = (
    "ANALYSIS_REASONING",
    "STUDENT_REASONING",
    "TUTOR_REASONING",
)


@pytest.mark.parametrize(
    "effort", ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_reasoning_environment_values_are_preserved(monkeypatch, effort):
    for field in REASONING_FIELDS:
        monkeypatch.setenv(field, effort)
    settings = Config(_env_file=None)
    assert all(getattr(settings, field) == effort for field in REASONING_FIELDS)


@pytest.mark.parametrize("field", REASONING_FIELDS)
def test_invalid_reasoning_is_rejected(monkeypatch, field):
    monkeypatch.setenv(field, "invalid")
    with pytest.raises(ValidationError, match=field):
        Config(_env_file=None)


def test_gpt56_reasoning_loads_from_dotenv(tmp_path, monkeypatch):
    for field in REASONING_FIELDS:
        monkeypatch.delenv(field, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CHAT_MODEL=gpt-5.6\nANALYSIS_MODEL=gpt-5.6\n"
        "ANALYSIS_REASONING=max\nSTUDENT_REASONING=xhigh\n"
        "TUTOR_REASONING=xhigh\n"
    )
    settings = Config(_env_file=env_file)
    assert tuple(getattr(settings, field) for field in REASONING_FIELDS) == (
        "max",
        "xhigh",
        "xhigh",
    )
