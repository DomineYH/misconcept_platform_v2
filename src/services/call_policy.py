"""Application-owned absolute deadlines and the single retry policy."""

import asyncio
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


class CallDeadline:
    def __init__(self, timeouts, role, *, admitted_at=None):
        self.started = (
            asyncio.get_running_loop().time()
            if admitted_at is None
            else admitted_at
        )
        self.ends = self.started + timeouts[f"{role or 'model_list'}_total"]
        first = timeouts.get(f"{role}_first_output")
        self.first_ends = self.started + first if first is not None else None
        self.timer = None

    def total(self):
        self.timer = asyncio.timeout_at(self.ends)
        return self.timer

    def first(self):
        return asyncio.timeout_at(self.first_ends)

    def expired(self):
        return self.timer is not None and self.timer.expired()

    def remaining(self):
        return self.ends - asyncio.get_running_loop().time()


def retry_after(value, *, now=None):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (
                target - (now or datetime.now(timezone.utc))
            ).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 1.0
    return seconds if math.isfinite(seconds) and seconds >= 0 else 1.0


def retry_limit(operation, role, admin):
    return int(
        operation
        in ("classification", "synthesis", "analysis_unified", "analysis_merge")
        and role == "analysis"
        and not admin
    )
