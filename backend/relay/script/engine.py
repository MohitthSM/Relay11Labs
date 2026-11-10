"""Script engine: event + context -> fact-locked scripts per language and urgency (ScriptPlan).

For each (language, urgency) slot:
  1. one batched call on SCRIPT_MODEL_DEFAULT for all slots
  2. fact lock per slot (factlock.check)
  3. failed slots are retried ONCE on SCRIPT_MODEL_ESCALATION, with the violations as feedback
  4. any slot still failing gets its deterministic template (per slot, not per event: D2)
A timeout falls straight to templates (R10: the announcement must not wait twice).
A refusal or provider error on attempt 1 goes to the escalation model like a rejection.

The engine only returns data. The events service persists it (LLMCall and Announcement rows).
"""

import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from relay.config import Settings
from relay.providers.errors import TIMEOUT
from relay.providers.llm_base import LLMProvider, ScriptBatch, ScriptRequest
from relay.schemas import EventInput
from relay.script import templates
from relay.script.calls import LLMCallRecord, call_llm
from relay.script.factlock import Violation, check, facts_used
from relay.script.normalize import spoken_text
from relay.script.prompt import script_system, script_user, slot_key
from relay.sites import Site

log = logging.getLogger(__name__)

Slot = tuple[str, int]  # (language, urgency)


class ScriptContext(BaseModel):
    """What the engine needs besides the event."""

    site: Site  # hub type, house style guide, gazetteer, policies
    languages: list[str]  # languages of the target zones, in play order
    previous_announcements: list[str] = Field(default_factory=list)  # earlier drafts, this event
    repeat_count: int = 0  # plays already done; an escalating page starts at urgency count + 1

    @property
    def hub_type(self) -> str:
        return self.site.hub_type

    @property
    def style_guide(self) -> str:
        return self.site.style_guide


def build_context(
    site: Site, event_type: str, previous: list[str] | None = None, repeat_count: int = 0
) -> ScriptContext:
    return ScriptContext(
        site=site,
        languages=site.languages_for(event_type),
        previous_announcements=previous or [],
        repeat_count=repeat_count,
    )


class Rejection(BaseModel):
    attempt: int
    model: str
    violations: list[Violation]


class ScriptSlot(BaseModel):
    language: str
    urgency: int
    draft: str  # as written, with {placeholders}
    text: str  # placeholders filled: what the fact lock approved
    spoken_text: str  # after normalize.py: what goes to TTS
    source: Literal["llm", "template"]
    model: str | None = None  # model that wrote an accepted LLM script
    facts_used: list[str]
    rejections: list[Rejection] = Field(default_factory=list)


class ScriptPlan(BaseModel):
    event_key: str
    event_type: str
    version: int
    scripts: list[ScriptSlot]  # ordered by urgency, then language play order
    priority: int
    repeat_interval_s: int
    max_plays: int | None
    expires_at: datetime | None
    escalate: bool
    zones: list[str]
    facts_used: list[str]
    llm_calls: list[LLMCallRecord]

    def script(self, language: str, urgency: int = 1) -> ScriptSlot:
        return next(s for s in self.scripts if (s.language, s.urgency) == (language, urgency))


def plan_slots(
    event_type: str, languages: list[str], repeat_count: int, escalate: bool
) -> list[Slot]:
    """Slots to write. An escalating event skips levels it has already played past."""
    levels = templates.URGENCY_LEVELS[event_type]
    if escalate:
        current = min(repeat_count + 1, max(levels))
        levels = [u for u in levels if u >= current]
    return [(lang, u) for u in levels for lang in languages]


