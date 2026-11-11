"""One worker thread for work that must not hold up a first play (voicing later urgency levels).

The events service submits a function and returns. The work runs in order of submission, one
task at a time, so later levels never compete with each other for the TTS concurrency cap. It
does compete with first-play renders, which share voice.py's process-wide semaphore. A task
that raises is logged and never takes the app down.

`wait()` blocks until everything submitted so far has finished: tests use it, and so do scripts
that need every clip before they move on.
"""

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures

log = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relay-background")
_lock = threading.Lock()
_pending: set[Future] = set()


def submit(fn: Callable[..., object], *args: object) -> Future:
    def run() -> object:
        try:
            return fn(*args)
        except Exception:
            log.exception("background task %s failed", getattr(fn, "__name__", fn))
            return None

    future = _pool.submit(run)
    with _lock:
        _pending.add(future)
    future.add_done_callback(_forget)
    return future


def _forget(future: Future) -> None:
    with _lock:
        _pending.discard(future)


def wait(timeout: float | None = None) -> bool:
    """Block until all submitted work is done, including work submitted meanwhile.
    Returns False on timeout."""
    while True:
        with _lock:
            pending = set(_pending)
        if not pending:
            return True
        _, not_done = wait_futures(pending, timeout=timeout)
        if not_done:
            return False
