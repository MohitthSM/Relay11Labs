"""Rank the account's voices for PA announcements (used by scripts/pick_voice.py). Pure.

A PA voice must be one voice in every language a site speaks (CLAUDE.md), calm and clear, and
must not be about to disappear (the legacy default voices end on 2026-12-31). The score only
orders the shortlist: the final choice is made by listening to it in EN, ES and FR.
"""

import re

from relay.providers.tts_base import VoiceInfo

PA_WORDS = re.compile(r"announc|narrat|news|informative|broadcast|clear|calm|professional", re.I)
AVOID_WORDS = re.compile(r"character|animation|whisper|asmr|meditat|gaming|villain", re.I)


def pa_score(voice: VoiceInfo, languages: list[str]) -> int:
    """Higher is better. Languages count most: a voice not verified in a site language may still
    speak it (v4 is multilingual) but nobody has checked it."""
    text = " ".join([voice.description, *voice.labels.values()])
    score = 3 * sum(lang in voice.languages for lang in languages)
    score += 2 if PA_WORDS.search(text) else 0
    score -= 3 if AVOID_WORDS.search(text) else 0
    score += 1 if voice.labels.get("accent", "").lower() in {"british", "english", "uk"} else 0
    score -= 10 if voice.legacy else 0
    return score


def shortlist(voices: list[VoiceInfo], languages: list[str], top: int = 10) -> list[VoiceInfo]:
    ranked = sorted(voices, key=lambda v: (-pa_score(v, languages), v.name))
    return ranked[:top]
