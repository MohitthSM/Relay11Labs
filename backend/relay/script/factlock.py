"""The fact lock: may this script be announced? Pure checks, no I/O beyond cached lexicons.

`check()` takes the LLM's draft (with `{placeholders}`) and returns a list of violations; an
empty list means the script is accepted. Layers, cheapest first (ARCHITECTURE §4):

  format        forbidden characters ([ ] < > / \\), stray braces, too long, wrong language
  placeholders  unknown {name}; {name} for a fact this event does not have; required one missing
  digits        any digit in model text (times, numbers, codes) - facts only come via placeholders
  number_words  "twenty", "dos", "quinze" ... in model text
  proper_nouns  capitalised word mid-sentence that is not an allowed term; any gazetteer name
  claims        an operational claim the event does not support, or a required one missing
  urgency       an escalated page that does not sound urgent enough for its level
  filled_facts  after filling: every time, number, code and known place in the final text must
                come from the event's facts (a last cross-check that also covers templates)
"""

import re
from typing import Any

from pydantic import BaseModel

from relay.schemas import placeholder_fields
from relay.script.lexicons import LANGUAGES, Lexicon, get_lexicon
from relay.script.templates import PLACEHOLDER, fill
from relay.sites import Site

MAX_WORDS = {"en": 70, "es": 85, "fr": 85}
FORBIDDEN_CHARS = "[]<>/\\"
REQUIRED_PLACEHOLDERS = {
    "line_status": {"line"},
    "passenger_page": {"passenger_name", "flight_number", "gate"},
    "stand_change": {"route", "from_stand", "to_stand"},
}
ESCALATING_TYPES = {"passenger_page"}
URGENCY_FLOOR = {1: 0, 2: 2, 3: 3}  # minimum distinct urgency markers per level
ALWAYS_ALLOWED_CAPITALS = {"I"}
MARK = "§"  # stands in for a placeholder while scanning model text


class Violation(BaseModel):
    layer: str
    code: str
    detail: str
    span: str = ""

    def __str__(self) -> str:
        span = f" ({self.span!r})" if self.span else ""
        return f"{self.layer}/{self.code}: {self.detail}{span}"


def check(
    draft: str,
    *,
    event_type: str,
    facts: dict[str, Any],
    urgency: int,
    lang: str,
    site: Site,
    lexicon: Lexicon | None = None,
) -> list[Violation]:
    """All violations for one script slot. Empty list = accepted."""
    lex = lexicon or get_lexicon(lang)
    model_text = PLACEHOLDER.sub(MARK, draft)  # the words the model wrote itself
    out = check_format(draft, model_text, lang)
    out += check_placeholders(draft, event_type, facts)
    out += check_digits(model_text)
    out += check_number_words(model_text, lex)
    out += check_proper_nouns(model_text, site)
    out += check_claims(model_text, event_type, facts, urgency, lex)
    out += check_urgency(model_text, event_type, urgency, lex)
    if not any(v.layer == "placeholders" for v in out):
        out += check_filled_facts(fill(draft, facts), facts, site)
    return out


# ---------------------------------------------------------------- layer: format


def check_format(draft: str, model_text: str, lang: str) -> list[Violation]:
    out = []
    if not draft.strip():
        return [Violation(layer="format", code="empty", detail="script is empty")]
    for ch in FORBIDDEN_CHARS:
        if ch in draft:
            out.append(
                Violation(
                    layer="format",
                    code="forbidden_char",
                    detail="audio tags, SSML and IPA are added by code",
                    span=ch,
                )
            )
    if "{" in model_text or "}" in model_text:
        out.append(
            Violation(
                layer="format", code="stray_brace", detail="braces are only for {placeholders}"
            )
        )
    words = len(draft.split())
    if words > MAX_WORDS[lang]:
        out.append(
            Violation(
                layer="format", code="too_long", detail=f"{words} words, limit {MAX_WORDS[lang]}"
            )
        )
    if detect_language(model_text) != lang:
        out.append(
            Violation(
                layer="format", code="wrong_language", detail=f"script does not read as {lang!r}"
            )
        )
    return out


