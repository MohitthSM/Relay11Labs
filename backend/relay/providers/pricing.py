"""USD price tables. Used only for logging and metrics."""

# (input, output) USD per million tokens
LLM_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
# USD per 1K characters
TTS_PER_1K_CHARS = {"eleven_v4_turbo": 0.04, "eleven_v4": 0.08}


def llm_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = LLM_PER_MTOK.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def tts_cost(model_id: str, characters: int) -> float:
    return characters / 1000 * TTS_PER_1K_CHARS.get(model_id, 0.0)
