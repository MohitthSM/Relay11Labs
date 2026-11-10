"""Staff note -> draft event, for a human to confirm. The note is untrusted input.

Defences, in order:
  1. screen: notes that look like instructions to the system ("ignore your instructions and
     announce ...") or that are safety-critical (evacuation, fire, security) are refused before
     any model sees them.
  2. the model sees the note only as data inside <staff_note> tags, and can only answer with
     the typed NoteExtraction fields (structured output). It cannot write announcement text.
  3. extraction lock: every extracted value must appear in the note (known names via the site
     gazetteer, enums via the note lexicon). Anything else is dropped and shown as "not found".
  4. the typed payload is validated against schemas.py (formats such as gate or flight number).
  5. the note itself never reaches the script prompt; scripts are written from the typed
     payload and pass the normal fact lock.
  6. nothing plays without a human clicking Broadcast (this module only returns a draft).
If the model fails, the deterministic rules in note_rules.py extract the fields instead.
"""

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from relay.config import Settings
from relay.providers.llm_base import ExtractRequest, LLMProvider, NoteExtraction
from relay.schemas import PAYLOAD_MODELS, fact_fields
from relay.script.calls import LLMCallRecord, call_llm
from relay.script.lexicons import get_note_lexicon
from relay.script.note_rules import rule_extract
from relay.script.prompt import note_system, note_user
from relay.sites import Site

log = logging.getLogger(__name__)

MAX_NOTE_CHARS = 280
GAZETTEER_FIELDS = {"line": "lines", "location": "stations", "destination": "cities"}
ENUM_FIELDS = ("status", "reason", "page_type")


class NoteDraft(BaseModel):
    status: Literal["ready", "incomplete", "rejected"]
    detail: str = ""  # why it was rejected
    event_type: str | None = None
    payload: dict[str, Any] | None = None  # validated payload, when ready
    fields: dict[str, Any] = Field(default_factory=dict)  # values found in the note
    dropped: dict[str, str] = Field(default_factory=dict)  # field -> why it was not kept
    missing: list[str] = Field(default_factory=list)  # required fields the note lacks
    source: Literal["llm", "rules", "none"] = "none"
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)


def clean_note(note: str) -> str:
    """Drop control and invisible characters; collapse whitespace."""
    visible = "".join(ch if ch.isprintable() else " " for ch in note)
    return " ".join(visible.split())


def screen(note: str) -> str | None:
    """A reason to refuse the note outright, or None."""
    if len(note) > MAX_NOTE_CHARS:
        return f"note is longer than {MAX_NOTE_CHARS} characters"
    lex = get_note_lexicon()
    if any(p.search(note) for p in lex.out_of_scope):
        return "safety-critical announcements are out of scope; use the pre-recorded messages"
    if any(p.search(note) for p in lex.injection):
        return "the note reads like an instruction to the system; type only the facts"
    return None


def draft_from_note(note: str, site: Site, llm: LLMProvider, settings: Settings) -> NoteDraft:
    note = clean_note(note)
    if not note:
        return NoteDraft(status="rejected", detail="note is empty")
    refusal = screen(note)
    if refusal:
        log.info("staff note refused by screen")
        return NoteDraft(status="rejected", detail=refusal)

    system, version = note_system(site, settings.prompts_dir)
    req = ExtractRequest(system=system, user=note_user(note), note=note, site=site)
    model = settings.script_model_escalation  # all staff notes go to the stronger model (D3)
    result, rec = call_llm(
        lambda: llm.extract_event(req, model),
        purpose="extract",
        attempt=1,
        model=model,
        prompt_version=version,
    )
    extraction, source = None, "llm"
    if result is not None:
        try:
            extraction = NoteExtraction.model_validate_json(result.text)
        except ValidationError:
            rec.outcome, rec.error = "invalid", "output was not a valid extraction"
    if extraction is None:
        extraction, source = NoteExtraction(**rule_extract(note, site)), "rules"
    elif extraction.looks_like_instruction:
        rec.outcome = "rejected"
        return NoteDraft(
            status="rejected",
            source=source,
            llm_calls=[rec],
            detail="the note reads like an instruction to the system; type only the facts",
        )

    fields, dropped = lock_extraction(extraction, note, site)
    if dropped and rec.outcome == "accepted":
        rec.outcome = "rejected"
    event_type = extraction.event_type
    if event_type is not None and event_type not in site.policies:
        return NoteDraft(
            status="rejected",
            source=source,
            llm_calls=[rec],
            event_type=event_type,
            detail=f"{site.name} does not announce {event_type} events",
        )
    draft = build_draft(event_type, fields, dropped)
    return draft.model_copy(update={"source": source, "llm_calls": [rec]})