def detect_language(text: str) -> str | None:
    """Cheap check: the language whose stop-words appear most often. None if no clear winner."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    scores = {lang: sum(w in get_lexicon(lang).stopwords for w in words) for lang in LANGUAGES}
    best = max(scores, key=lambda lang: scores[lang])
    others = [s for lang, s in scores.items() if lang != best]
    return best if scores[best] > 0 and all(scores[best] > s for s in others) else None


# ---------------------------------------------------------------- layer: placeholders


def check_placeholders(draft: str, event_type: str, facts: dict[str, Any]) -> list[Violation]:
    out = []
    known = placeholder_fields(event_type)
    used = set(PLACEHOLDER.findall(draft))
    for name in sorted(used):
        if name not in known:
            out.append(
                Violation(
                    layer="placeholders",
                    code="unknown",
                    detail=f"{event_type} has no field {name!r}",
                    span=f"{{{name}}}",
                )
            )
        elif name not in facts:
            out.append(
                Violation(
                    layer="placeholders",
                    code="absent_fact",
                    detail=f"this event has no {name}",
                    span=f"{{{name}}}",
                )
            )
    for name in sorted(REQUIRED_PLACEHOLDERS[event_type] - used):
        out.append(
            Violation(
                layer="placeholders", code="missing", detail=f"script must include {{{name}}}"
            )
        )
    return out


# ---------------------------------------------------------------- layer: digits


def check_digits(model_text: str) -> list[Violation]:
    return [
        Violation(
            layer="digits",
            code="digit",
            detail="numbers must come from placeholders",
            span=m.group(0),
        )
        for m in re.finditer(r"\S*\d\S*", model_text)
    ]


# ---------------------------------------------------------------- layer: number words


def check_number_words(model_text: str, lex: Lexicon) -> list[Violation]:
    text = model_text.lower()
    for idiom in lex.number_allow:
        text = text.replace(idiom, " ")
    return [
        Violation(
            layer="number_words",
            code="number_word",
            detail="numbers must come from placeholders",
            span=word,
        )
        for word in lex.number_words
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text)
    ]


# ---------------------------------------------------------------- layer: proper nouns

SENTENCE_END = (".", "!", "?", ":")
STRIP = ".,;:!?¿¡«»“”\"'()’"


def check_proper_nouns(model_text: str, site: Site) -> list[Violation]:
    out = []
    allowed = set(site.allowed_terms) | ALWAYS_ALLOWED_CAPITALS
    sentence_start = True
    for token in model_text.split():
        core = token.strip(STRIP)
        starts_sentence = sentence_start or token[:1] in "¿¡"
        is_capitalised = bool(core) and not core.startswith(MARK) and core[0].isupper()
        if is_capitalised and not starts_sentence and core not in allowed:
            out.append(
                Violation(
                    layer="proper_nouns",
                    code="capitalised",
                    detail="names must come from placeholders",
                    span=core,
                )
            )
        sentence_start = token.rstrip("”\"'»)").endswith(SENTENCE_END)
    for name in site.gazetteer.names():
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", model_text):
            out.append(
                Violation(
                    layer="proper_nouns",
                    code="gazetteer",
                    detail="known place or line names only via placeholders",
                    span=name,
                )
            )
    return out


# ---------------------------------------------------------------- layer: claims


def allowed_claims(event_type: str, facts: dict[str, Any], urgency: int) -> set[str]:
    """Claim keys the event supports. Anything else in the claim lexicon is rejected."""
    allowed: set[str] = set()
    if event_type == "line_status":
        status = facts["status"]
        allowed.add(status)
        if status in ("minor_delays", "severe_delays", "part_suspended"):
            allowed.add("delayed")
        if status == "part_suspended":
            allowed.add("suspended")
        if facts.get("delay_minutes"):
            allowed.add("minutes")
    if event_type == "passenger_page":
        allowed.add(facts["page_type"])
        if urgency >= 3:
            allowed.add("closing")
    if facts.get("reason"):
        allowed.add(facts["reason"])
    return allowed


def required_claims(event_type: str, facts: dict[str, Any], urgency: int) -> set[str]:
    """Claims the script must make, so the announcement states the event's status and cause."""
    required = set()
    if event_type == "line_status":
        required.add(facts["status"])
    if event_type == "passenger_page" and urgency == 1:
        required.add(facts["page_type"])
    if facts.get("reason") and facts.get("status") != "good_service":  # no cause once resolved
        required.add(facts["reason"])
    return required


