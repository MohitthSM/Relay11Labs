"""The ONLY place RELAY_TIME_SCALE is read. All intervals and sleeps go through here."""

import time
from datetime import UTC, datetime

from relay.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


def scaled(seconds: float) -> float:
    """Divide an interval by the time scale (60 = one minute passes in one second)."""
    return seconds / get_settings().relay_time_scale


def sleep(seconds: float) -> None:
    """Blocking, time-scaled sleep for sync code (e.g. retry backoff)."""
    time.sleep(scaled(seconds))
