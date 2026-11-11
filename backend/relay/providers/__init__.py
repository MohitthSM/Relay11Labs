"""Provider factory. Only this package may import vendor SDKs (anthropic, elevenlabs).

The LLM is live (Claude) when ANTHROPIC_API_KEY is set and RELAY_LLM_MODE is not "mock".
TTS is live (ElevenLabs) when ELEVENLABS_API_KEY is set and RELAY_TTS_MODE is not "mock".
A missing key resolves to the mock, so the demo never breaks (D13). `mode` on each provider
says what you got.
"""

import logging
from dataclasses import dataclass

from relay.config import Settings, resolve_mode
from relay.providers.llm_base import LLMProvider
from relay.providers.llm_mock import MockLLMProvider
from relay.providers.tts_base import TTSProvider
from relay.providers.tts_mock import MockTTSProvider

log = logging.getLogger(__name__)


@dataclass
class Connectors:
    llm: LLMProvider
    tts: TTSProvider


def build_llm(s: Settings) -> LLMProvider:
    if resolve_mode(s.relay_llm_mode, s.anthropic_api_key) == "live":
        from relay.providers.llm_anthropic import AnthropicLLMProvider  # imports the SDK

        return AnthropicLLMProvider(
            s.anthropic_api_key,
            timeout_s=s.llm_timeout_s,
            server_fallback=s.anthropic_server_fallback,
        )
    return MockLLMProvider(hallucinate=s.relay_mock_hallucinate)


def build_tts(s: Settings) -> TTSProvider:
    if resolve_mode(s.relay_tts_mode, s.elevenlabs_api_key) == "live":
        from relay.providers.tts_elevenlabs import ElevenLabsProvider  # imports the SDK

        return ElevenLabsProvider(s.elevenlabs_api_key, timeout_s=s.elevenlabs_timeout_s)
    return MockTTSProvider()


def build_connectors(s: Settings) -> Connectors:
    return Connectors(
        llm=build_llm(s),
        tts=build_tts(s),
    )
