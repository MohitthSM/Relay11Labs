from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class LLMResult:
    text: str  # JSON text; the caller validates it against its Pydantic model
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    stop_reason: str = "end_turn"


class LLMProvider(Protocol):
    mode: str

    def complete_json(self, prompt: str, schema: dict[str, Any], model: str) -> LLMResult: ...