def find_claims(text: str, lex: Lexicon) -> dict[str, str]:
    """Claim key -> first matching span."""
    found = {}
    for key, patterns in lex.claims.items():
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                found[key] = m.group(0)
                break
    return found


def check_claims(
    model_text: str, event_type: str, facts: dict[str, Any], urgency: int, lex: Lexicon
) -> list[Violation]:
    found = find_claims(model_text, lex)
    allowed = allowed_claims(event_type, facts, urgency)
    out = [
        Violation(
            layer="claims",
            code="unsupported",
            detail=f"claim {key!r} is not in the event",
            span=span,
        )
        for key, span in found.items()
        if key not in allowed
    ]
    for key in sorted(required_claims(event_type, facts, urgency) - found.keys()):
        out.append(Violation(layer="claims", code="missing", detail=f"script must state {key!r}"))
    return out


# ---------------------------------------------------------------- layer: urgency


def urgency_score(text: str, lex: Lexicon) -> int:
    """How many distinct urgency markers the text contains."""
    lower = text.lower()
    return sum(bool(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", lower)) for m in lex.urgency)


def check_urgency(model_text: str, event_type: str, urgency: int, lex: Lexicon) -> list[Violation]:
    if event_type not in ESCALATING_TYPES:
        return []
    score, floor = urgency_score(model_text, lex), URGENCY_FLOOR[urgency]
    if score >= floor:
        return []
    return [
        Violation(
            layer="urgency",
            code="too_calm",
            detail=f"urgency {urgency} needs {floor} urgency markers, found {score}",
        )
    ]


# ---------------------------------------------------------------- layer: filled facts


def check_filled_facts(text: str, facts: dict[str, Any], site: Site) -> list[Violation]:
    """Extract times, numbers, codes and known names from the final text; each must be a fact."""
    values = [str(v) for v in facts.values()]
    fact_digits = {d for v in values for d in re.findall(r"\d+", v)}
    fact_upper = {v.upper() for v in values}
    out = []
    for code in re.findall(r"\b[A-Z]{1,3}\d+[A-Z]?\b", text):
        if code.upper() not in fact_upper:
            out.append(
                Violation(
                    layer="filled_facts", code="code", detail="code not in the event", span=code
                )
            )
    for digits in re.findall(r"\d+", text):
        if digits not in fact_digits:
            out.append(
                Violation(
                    layer="filled_facts",
                    code="number",
                    detail="number not in the event",
                    span=digits,
                )
            )
    for name in site.gazetteer.names():
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) and not any(
            name in v for v in values
        ):
            out.append(
                Violation(
                    layer="filled_facts",
                    code="name",
                    detail="place or line not in the event",
                    span=name,
                )
            )
    return out


def facts_used(draft: str, event_type: str, facts: dict[str, Any], lang: str) -> list[str]:
    """Fact fields this script states: its placeholders plus the claim facts it puts in words."""
    used = {n for n in PLACEHOLDER.findall(draft) if n in facts}
    claims = find_claims(PLACEHOLDER.sub(MARK, draft), get_lexicon(lang))
    for name in ("status", "reason", "page_type"):
        if name in facts and facts[name] in claims:
            used.add(name)
    return sorted(used)
