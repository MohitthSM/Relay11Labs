"""Retry with exponential backoff for TTS calls (voice.py). The LLM is not retried here: the
script engine owns LLM retries (D14)."""

import logging
from collections.abc import Callable

from relay import timeutil
from relay.providers.errors import ProviderError

log = logging.getLogger(__name__)
MAX_DELAY_S = 30.0


def backoff_delay(attempt: int, base_delay_s: float) -> float:
    """attempt is 1 for the first failure: base, 2*base, 4*base, ... capped."""
    return min(base_delay_s * 2 ** (attempt - 1), MAX_DELAY_S)


def call_with_retry[T](
    fn: Callable[[], T], *, max_attempts: int, base_delay_s: float
) -> tuple[T, int]:
    """Call fn; retry only retryable ProviderErrors. Returns (result, attempts used)."""
    attempt = 1
    while True:
        try:
            return fn(), attempt
        except ProviderError as exc:
            if not exc.retryable or attempt >= max_attempts:
                raise
            delay = backoff_delay(attempt, base_delay_s)
            log.warning("call failed (attempt %d), retrying in %.2fs", attempt, delay)
            timeutil.sleep(delay)
            attempt += 1
