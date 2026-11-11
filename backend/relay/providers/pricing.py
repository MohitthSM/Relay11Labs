"""USD price tables. Used only for logging and metrics.

Checked 2026-11-11: Claude at platform.claude.com pricing (Haiku 5.5 rates are for prompts up to
100K tokens; Relay's are about 2K), ElevenLabs at elevenlabs.io/pricing/api. A model missing
from a table costs $0 in the metrics, so it is logged once as a warning.
"""

import logging
from functools import cache

log = logging.getLogger(__name__)

# (input, output) USD per million tokens
LLM_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
# USD per 1K characters
TTS_PER_1K_CHARS = {"eleven_v4_turbo": 0.04, "eleven_v4": 0.08}


@cache
def _warn_unpriced(model: str) -> None:
    log.warning("no price for model %s: its cost shows as $0 in the metrics", model)


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in LLM_PER_MTOK:
        _warn_unpriced(model)
    price_in, price_out = LLM_PER_MTOK.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def tts_cost(model_id: str, characters: int) -> float:
    if model_id not in TTS_PER_1K_CHARS:
        _warn_unpriced(model_id)
    return characters / 1000 * TTS_PER_1K_CHARS.get(model_id, 0.0)
