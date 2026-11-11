"""The ONLY place RELAY_TIME_SCALE is read. All intervals and sleeps go through here."""

import asyncio
import time
from datetime import UTC, datetime

from relay.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


_scale_override: float | None = None  # set at runtime by the speed control


def get_scale() -> float:
    return _scale_override or get_settings().relay_time_scale


def set_scale(scale: float | None) -> None:
    """Change the speed while running (None goes back to RELAY_TIME_SCALE)."""
    global _scale_override
    if scale is not None and scale <= 0:
        raise ValueError("time scale must be positive")
    _scale_override = scale


def scaled(seconds: float) -> float:
    """Divide an interval by the time scale (60 = one minute passes in one second)."""
    return seconds / get_scale()


async def asleep(seconds: float) -> None:
    """Async, time-scaled sleep."""
    await asyncio.sleep(scaled(seconds))


def sleep(seconds: float) -> None:
    """Blocking, time-scaled sleep for sync code (e.g. retry backoff)."""
    time.sleep(scaled(seconds))
