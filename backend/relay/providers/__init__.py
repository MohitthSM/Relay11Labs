"""Provider factory. Only this package may import vendor SDKs (anthropic, elevenlabs, slack).

Live implementations are not written yet: a key plus mode=live still resolves to the mock and
logs a warning, so the demo never breaks (D13). `mode` on each provider says what you got.
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


def build_connectors(s: Settings) -> Connectors:
    _warn_if_live("llm", s.relay_llm_mode, s.anthropic_api_key)
    _warn_if_live("tts", s.relay_tts_mode, s.elevenlabs_api_key)
    _warn_if_live("chat", s.relay_chat_mode, s.slack_bot_token)
    _warn_if_live("crm", s.relay_crm_mode, s.salesforce_access_token)
    return Connectors(
        llm=MockLLMProvider(),
        chat=MockChatProvider(),
        crm=MockSalesforceProvider(),
        tts=MockTTSProvider(),
    )
