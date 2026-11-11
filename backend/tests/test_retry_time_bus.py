"""relay/retry.py (moved from the removed workflow package), runtime speed control, the bus."""

import asyncio

import pytest

from relay import bus, timeutil
from relay.providers.errors import ProviderError
from relay.retry import MAX_DELAY_S, backoff_delay, call_with_retry


def flaky(failures: int, *, retryable: bool = True):
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] <= failures:
            raise ProviderError("boom", retryable=retryable)
        return "ok"

    return fn, state


def test_retries_retryable_errors_then_succeeds():
    fn, state = flaky(2)
    assert call_with_retry(fn, max_attempts=3, base_delay_s=0.5) == ("ok", 3)
    assert state["n"] == 3


def test_gives_up_after_max_attempts():
    fn, state = flaky(5)
    with pytest.raises(ProviderError):
        call_with_retry(fn, max_attempts=2, base_delay_s=0.5)
    assert state["n"] == 2


def test_does_not_retry_permanent_errors():
    fn, state = flaky(1, retryable=False)
    with pytest.raises(ProviderError):
        call_with_retry(fn, max_attempts=4, base_delay_s=0.5)
    assert state["n"] == 1


def test_backoff_doubles_and_is_capped():
    assert [backoff_delay(n, 0.5) for n in (1, 2, 3)] == [0.5, 1.0, 2.0]
    assert backoff_delay(30, 0.5) == MAX_DELAY_S


def test_speed_override_changes_every_interval():
    assert timeutil.scaled(60) == pytest.approx(0.06)  # conftest: scale 1000
    timeutil.set_scale(60)
    assert timeutil.get_scale() == 60 and timeutil.scaled(60) == 1
    timeutil.set_scale(None)
    assert timeutil.get_scale() == 1000
    with pytest.raises(ValueError):
        timeutil.set_scale(0)


async def test_bus_delivers_to_every_subscriber_and_unsubscribes():
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish({"n": 1})
    assert await asyncio.wait_for(a.get(), 1) == {"n": 1}
    assert await asyncio.wait_for(b.get(), 1) == {"n": 1}
    bus.unsubscribe(a)
    bus.publish({"n": 2})
    assert await asyncio.wait_for(b.get(), 1) == {"n": 2}
    assert a.empty()
    bus.unsubscribe(b)
    assert bus.subscriber_count() == 0


async def test_slow_client_loses_oldest_message_not_the_newest():
    q = bus.subscribe()
    for n in range(bus.QUEUE_SIZE + 5):
        bus.publish({"n": n})
    await asyncio.sleep(0.05)  # let call_soon_threadsafe callbacks run
    first = q.get_nowait()
    assert first["n"] == 5 and q.qsize() == bus.QUEUE_SIZE - 1
    bus.unsubscribe(q)
