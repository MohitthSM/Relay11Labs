"""Provider errors and a tiny failure-injection helper used by the mocks."""

TIMEOUT, REFUSED, ERROR = "timeout", "refused", "error"


class ProviderError(Exception):
    """Raised by any provider.

    `retryable`: a retry may succeed (timeout, 429, 5xx).
    `kind`: timeout | refused | error, so callers can log the outcome and pick a fallback.
    """

    def __init__(self, message: str, *, retryable: bool = False, kind: str = ERROR):
        super().__init__(message)
        self.retryable = retryable
        self.kind = kind


class FailureQueue:
    """Lets tests make the next N calls fail: mock.failures.fail_next(2)."""

    def __init__(self) -> None:
        self._pending: list[ProviderError] = []

    def fail_next(
        self,
        n: int = 1,
        *,
        retryable: bool = True,
        message: str = "mock failure",
        kind: str = ERROR,
    ):
        self._pending.extend(
            ProviderError(message, retryable=retryable, kind=kind) for _ in range(n)
        )

    def maybe_raise(self) -> None:
        if self._pending:
            raise self._pending.pop(0)
