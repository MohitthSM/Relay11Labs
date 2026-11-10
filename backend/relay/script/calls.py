"""One LLM attempt -> one LLMCallRecord (the future LLMCall row): model, tokens, cost, outcome."""

import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field

from relay.providers.errors import REFUSED, TIMEOUT, ProviderError
from relay.providers.llm_base import LLMResult
from relay.providers.pricing import llm_cost

Outcome = Literal["accepted", "rejected", "timeout", "error", "refused", "invalid"]


class LLMCallRecord(BaseModel):
    purpose: Literal["script", "extract"]
    attempt: int
    model: str
    prompt_version: str  # e.g. "script_airport.v1+script_rules.v1"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    outcome: Outcome = "accepted"
    error: str | None = None
    # slot ("fr/3") -> fact-lock reasons, for rejected script calls
    violations: dict[str, list[str]] = Field(default_factory=dict)


def call_llm(
    fn: Callable[[], LLMResult],
    *,
    purpose: Literal["script", "extract"],
    attempt: int,
    model: str,
    prompt_version: str,
) -> tuple[LLMResult | None, LLMCallRecord]:
    """Run one provider call. A ProviderError becomes (None, record with the outcome)."""
    rec = LLMCallRecord(
        purpose=purpose, attempt=attempt, model=model, prompt_version=prompt_version
    )
    started = time.perf_counter()
    try:
        result = fn()
    except ProviderError as exc:
        rec.latency_ms = int((time.perf_counter() - started) * 1000)
        rec.outcome = exc.kind if exc.kind in (TIMEOUT, REFUSED) else "error"
        rec.error = str(exc)
        return None, rec
    rec.latency_ms = int((time.perf_counter() - started) * 1000)
    rec.model = result.model
    rec.input_tokens, rec.output_tokens = result.input_tokens, result.output_tokens
    rec.cost_usd = llm_cost(result.model, result.input_tokens, result.output_tokens)
    return result, rec
