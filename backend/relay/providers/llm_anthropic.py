"""Claude via the official `anthropic` SDK. The only module that imports `anthropic`.

- Structured output: `messages.parse(output_format=<Pydantic model>)`, read `parsed_output`.
- `output_config={"effort": "low"}` for latency. No temperature/top_p/top_k, no prefill, no
  forced tool_choice (current models reject them).
- Per-call timeout (LLM_TIMEOUT_S) and `max_retries=0`: the engine owns fallback (D14).
- `stop_reason == "refusal"` raises ProviderError(kind="refused"); the engine falls back.
- ANTHROPIC_SERVER_FALLBACK=true adds the server-side refusal fallback on Sonnet models
  (Haiku has none).
"""

import json
import time
from collections.abc import Callable
from typing import Any

import anthropic
from pydantic import BaseModel

from relay.providers.errors import ERROR, REFUSED, TIMEOUT, ProviderError
from relay.providers.llm_base import (
    ExtractRequest,
    LLMResult,
    NoteExtraction,
    ScriptBatch,
    ScriptRequest,
)

MAX_TOKENS = 8000  # includes adaptive thinking; 9 short scripts need well under 2K
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLMProvider:
    mode = "live"

    def __init__(self, api_key: str, *, timeout_s: float, server_fallback: bool = False) -> None:
        self.client = anthropic.Anthropic(api_key=api_key).with_options(
            timeout=timeout_s, max_retries=0
        )
        self.server_fallback = server_fallback

    def generate_scripts(self, req: ScriptRequest, model: str) -> LLMResult:
        return self._parse(model, req.system, req.user, ScriptBatch)

    def extract_event(self, req: ExtractRequest, model: str) -> LLMResult:
        return self._parse(model, req.system, req.user, NoteExtraction)

    def complete_json(self, prompt: str, schema: dict[str, Any], model: str) -> LLMResult:
        started = time.perf_counter()
        resp = _call(
            lambda: self.client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
                messages=[{"role": "user", "content": prompt}],
            )
        )
        _raise_on_refusal(resp.stop_reason)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _result(resp, text, started)

    def _parse(self, model: str, system: str, user: str, output: type[BaseModel]) -> LLMResult:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low"},
            "output_format": output,
        }
        if self.server_fallback and model.startswith("claude-sonnet"):
            resp = _call(
                lambda: self.client.beta.messages.parse(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                )
            )
        else:
            resp = _call(lambda: self.client.messages.parse(**kwargs))
        _raise_on_refusal(resp.stop_reason)
        if resp.parsed_output is None:
            raise ProviderError(f"no parsed output (stop_reason={resp.stop_reason})", kind=ERROR)
        return _result(resp, json.dumps(resp.parsed_output.model_dump(mode="json")), started)


def _call(fn: Callable[[], Any]) -> Any:
    """Run one SDK call; turn SDK exceptions into ProviderError with a retryable flag and kind."""
    try:
        return fn()
    except anthropic.APITimeoutError as exc:  # subclass of APIConnectionError: check it first
        raise ProviderError("timeout", retryable=True, kind=TIMEOUT) from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError("connection error", retryable=True) from exc
    except anthropic.APIStatusError as exc:
        retryable = exc.status_code == 429 or exc.status_code >= 500
        raise ProviderError(f"HTTP {exc.status_code}", retryable=retryable) from exc
    except ValueError as exc:  # output that does not parse (pydantic ValidationError, bad JSON)
        raise ProviderError(f"invalid output: {type(exc).__name__}") from exc


def _raise_on_refusal(stop_reason: str | None) -> None:
    if stop_reason == "refusal":
        raise ProviderError("model refused", kind=REFUSED)


def _result(resp: Any, text: str, started: float) -> LLMResult:
    return LLMResult(
        text=text,
        model=resp.model,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
        stop_reason=resp.stop_reason or "",
    )
