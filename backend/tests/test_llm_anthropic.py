"""AnthropicLLMProvider with a stub client: request shape and refusal handling, no network."""

from types import SimpleNamespace

import pytest

from relay.providers.errors import ProviderError
from relay.providers.llm_anthropic import AnthropicLLMProvider
from relay.providers.llm_base import ScriptBatch, ScriptLine, ScriptRequest


class StubMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def provider_with(response, **kw) -> tuple[AnthropicLLMProvider, StubMessages]:
    p = AnthropicLLMProvider("test-key", timeout_s=6, **kw)
    stub = StubMessages(response)
    p.client = SimpleNamespace(messages=stub, beta=SimpleNamespace(messages=stub))
    return p, stub


def response(stop_reason="end_turn", parsed=None):
    usage = SimpleNamespace(input_tokens=500, output_tokens=120)
    return SimpleNamespace(
        stop_reason=stop_reason, parsed_output=parsed, model="claude-haiku-5-5", usage=usage
    )


REQ = ScriptRequest(
    system="sys", user="usr", event_type="stand_change", facts={}, slots=[("en", 1)]
)


def test_structured_output_request_and_result():
    parsed = ScriptBatch(scripts=[ScriptLine(language="en", urgency=1, text="Hi {route}.")])
    p, stub = provider_with(response(parsed=parsed))
    result = p.generate_scripts(REQ, "claude-haiku-5-5")
    assert stub.kwargs["output_format"] is ScriptBatch
    assert stub.kwargs["output_config"] == {"effort": "low"}
    assert stub.kwargs["system"] == "sys"
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "betas"):
        assert banned not in stub.kwargs
    assert ScriptBatch.model_validate_json(result.text) == parsed
    assert (result.input_tokens, result.output_tokens) == (500, 120)


def test_refusal_raises_refused():
    p, _ = provider_with(response(stop_reason="refusal"))
    with pytest.raises(ProviderError) as exc:
        p.generate_scripts(REQ, "claude-haiku-5-5")
    assert exc.value.kind == "refused"


def test_server_fallback_only_on_sonnet():
    parsed = ScriptBatch(scripts=[])
    p, stub = provider_with(response(parsed=parsed), server_fallback=True)
    p.generate_scripts(REQ, "claude-sonnet-5-5")
    assert stub.kwargs["fallbacks"] == "default" and stub.kwargs["betas"]
    p.generate_scripts(REQ, "claude-haiku-5-5")
    assert "fallbacks" not in stub.kwargs


def test_unparseable_output_becomes_provider_error():
    class Broken(StubMessages):
        def parse(self, **kwargs):
            raise ValueError("bad json")

    p, _ = provider_with(response())
    p.client = SimpleNamespace(messages=Broken(None))
    with pytest.raises(ProviderError) as exc:
        p.generate_scripts(REQ, "claude-haiku-5-5")
    assert exc.value.kind == "error"
