"""Numeric/date retry hints at the shared application policy boundary."""

from datetime import datetime, timezone

import pytest

from src.services.call_policy import retry_after, retry_limit


@pytest.mark.parametrize("operation", ["analysis_unified", "analysis_merge"])
@pytest.mark.parametrize(
    "role,admin,expected",
    [
        ("analysis", False, 1),
        ("analysis", True, 0),
        ("student", False, 0),
        ("mentor", False, 0),
    ],
)
def test_s4_retry_allowlist_is_limited_to_runtime_analysis(
    operation, role, admin, expected
):
    assert retry_limit(operation, role, admin) == expected
    assert retry_limit("probe", role, admin) == 0
    assert retry_limit("greeting", role, admin) == 0


@pytest.mark.parametrize(
    "value,seconds",
    [
        ("0", 0),
        ("2.5", 2.5),
        (None, 1),
        ("-1", 1),
        ("NaN", 1),
        ("inf", 1),
        ("PRIVATE-ERROR", 1),
        ("Fri, 09 Oct 2026 00:00:05 GMT", 5),
        ("Thu, 08 Oct 2026 23:59:59 GMT", 1),
    ],
)
def test_retry_after_seconds_dates_and_invalid_hints(value, seconds):
    assert (
        retry_after(value, now=datetime(2026, 10, 9, tzinfo=timezone.utc))
        == seconds
    )
