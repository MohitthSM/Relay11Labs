"""Event endpoints. Phase 3 adds the staff-note draft; create/update/resolve come with events.py.

POST /events/from-note returns a DRAFT for the agent to confirm. It creates nothing and plays
nothing (SPEC F2): the agent edits the fields if needed and clicks Broadcast.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from relay import timeutil
from relay.schemas import EventInput
from relay.script.engine import ScriptSlot, build_context, generate
from relay.script.notes import NoteDraft, draft_from_note
from relay.sites import load_sites
from relay.workflow.context import Context


class NoteIn(BaseModel):
    site_id: str
    note: str = Field(max_length=2000)  # screen() applies the real limit and says why


class NoteDraftOut(BaseModel):
    draft: NoteDraft
    preview: ScriptSlot | None = None  # first-language, urgency-1 script, when the draft is ready


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter()

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
        event = EventInput(event_key="draft", site_id=site.id, payload=draft.payload)
        context = build_context(site, event.type)
        context.languages = context.languages[:1]
        plan = generate(event, context, ctx.conns.llm, ctx.settings, now=timeutil.now())
        return NoteDraftOut(draft=draft, preview=plan.scripts[0])

    return router
