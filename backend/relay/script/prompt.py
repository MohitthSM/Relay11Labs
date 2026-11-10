"""Builds LLM prompts. System prompts are versioned files in backend/prompts/:

- script_<hub>.vN.md   one per hub type (underground, airport, bus), with house style and
                       few-shot examples; it includes script_rules.vN.md as $rules
- note_extract.vN.md   staff note -> event fields

The user message is built here from the event. Value facts appear only as placeholder names,
never as values, so the model cannot copy a value wrongly and passenger names are not sent to
the LLM. Claim facts (status, reason, page type) are given as enum values.
"""

from functools import lru_cache
from typing import Any

from relay.prompts import Prompt, load_prompt
from relay.schemas import fact_fields, placeholder_fields
from relay.sites import Site

HUB_PROMPTS = {
    "underground": "script_underground",
    "airport": "script_airport",
    "bus": "script_bus",
}
RULES_PROMPT = "script_rules"
NOTE_PROMPT = "note_extract"


@lru_cache
def _load(prompts_dir: str, name: str) -> Prompt:
    return load_prompt(prompts_dir, name)


def slot_key(lang: str, urgency: int) -> str:
    return f"{lang}/{urgency}"


def script_system(site: Site, prompts_dir: str) -> tuple[str, str]:
    """(system prompt, prompt version) for this site's hub type."""
    hub = _load(prompts_dir, HUB_PROMPTS[site.hub_type])
    rules = _load(prompts_dir, RULES_PROMPT)
    text = hub.render(style_guide=site.style_guide.strip(), rules=rules.template.strip())
    return text, f"{hub.ref}+{rules.ref}"


def script_user(
    event_type: str,
    facts: dict[str, Any],
    slots: list[tuple[str, int]],
    *,
    previous: list[str] | None = None,
    update: bool = False,
    feedback: dict[str, list[str]] | None = None,
) -> str:
    claims = [f"- {n} = {facts[n]}" for n in fact_fields(event_type, claim=True) if n in facts]
    holders = placeholder_fields(event_type)
    usable = [f"- {{{n}}}: {desc}" for n, desc in holders.items() if n in facts]
    absent = [f"{{{n}}}" for n in holders if n not in facts]
    lines = [
        f"Event type: {event_type}",
        f"This updates an earlier announcement: {'yes' if update else 'no'}",
        "",
        "Facts said in words (state these, add nothing else):",
        *(claims or ["- (none)"]),
        "",
        "Placeholders you may use (code fills them in):",
        *usable,
    ]
    if absent:
        lines.append(f"Not in this event, do not mention: {', '.join(absent)}")
    if previous:
        lines += ["", "<previous_announcements>", *previous, "</previous_announcements>"]
    if feedback:
        lines += ["", "Your previous scripts for these slots were rejected. Fix every problem:"]
        lines += [f"- {slot}: {'; '.join(reasons)}" for slot, reasons in feedback.items()]
    wanted = ", ".join(slot_key(lang, u) for lang, u in slots)
    lines += ["", f"Write exactly one script for each slot (language/urgency): {wanted}."]
    return "\n".join(lines)


def note_system(site: Site, prompts_dir: str) -> tuple[str, str]:
    prompt = _load(prompts_dir, NOTE_PROMPT)
    gaz = site.gazetteer
    known = [
        f"  {label}: {', '.join(names)}"
        for label, names in (
            ("lines", gaz.lines),
            ("stations", gaz.stations),
            ("cities", gaz.cities),
        )
        if names
    ]
    return prompt.render(known_names="\n".join(known) or "  (none)"), prompt.ref


def note_user(note: str) -> str:
    """The note as data. Angle brackets are swapped so the note cannot close its own tag."""
    safe = note.replace("<", "‹").replace(">", "›")
    return f"<staff_note>\n{safe}\n</staff_note>"
