"""Mock LLM: returns scripted responses, or fills the JSON schema with placeholder values."""

import json
from typing import Any

from relay.providers.errors import FailureQueue
from relay.providers.llm_base import LLMResult


def fill_schema(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """Build a minimal valid value for a JSON schema (enough for Pydantic models)."""
    root = root or schema
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        return fill_schema(node, root)
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return fill_schema(options[0], root) if options else None
    kind = schema.get("type")
    if kind == "object":
        return {k: fill_schema(v, root) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return []
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    return "mock"


class MockLLMProvider:
    mode = "mock"

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        self.responses = list(responses or [])  # consumed in order, then schema-fill
        self.calls: list[dict[str, Any]] = []
        self.failures = FailureQueue()

    def complete_json(self, prompt: str, schema: dict[str, Any], model: str) -> LLMResult:
        self.calls.append({"prompt": prompt, "model": model})
        self.failures.maybe_raise()
        item = self.responses.pop(0) if self.responses else json.dumps(fill_schema(schema))
        if isinstance(item, Exception):
            raise item
        return LLMResult(
            text=item,
            model=model,
            input_tokens=len(prompt) // 4,
            output_tokens=len(item) // 4,
            latency_ms=1,
        )
