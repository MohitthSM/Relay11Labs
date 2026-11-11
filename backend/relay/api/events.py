"""Event endpoints.

POST /events            create version 1 (409 if an active event has the key; 200 and the active
                        event, with duplicate=true, if an identical event is already active)
PUT  /events/{key}      a new version of the active event (the same facts again: duplicate=true)
POST /events/{id}/resolve
POST /events/{id}/approve   an operator releases an event that waits for approval (D20)
GET  /events, GET /events/{id}   (the detail has announcements and audio urls; the list takes
                        ?detail=true to include them)
POST /events/from-note  a DRAFT for the agent to confirm (SPEC F2). Creates nothing, plays nothing.
POST /events/preview    the scripts and fact-lock result for a payload, and what submitting it
                        would do (create, update, duplicate; waits for approval or not). Creates
                        nothing, plays nothing, writes no rows.

A provider failure never gives a 500: scripts fall back to templates and a failed clip shows as
`audio_error` on its announcement.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, col, select

from relay import events as events_service
from relay import jobs, timeutil
from relay.context import Context
from relay.events import EventError
from relay.models import Announcement, AudioClip, Event
from relay.schemas import EventInput
from relay.script.engine import ScriptSlot, build_context, generate
from relay.script.notes import NoteDraft, draft_from_note
from relay.sites import load_sites


class NoteIn(BaseModel):
    site_id: str
    note: str = Field(max_length=2000)  # screen() applies the real limit and says why


class NoteDraftOut(BaseModel):
    draft: NoteDraft
    preview: ScriptSlot | None = None  # first-language, urgency-1 script, when the draft is ready


class PreviewIn(BaseModel):
    site_id: str
    payload: dict[str, Any]
    source: Literal["form", "staff_note", "api"] = "form"


class PreviewOut(BaseModel):
    """What the operator sees before broadcasting: every script with its fact-check result."""

    event_key: str
    action: Literal["create", "update", "duplicate"]
    active_event_id: int | None  # the event this would update (or already is)
    needs_approval: str | None  # why an operator must approve it first, or None: plays at once
    scripts: list[ScriptSlot]  # each has source (llm | template) and its fact-lock rejections


class EventIn(BaseModel):
    site_id: str
    payload: dict[str, Any]
    event_key: str | None = None
    source: Literal["form", "staff_note", "api"] = "api"
    staff_note: str | None = Field(default=None, max_length=2000)


class EventUpdateIn(BaseModel):
    payload: dict[str, Any]


class ResolveIn(BaseModel):
    payload: dict[str, Any] | None = None


class ApproveIn(BaseModel):
    operator: str = Field(
        min_length=1, max_length=60, pattern=r"\S"
    )  # who approved: goes in the audit log


class AnnouncementOut(BaseModel):
    id: int
    language: str
    urgency: int
    is_resolution: bool
    script: str
    spoken_text: str
    source: str
    model: str | None
    rejections: list[Any]
    clip_id: str | None
    audio_url: str | None
    duration_s: float | None
    audio_error: str | None


class EventOut(BaseModel):
    id: int
    event_key: str
    site_id: str
    type: str
    version: int
    state: str
    priority: int
    source: str
    payload: dict[str, Any]
    created_at: datetime
    resolved_at: datetime | None
    approval: str
    approval_reason: str | None
    approved_by: str | None
    approved_at: datetime | None
    duplicate: bool = False  # this request repeated the active event and changed nothing
    announcements: list[AnnouncementOut] = Field(default_factory=list)


def event_out(
    session: Session, event: Event, *, detail: bool = False, duplicate: bool = False
) -> EventOut:
    out = EventOut(**event.model_dump(exclude={"staff_note"}), duplicate=duplicate)
    if detail:
        rows = session.exec(
            select(Announcement)
            .where(Announcement.event_id == event.id)
            .order_by(
                col(Announcement.is_resolution), col(Announcement.urgency), col(Announcement.id)
            )
        ).all()
        for a in rows:
            clip = session.get(AudioClip, a.clip_id) if a.clip_id else None
            out.announcements.append(
                AnnouncementOut(
                    **a.model_dump(include={"id", "language", "urgency", "is_resolution", "script",
                                            "spoken_text", "source", "model", "rejections",
                                            "clip_id", "audio_error"}),
                    audio_url=f"/audio/{a.clip_id}" if a.clip_id else None,
                    duration_s=clip.duration_s if clip else None,
                )
            )  # fmt: skip
    return out


def _refuse(exc: Exception) -> HTTPException:
    """Validation and service errors become 4xx, never 500."""
    if isinstance(exc, ValidationError):
        return HTTPException(422, exc.errors(include_url=False, include_context=False))
    assert isinstance(exc, EventError)
    return HTTPException(exc.status, str(exc))


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter()

    @router.post("/events", status_code=201)
    def create_event(body: EventIn, response: Response) -> EventOut:
        with Session(ctx.engine) as session:
            try:
                duplicate = events_service.find_duplicate(session, body.site_id, body.payload)
                if duplicate:
                    response.status_code = 200
                    return event_out(session, duplicate, detail=True, duplicate=True)
                event = events_service.create(
                    session, ctx, site_id=body.site_id, payload=body.payload,
                    event_key=body.event_key, source=body.source, staff_note=body.staff_note,
                )  # fmt: skip
            except (ValidationError, EventError) as exc:
                raise _refuse(exc) from exc
            return event_out(session, event, detail=True)

    @router.put("/events/{event_key}")
    def update_event(event_key: str, body: EventUpdateIn) -> EventOut:
        with Session(ctx.engine) as session:
            before = events_service.active_event(session, event_key)
            try:
                event = events_service.update(session, ctx, event_key, body.payload)
            except (ValidationError, EventError) as exc:
                raise _refuse(exc) from exc
            duplicate = before is not None and event.id == before.id
            return event_out(session, event, detail=True, duplicate=duplicate)

    @router.post("/events/{event_id}/resolve")
    def resolve_event(event_id: int, body: ResolveIn | None = None) -> EventOut:
        with Session(ctx.engine) as session:
            row = session.get(Event, event_id)
            if row is None:
                raise HTTPException(404, "no such event")
            try:
                event = events_service.resolve(
                    session, ctx, row.event_key, body.payload if body else None
                )
            except (ValidationError, EventError) as exc:
                raise _refuse(exc) from exc
            return event_out(session, event, detail=True)

    @router.post("/events/{event_id}/approve")
    def approve_event(event_id: int, body: ApproveIn) -> EventOut:
        with Session(ctx.engine) as session:
            try:
                event = events_service.approve(session, event_id, body.operator.strip())
            except EventError as exc:
                raise _refuse(exc) from exc
            return event_out(session, event, detail=True)

    @router.get("/events")
    def list_events(
        state: Literal["active", "superseded", "resolved"] | None = None,
        site_id: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        detail: bool = False,
    ) -> list[EventOut]:
        with Session(ctx.engine) as session:
            query = select(Event).order_by(col(Event.id).desc()).limit(limit)
            if state:
                query = query.where(Event.state == state)
            if site_id:
                query = query.where(Event.site_id == site_id)
            return [event_out(session, e, detail=detail) for e in session.exec(query).all()]

    @router.get("/events/{event_id}")
    def get_event(event_id: int) -> EventOut:
        with Session(ctx.engine) as session:
            event = session.get(Event, event_id)
            if event is None:
                raise HTTPException(404, "no such event")
            return event_out(session, event, detail=True)

    @router.post("/events/from-note")
    def from_note(body: NoteIn) -> NoteDraftOut:
        site = load_sites().get(body.site_id)
        if site is None:
            raise HTTPException(404, "unknown site")
        draft = draft_from_note(body.note, site, ctx.conns.llm, ctx.settings)
        if draft.status == "rejected":
            raise HTTPException(422, draft.detail)
        if draft.status != "ready":
            return NoteDraftOut(draft=draft)
        event = EventInput.model_validate(
            {"event_key": "draft", "site_id": site.id, "payload": draft.payload}
        )
        context = build_context(site, event.type)
        context.languages = context.languages[:1]
        plan = generate(event, context, ctx.conns.llm, ctx.settings, now=timeutil.now())
        return NoteDraftOut(draft=draft, preview=plan.scripts[0])

    @router.post("/events/preview")
    def preview(body: PreviewIn) -> PreviewOut:
        try:
            probe = EventInput(event_key="draft", site_id=body.site_id, payload=body.payload)  # type: ignore[arg-type]
            site = events_service.get_site_for(body.site_id, probe.type)
        except (ValidationError, EventError) as exc:
            raise _refuse(exc) from exc
        key = events_service.derive_key(probe.payload)
        with Session(ctx.engine) as session:
            duplicate = events_service.find_duplicate(session, body.site_id, body.payload)
            active = duplicate or events_service.active_event(session, key)
            active_key, active_id = (active.event_key, active.id) if active else (key, None)
            previous = events_service.previous_drafts(session, active) if active else []
        reason = jobs.approval_reason(
            site.policies[probe.type].approval,
            source=body.source,
            event_type=probe.type,
            payload=probe.payload.model_dump(),
        )
        plan = generate(
            probe.model_copy(update={"event_key": active_key}),
            build_context(
                site, probe.type, previous=previous
            ),  # an update says "this is an update"
            ctx.conns.llm,
            ctx.settings,
            now=timeutil.now(),
        )
        return PreviewOut(
            event_key=active_key,
            action="duplicate" if duplicate else "update" if active else "create",
            active_event_id=active_id,
            needs_approval=reason,
            scripts=plan.scripts,
        )

    return router
