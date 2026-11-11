"""Approved script -> spoken text (what is sent to TTS). Runs after the fact lock. Pure.

Codes are spaced out so every voice reads them letter by letter and digit by digit:
"BA117" -> "B A 1 1 7". Plain numbers ("Gate 23") are left for the voice to read as numbers.
No event field carries a clock time yet, so there is no time phrasing step.
Audio tags and IPA will be inserted here in Phase 8, never by the LLM.
"""

import re

CODE = re.compile(r"\b([A-Z]{1,3})(\d{1,4})([A-Z]?)\b")


def spoken_text(script: str) -> str:
    text = CODE.sub(lambda m: " ".join("".join(m.groups())), script)
    return " ".join(text.split())
