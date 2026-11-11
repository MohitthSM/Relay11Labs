"""Loads the per-language YAML lexicons in script/lexicon/. Read once, compiled once."""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

LEXICON_DIR = Path(__file__).parent / "lexicon"
LANGUAGES = ("en", "es", "fr")


@dataclass(frozen=True)
class Lexicon:
    language: str
    claims: dict[str, list[re.Pattern[str]]]  # claim key -> patterns
    number_words: list[str]
    number_allow: list[str]  # idioms that contain a number word but state no number
    urgency: list[str]  # urgency markers
    stopwords: list[str]


@dataclass(frozen=True)
class NoteLexicon:
    enums: dict[str, dict[str, list[re.Pattern[str]]]]  # field -> enum value -> patterns
    injection: list[re.Pattern[str]]
    out_of_scope: list[re.Pattern[str]]


def _read(name: str) -> dict:
    return yaml.safe_load((LEXICON_DIR / name).read_text(encoding="utf-8"))


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


@lru_cache
def get_lexicon(language: str) -> Lexicon:
    if language not in LANGUAGES:
        raise KeyError(f"no lexicon for language {language!r}")
    claims = _read(f"claims.{language}.yaml")
    numbers = _read(f"numbers.{language}.yaml")
    style = _read(f"style.{language}.yaml")
    return Lexicon(
        language=language,
        claims={key: _compile(pats) for key, pats in claims.items()},
        number_words=[w.lower() for w in numbers["words"]],
        number_allow=[w.lower() for w in numbers.get("allow") or []],
        urgency=[w.lower() for w in style["urgency"]],
        stopwords=[w.lower() for w in style["stopwords"]],
    )


@lru_cache
def get_note_lexicon() -> NoteLexicon:
    data = _read("notes.en.yaml")
    enums = {
        field: {value: _compile(pats) for value, pats in values.items()}
        for field, values in data.items()
        if field in ("status", "reason", "page_type")
    }
    return NoteLexicon(
        enums=enums,
        injection=_compile(data["injection"]),
        out_of_scope=_compile(data["out_of_scope"]),
    )