def generate(
    event: EventInput,
    ctx: ScriptContext,
    llm: LLMProvider,
    settings: Settings,
    *,
    now: datetime,
) -> ScriptPlan:
    policy = ctx.site.policies[event.type]
    facts = event.facts()
    update = bool(ctx.previous_announcements)
    slots = plan_slots(event.type, ctx.languages, ctx.repeat_count, policy.escalate)
    system, prompt_version = script_system(ctx.site, settings.prompts_dir)

    accepted: dict[Slot, ScriptSlot] = {}
    rejections: dict[Slot, list[Rejection]] = {slot: [] for slot in slots}
    calls: list[LLMCallRecord] = []
    pending, feedback = list(slots), {}
    models = ((1, settings.script_model_default), (2, settings.script_model_escalation))
    for attempt, model in models:
        req = ScriptRequest(
            system=system,
            user=script_user(
                event.type,
                facts,
                pending,
                previous=ctx.previous_announcements,
                update=update,
                feedback=feedback,
            ),
            event_type=event.type,
            facts=facts,
            slots=pending,
            update=update,
            feedback=feedback,
        )
        result, rec = call_llm(
            lambda req=req, model=model: llm.generate_scripts(req, model),
            purpose="script",
            attempt=attempt,
            model=model,
            prompt_version=prompt_version,
        )
        calls.append(rec)
        if result is None:
            if rec.outcome == TIMEOUT:
                break  # no second wait: templates now
            continue  # refused or error: the escalation model gets one try
        drafts, problem = parse_batch(result.text)
        if problem:
            rec.outcome, rec.error = "invalid", problem
        failed: dict[Slot, list[Violation]] = {}
        for slot in pending:
            lang, urgency = slot
            draft = drafts.get(slot)
            if draft is None:
                violations = [
                    Violation(
                        layer="format",
                        code="missing_slot",
                        detail=problem or "no script for this slot",
                    )
                ]
            else:
                violations = check(
                    draft,
                    event_type=event.type,
                    facts=facts,
                    urgency=urgency,
                    lang=lang,
                    site=ctx.site,
                )
            if violations:
                failed[slot] = violations
                rejections[slot].append(
                    Rejection(attempt=attempt, model=result.model, violations=violations)
                )
            else:
                accepted[slot] = _slot(draft, slot, event.type, facts, "llm", result.model)
        if failed and rec.outcome == "accepted":
            rec.outcome = "rejected"
        rec.violations = {slot_key(*s): [str(v) for v in vs] for s, vs in failed.items()}
        pending = [s for s in pending if s in failed]
        feedback = rec.violations
        if not pending:
            break

    scripts = []
    for slot in slots:
        if slot not in accepted:
            accepted[slot] = _template_slot(slot, event.type, facts, update, ctx.site)
        scripts.append(accepted[slot].model_copy(update={"rejections": rejections[slot]}))

    n_llm = sum(s.source == "llm" for s in scripts)
    log.info(
        "scripts for %s v%d: %d llm, %d template, %d calls",
        event.type,
        event.version,
        n_llm,
        len(scripts) - n_llm,
        len(calls),
    )
    return ScriptPlan(
        event_key=event.event_key,
        event_type=event.type,
        version=event.version,
        scripts=scripts,
        priority=policy.priority,
        repeat_interval_s=policy.interval_s,
        max_plays=policy.max_plays,
        expires_at=now + timedelta(seconds=policy.duration_s) if policy.duration_s else None,
        escalate=policy.escalate,
        zones=policy.zones,
        facts_used=sorted({f for s in scripts for f in s.facts_used}),
        llm_calls=calls,
    )


def parse_batch(text: str) -> tuple[dict[Slot, str], str | None]:
    """LLM JSON -> {slot: draft}. Returns (drafts, problem) where problem explains bad output."""
    try:
        batch = ScriptBatch.model_validate_json(text)
    except ValidationError as exc:
        return {}, f"output was not a valid script batch: {exc.error_count()} error(s)"
    drafts: dict[Slot, str] = {}
    for line in batch.scripts:
        drafts.setdefault((line.language, line.urgency), line.text.strip())
    return drafts, None


def _slot(
    draft: str, slot: Slot, event_type: str, facts: dict, source: str, model: str | None
) -> ScriptSlot:
    lang, urgency = slot
    text = templates.fill(draft, facts)
    return ScriptSlot(
        language=lang,
        urgency=urgency,
        draft=draft,
        text=text,
        spoken_text=spoken_text(text),
        source=source,
        model=model,
        facts_used=facts_used(draft, event_type, facts, lang),
    )


def _template_slot(
    slot: Slot, event_type: str, facts: dict, update: bool, site: Site
) -> ScriptSlot:
    lang, urgency = slot
    draft = templates.draft(event_type, facts, urgency, lang, update=update)
    problems = check(
        draft, event_type=event_type, facts=facts, urgency=urgency, lang=lang, site=site
    )
    if problems:  # a template bug; the tests prevent this, but never block an announcement on it
        log.error("template %s/%s/%d fails the lock: %s", event_type, lang, urgency, problems)
    return _slot(draft, slot, event_type, facts, "template", None)
