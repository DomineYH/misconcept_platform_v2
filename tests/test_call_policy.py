"""Numeric/date retry hints at the shared application policy boundary."""

from datetime import datetime, timezone

import pytest

from src.services.call_policy import retry_after


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
