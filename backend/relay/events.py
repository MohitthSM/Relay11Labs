"""Events service: create, versioned update, resolve. Orchestrates script -> voice.

One call does the whole path (SPEC F1):
  validate -> Event row -> script engine (writes LLMCall + Announcement rows)
  -> voice.render_many (clips) -> state-change message on the bus.
Scheduling comes in Phase 5; nothing here plays audio.

Logging uses event ids only. An event_key can contain a passenger name.
"""

import logging
import re
from typing import Any

from pydantic import BaseModel
from sqlmodel import Session, col, select

from relay import bus, timeutil, voice
from relay.context import Context
from relay.models import ACTIVE, RESOLVED, SUPERSEDED, Announcement, Event, LLMCall
from relay.schemas import EventInput, LinePayload, PassengerPagePayload, StandChangePayload
from relay.script.engine import ScriptPlan, build_context, generate
from relay.sites import Site, load_sites

log = logging.getLogger(__name__)


class EventError(Exception):
    """A request the service refuses. `status` is the HTTP code the API should answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def derive_key(payload: BaseModel) -> str:
    """A stable key per real-world thing: one line, one page, one route."""
    if isinstance(payload, LinePayload):
        return f"{slug(payload.line)}-status"
    if isinstance(payload, PassengerPagePayload):
        return f"page-{payload.flight_number}-{slug(payload.passenger_name)}"
    if isinstance(payload, StandChangePayload):
        return f"stand-{payload.route}"
    raise EventError(422, "unknown event type")


def get_site_for(site_id: str, event_type: str | None = None) -> Site:
    site = load_sites().get(site_id)
    if site is None:
        raise EventError(404, "unknown site")
    if event_type is not None and event_type not in site.policies:
        raise EventError(422, f"site {site_id} does not announce {event_type}")
    return site


def active_event(session: Session, event_key: str) -> Event | None:
    return session.exec(
        select(Event).where(Event.event_key == event_key, Event.state == ACTIVE)
    ).first()


def create(
    session: Session,
    ctx: Context,
    *,
    site_id: str,
    payload: dict[str, Any],
    event_key: str | None = None,
    source: str = "api",
    staff_note: str | None = None,
) -> Event:
    """Version 1 of a new event. A bad payload raises ValidationError (the API answers 422)."""
    probe = EventInput(event_key=event_key or "-", site_id=site_id, payload=payload)  # type: ignore[arg-type]
    site = get_site_for(site_id, probe.type)
    key = event_key or derive_key(probe.payload)
    if active_event(session, key):
        raise EventError(409, "an active event with this key exists: update or resolve it")
    return _new_version(session, ctx, site, key, probe.payload.model_dump(), 1, source, staff_note)


def update(
    session: Session, ctx: Context, event_key: str, payload: dict[str, Any], *, source: str = "api"
) -> Event:
    """A new version of the active event with this key. The previous version is superseded."""
    current = active_event(session, event_key)
    if current is None:
        raise EventError(404, "no active event with this key")
    probe = EventInput(event_key=event_key, site_id=current.site_id, payload=payload)  # type: ignore[arg-type]
    if probe.type != current.type:
        raise EventError(422, "an update cannot change the event type")
    site = get_site_for(current.site_id, current.type)
    previous = _previous_drafts(session, current)
    current.state = SUPERSEDED
    session.add(current)
    return _new_version(
        session, ctx, site, event_key, probe.payload.model_dump(), current.version + 1,
        source, None, previous=previous,
    )  # fmt: skip


def resolve(
    session: Session, ctx: Context, event_key: str, payload: dict[str, Any] | None = None
) -> Event:
    """End the event. If the site has a resolution message for its type (SPEC F4), generate it
    through the same pipeline and fact lock. A line goes back to "good service"."""
    event = active_event(session, event_key)
    if event is None:
        raise EventError(404, "no active event with this key")
    site = get_site_for(event.site_id, event.type)
    event.state, event.resolved_at = RESOLVED, timeutil.now()
    session.add(event)
    if site.policies[event.type].resolution_plays > 0 and event.type == "line_status":
        wanted = payload or {"type": "line_status", "line": event.payload["line"]}
        wanted = {**wanted, "status": "good_service"}
        if payload and payload.get("status") not in (None, "good_service"):
            raise EventError(422, "a line is resolved with status good_service")
        res = EventInput(event_key=event_key, site_id=event.site_id, payload=wanted)  # type: ignore[arg-type]
        _announce(session, ctx, site, event, res, is_resolution=True)
    session.commit()
    session.refresh(event)
    _publish("event.resolved", event)
    log.info("event %s resolved", event.id)
    return event


# --- internals ------------------------------------------------------------------------------


def _new_version(
    session: Session,
    ctx: Context,
    site: Site,
    key: str,
    payload: dict[str, Any],
    version: int,
    source: str,
    staff_note: str | None,
    previous: list[str] | None = None,
) -> Event:
    event = Event(
        event_key=key,
        site_id=site.id,
        type=payload["type"],
        version=version,
        payload=payload,
        source=source,
        staff_note=staff_note,
        priority=site.policies[payload["type"]].priority,
    )
    session.add(event)
    session.flush()  # the id is needed for the child rows
    ev_input = EventInput(event_key=key, site_id=site.id, version=version, payload=payload)  # type: ignore[arg-type]
    _announce(session, ctx, site, event, ev_input, previous=previous or [])
    session.commit()
    session.refresh(event)
    _publish("event.created" if version == 1 else "event.updated", event)
    log.info("event %s v%d (%s) at %s", event.id, version, event.type, site.id)
    return event


def _announce(
    session: Session,
    ctx: Context,
    site: Site,
    event: Event,
    ev_input: EventInput,
    *,
    previous: list[str] | None = None,
    is_resolution: bool = False,
) -> list[Announcement]:
    """Script engine -> LLMCall and Announcement rows -> voice -> clip ids on the announcements."""
    assert event.id is not None
    context = build_context(site, ev_input.type, previous=previous)
    plan = generate(ev_input, context, ctx.conns.llm, ctx.settings, now=timeutil.now())
    announcements = _persist_plan(session, event.id, plan, is_resolution)
    first_language = context.languages[0]
    items = [
        voice.Item(
            text=a.spoken_text,
            language=a.language,
            kind=voice.render_kind(a.urgency, is_resolution=is_resolution),
            chime=a.language == first_language,  # one chime per sequence, in the first clip
            urgency=a.urgency,
            event_id=event.id,
            announcement_id=a.id,
        )
        for a in announcements
    ]
    rendered = voice.render_many(session, ctx.conns.tts, ctx.settings, site, items)
    for ann, result in zip(announcements, rendered, strict=True):
        ann.clip_id = result.clip_id
        ann.audio_error = None if result.error is None else f"{result.error.kind}: {result.error}"
        session.add(ann)
    return announcements


def _persist_plan(
    session: Session, event_id: int, plan: ScriptPlan, is_resolution: bool
) -> list[Announcement]:
    calls: list[LLMCall] = []
    for rec in plan.llm_calls:
        call = LLMCall(event_id=event_id, **rec.model_dump())
        session.add(call)
        calls.append(call)
    session.flush()
    rows = []
    for slot in plan.scripts:
        accepted = next((c for c in calls if slot.model and c.model == slot.model), None)
        ann = Announcement(
            event_id=event_id,
            language=slot.language,
            urgency=slot.urgency,
            is_resolution=is_resolution,
            draft=slot.draft,
            script=slot.text,
            spoken_text=slot.spoken_text,
            source=slot.source,
            model=slot.model,
            llm_call_id=accepted.id if accepted else None,
            rejections=[r.model_dump(mode="json") for r in slot.rejections],
        )
        session.add(ann)
        rows.append(ann)
    session.flush()
    return rows


def _previous_drafts(session: Session, event: Event) -> list[str]:
    """The placeholder draft the last version said first (for the 'This is an update' prompt)."""
    first = session.exec(
        select(Announcement)
        .where(Announcement.event_id == event.id, col(Announcement.is_resolution).is_(False))
        .order_by(col(Announcement.urgency), col(Announcement.id))
    ).first()
    return [first.draft] if first else []


def _publish(kind: str, event: Event) -> None:
    """A small, name-free message: clients re-fetch what they need."""
    bus.publish(
        {
            "type": kind,
            "event_id": event.id,
            "site_id": event.site_id,
            "event_type": event.type,
            "version": event.version,
            "state": event.state,
        }
    )
