"""Mock Slack. Dedupes on idempotency key, like a real client would need to."""

from relay.providers.chat_base import ChatResult
from relay.providers.errors import FailureQueue


class MockChatProvider:
    mode = "mock"

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self._by_key: dict[str, ChatResult] = {}
        self.failures = FailureQueue()

    def post_message(self, channel: str, text: str, *, idempotency_key: str) -> ChatResult:
        self.failures.maybe_raise()
        if idempotency_key in self._by_key:
            first = self._by_key[idempotency_key]
            return ChatResult(first.channel, first.ts, deduped=True)
        result = ChatResult(channel, ts=f"1700000000.{len(self.sent):06d}")
        self.sent.append({"channel": channel, "text": text})
        self._by_key[idempotency_key] = result
        return result
