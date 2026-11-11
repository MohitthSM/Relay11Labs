"""Provider factory. Only this package may import vendor SDKs (anthropic, elevenlabs, slack).

The LLM is live (Claude) when ANTHROPIC_API_KEY is set and RELAY_LLM_MODE is not "mock".
TTS is live (ElevenLabs) when ELEVENLABS_API_KEY is set and RELAY_TTS_MODE is not "mock".
Chat and CRM live implementations are not written yet: a key plus mode=live still resolves
to the mock and logs a warning, so the demo never breaks (D13). `mode` on each provider says
what you got.
"""

import logging
from dataclasses import dataclass

from relay.config import Settings, resolve_mode
from relay.providers.chat_base import ChatProvider
from relay.providers.chat_mock import MockChatProvider
from relay.providers.crm_base import CRMProvider
from relay.providers.crm_mock import MockSalesforceProvider
from relay.providers.llm_base import LLMProvider
from relay.providers.llm_mock import MockLLMProvider
from relay.providers.tts_base import TTSProvider
from relay.providers.tts_mock import MockTTSProvider

log = logging.getLogger(__name__)


@dataclass
class Connectors:
    llm: LLMProvider
    chat: ChatProvider
    crm: CRMProvider
    tts: TTSProvider


def _warn_if_live(name: str, mode: str, key: str) -> None:
    if resolve_mode(mode, key) == "live":  # type: ignore[arg-type]
        log.warning("%s: live provider not implemented yet, using mock", name)


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
    _warn_if_live("chat", s.relay_chat_mode, s.slack_bot_token)
    _warn_if_live("crm", s.relay_crm_mode, s.salesforce_access_token)
    return Connectors(
        llm=build_llm(s),
        chat=MockChatProvider(),
        crm=MockSalesforceProvider(),
        tts=build_tts(s),
    )
