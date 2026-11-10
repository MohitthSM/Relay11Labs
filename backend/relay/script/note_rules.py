"""Deterministic staff-note parser: regexes, the site gazetteer and the note lexicon.

Used by the mock LLM (so staff notes work with no keys) and as the fallback when the live
extraction fails. Returns a plain dict with NoteExtraction's field names. Pure; it does not
import providers, so the mock provider can import it without a cycle.
"""

import re
from typing import Any

from relay.script.lexicons import get_note_lexicon
from relay.sites import Gazetteer, Site

TITLES = r"(?:mr|mrs|ms|miss|mx|dr|passenger)"
NAME_STOP = {
    "gate",
    "flight",
    "to",
    "on",
    "for",
    "travelling",
    "final",
    "call",
    "last",
    "at",
    "boarding",
    "please",
    "page",
    "paging",
    "the",
    "and",
}
FLIGHT = re.compile(r"\b([a-z]{2}|[a-z]\d|\d[a-z])(\d{1,4})\b", re.IGNORECASE)
GATE = re.compile(r"\bgate\s*([a-z]?\d{1,3}[a-z]?)\b", re.IGNORECASE)
ROUTE = re.compile(r"\b(?:route|bus)\s*([a-z]{0,2}\d{1,3}[a-z]?)\b", re.IGNORECASE)
STANDS = re.compile(
    r"\bstand\s+([a-z]{1,2}\d{0,2})\s*(?:to|->|→|now|moved to)\s*"
    r"(?:stand\s+)?([a-z]{1,2}\d{0,2})\b",
    re.IGNORECASE,
)
MINUTES = re.compile(r"\b(\d{1,3})\s*(?:min|mins|minute|minutes)\b", re.IGNORECASE)


def find_known(note: str, names: list[str], gazetteer: Gazetteer) -> str | None:
    """The canonical name whose spelling (or alias) appears in the note, case-insensitively."""
    hits = []
    for canonical in names:
        for spelling in gazetteer.spellings(canonical):
            if re.search(rf"(?<!\w){re.escape(spelling)}(?!\w)", note, re.IGNORECASE):
                hits.append((len(spelling), canonical))
    return max(hits)[1] if hits else None


def find_enum(field: str, note: str) -> str | None:
    for value, patterns in get_note_lexicon().enums[field].items():
        if any(p.search(note) for p in patterns):
            return value
    return None


def find_name(note: str) -> str | None:
    m = re.search(rf"\b{TITLES}\.?\s+(.+)", note, re.IGNORECASE)
    if not m:
        return None
    words = []
    for word in m.group(1).split():
        clean = word.strip(".,;:")
        if not re.fullmatch(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", clean) or clean.lower() in NAME_STOP:
            break
        words.append(clean[0].upper() + clean[1:])
        if len(words) == 3 or clean != word:  # punctuation ends the name
            break
    return " ".join(words) or None


def rule_extract(note: str, site: Site) -> dict[str, Any]:
    gaz = site.gazetteer
    out: dict[str, Any] = {
        "status": find_enum("status", note),
        "reason": find_enum("reason", note),
        "page_type": find_enum("page_type", note),
        "line": find_known(note, gaz.lines, gaz),
        "location": find_known(note, gaz.stations, gaz),
        "destination": find_known(note, gaz.cities, gaz),
        "passenger_name": find_name(note),
    }
    if m := MINUTES.search(note):
        out["delay_minutes"] = int(m.group(1))
    if m := GATE.search(note):
        out["gate"] = m.group(1).upper()
    if m := FLIGHT.search(GATE.sub(" ", note)):
        out["flight_number"] = (m.group(1) + m.group(2)).upper()
    if m := ROUTE.search(note):
        out["route"] = m.group(1).upper()
    if m := STANDS.search(note):
        out["from_stand"], out["to_stand"] = m.group(1).upper(), m.group(2).upper()
    if out.get("passenger_name") or out.get("flight_number") or out.get("gate"):
        out["event_type"] = "passenger_page"
    elif out.get("route") or out.get("to_stand"):
        out["event_type"] = "stand_change"
    elif out.get("line"):
        out["event_type"] = "line_status"
    return {k: v for k, v in out.items() if v is not None}