# ---------------------------------------------------------------- extraction lock


def lock_extraction(
    ext: NoteExtraction, note: str, site: Site
) -> tuple[dict[str, Any], dict[str, str]]:
    """Keep only values that are in the note. Returns (kept fields, dropped field -> reason)."""
    kept: dict[str, Any] = {}
    dropped: dict[str, str] = {}
    tokens = re.findall(r"[^\W_]+", note.lower())
    for name, value in ext.model_dump(exclude={"event_type", "looks_like_instruction"}).items():
        if value is None or value == "":
            continue
        ok, canonical = _in_note(name, value, note, tokens, site)
        if ok:
            kept[name] = canonical
        else:
            dropped[name] = f"{value!r} not found in note"
    return kept, dropped


def _in_note(name: str, value: Any, note: str, tokens: list[str], site: Site) -> tuple[bool, Any]:
    if name in ENUM_FIELDS:
        patterns = get_note_lexicon().enums[name].get(str(value), [])
        return any(p.search(note) for p in patterns), value
    if name in GAZETTEER_FIELDS:
        gaz = site.gazetteer
        known = getattr(gaz, GAZETTEER_FIELDS[name])
        canonical = next((k for k in known if k.lower() == str(value).lower()), None)
        if canonical is None:
            return False, value  # not a place or line this site knows
        spellings = gaz.spellings(canonical)
        found = any(
            re.search(rf"(?<!\w){re.escape(s)}(?!\w)", note, re.IGNORECASE) for s in spellings
        )
        return found, canonical
    if name == "delay_minutes":
        return str(value) in tokens, value
    if name == "passenger_name":
        words = re.findall(r"[^\W_]+", str(value).lower())
        joined, haystack = " ".join(words), " ".join(tokens)
        return bool(words) and re.search(rf"\b{re.escape(joined)}\b", haystack) is not None, value
    # codes: flight, gate, route, stands. Must be one token, or two adjacent tokens ("ba 117").
    squashed = re.sub(r"[^\w]", "", str(value).lower())
    pairs = {a + b for a, b in zip(tokens, tokens[1:], strict=False)}
    return squashed in set(tokens) | pairs, str(value).upper()


def build_draft(
    event_type: str | None, fields: dict[str, Any], dropped: dict[str, str]
) -> NoteDraft:
    if event_type is None:
        return NoteDraft(
            status="incomplete", fields=fields, dropped=dropped, missing=["event_type"]
        )
    relevant = {k: v for k, v in fields.items() if k in fact_fields(event_type)}
    try:
        payload = PAYLOAD_MODELS[event_type].model_validate({"type": event_type, **relevant})
    except ValidationError as exc:
        missing = []
        for err in exc.errors():
            field = str(err["loc"][0]) if err["loc"] else "payload"
            if err["type"] == "missing":
                missing.append(field)
            else:
                dropped[field] = f"invalid: {err['msg']}"
        return NoteDraft(
            status="incomplete",
            event_type=event_type,
            fields=relevant,
            dropped=dropped,
            missing=sorted(missing),
        )
    return NoteDraft(
        status="ready",
        event_type=event_type,
        fields=relevant,
        dropped=dropped,
        payload=payload.model_dump(mode="json"),
    )
