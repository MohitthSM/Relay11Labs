"""In-memory fan-out of state changes to connected clients (single process, no broker).

Each client gets an asyncio.Queue. `publish` may be called from any thread (the events service
runs in FastAPI's threadpool), so it hands each message to the client's own loop.
"""

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 100
_subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}


def subscribe() -> asyncio.Queue:
    """Call from the client's own loop (the websocket handler)."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers[queue] = asyncio.get_running_loop()
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    _subscribers.pop(queue, None)


def publish(message: dict[str, Any]) -> None:
    for queue, loop in list(_subscribers.items()):
        try:
            loop.call_soon_threadsafe(_put, queue, message)
        except RuntimeError:  # that client's loop has closed
            _subscribers.pop(queue, None)


def _put(queue: asyncio.Queue, message: dict[str, Any]) -> None:
    if queue.full():  # a slow client loses its oldest message, never blocks the others
        queue.get_nowait()
    queue.put_nowait(message)


def subscriber_count() -> int:
    return len(_subscribers)
