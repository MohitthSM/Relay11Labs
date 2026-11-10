from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChatResult:
    channel: str
    ts: str
    deduped: bool = False  # True if this idempotency key was already posted


class ChatProvider(Protocol):
    mode: str

    def post_message(self, channel: str, text: str, *, idempotency_key: str) -> ChatResult: ...
