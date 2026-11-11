"""Metrics endpoints (R7, SPEC F5). Everything is read from database rows, never from logs.

GET /metrics/summary         totals, model routing, fact-lock rejection rate by layer, TTS cache
                             and cost, and the time from event to first play (p50, p95)
GET /metrics/announcements   one row per announcement: source, model, rejection layers, voice cost

No passenger name can appear here: the rows used (LLMCall, VoiceRender, PlayLog and the ids and
enums of Announcement and Event) hold none, and an event is named by its id, never by its key.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlmodel import Session, col

from relay import voice
from relay.config import Settings
from relay.context import Context
from relay.models import ACTIVE, Announcement, Event, LLMCall, PlayLog, VoiceRender


class CountOut(BaseModel):
    total: int
    llm: int  # an LLM script passed the fact lock
    template: int  # the deterministic template was used
    rejected: int  # had at least one fact-lock rejection on the way
    rejection_rate: float
    template_rate: float
    by_layer: dict[str, int]  # fact-lock layer -> violations


class RouteOut(BaseModel):
    model: str
    role: str  # default | escalation | other
    calls: int
    accepted: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class TTSModelOut(BaseModel):
    model: str
    role: str  # live | scheduled | other
    renders: int
    characters_billed: int
    cost_usd: float


class TimingOut(BaseModel):
    n: int
    p50_s: float | None
    p95_s: float | None


class SummaryOut(BaseModel):
    events: int
    active_events: int
    plays: int  # sequences played in zones (PlayLog rows)
    announcements: CountOut
    llm_routes: list[RouteOut]
    llm_calls: int
    llm_escalation_share: float  # calls on the escalation model / all calls
    llm_cost_usd: float
    tts_renders: int
    tts_cache_hit_rate: float
    tts_segment_hit_rate: float
    tts_characters_billed: int
    tts_characters_saved: int
    tts_cost_usd: float
    tts_cost_saved_usd: float
    tts_models: list[TTSModelOut]
    total_cost_usd: float
    time_to_first_play: TimingOut
    avg_tts_ttfa_ms: float


class AnnouncementRowOut(BaseModel):
    id: int
    event_id: int
    event_type: str
    site_id: str
    version: int
    language: str
    urgency: int
    is_resolution: bool
    source: str
    model: str | None
    rejection_layers: list[str]
    tts_model: str | None
    cache_hit: bool | None
    characters_billed: int | None
    characters_saved: int | None
    tts_cost_usd: float | None
    ttfa_ms: float | None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..1) of a list; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def model_role(model: str, default: str, escalation: str) -> str:
    if model == default:
        return "default"
    if model == escalation:
        return "escalation"
    return "other"


def rejection_layers(rejections: list) -> list[str]:
    """Fact-lock layers named in an announcement's rejections, one entry per violation."""
    return [v["layer"] for r in rejections for v in r.get("violations", [])]


def counts(session: Session) -> CountOut:
    rows = session.execute(select(col(Announcement.source), col(Announcement.rejections))).all()
    by_layer: Counter[str] = Counter()
    rejected = 0
    for _source, rejections in rows:
        layers = rejection_layers(rejections)
        by_layer.update(layers)
        rejected += bool(layers)
    total = len(rows)
    template = sum(1 for source, _ in rows if source == "template")
    return CountOut(
        total=total,
        llm=total - template,
        template=template,
        rejected=rejected,
        rejection_rate=rejected / total if total else 0.0,
        template_rate=template / total if total else 0.0,
        by_layer=dict(by_layer),
    )


def llm_routes(session: Session, settings: Settings) -> list[RouteOut]:
    c = LLMCall
    rows = session.execute(
        select(
            col(c.model),
            func.count(),
            func.coalesce(func.sum(case((col(c.outcome) == "accepted", 1), else_=0)), 0),
            func.coalesce(func.sum(c.input_tokens), 0),
            func.coalesce(func.sum(c.output_tokens), 0),
            func.coalesce(func.sum(c.cost_usd), 0.0),
        )
        .where(col(c.purpose) == "script")
        .group_by(col(c.model))
        .order_by(col(c.model))
    ).all()
    return [
        RouteOut(
            model=model,
            role=model_role(model, settings.script_model_default, settings.script_model_escalation),
            calls=calls,
            accepted=accepted,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost,
        )
        for model, calls, accepted, tokens_in, tokens_out, cost in rows
    ]


def seconds_to_first_play(session: Session) -> list[float]:
    """Per event version: the first real play minus the moment it could first play (created,
    or approved when an operator had to release it). Resolution messages do not count."""
    first = dict(
        session.execute(
            select(col(PlayLog.event_id), func.min(PlayLog.played_at))
            .where(col(PlayLog.is_resolution).is_(False))
            .group_by(col(PlayLog.event_id))
        ).all()
    )
    out = []
    for event in session.scalars(select(Event).where(col(Event.id).in_(list(first)))):
        played: datetime = first[event.id]
        start = max(event.created_at, event.approved_at or event.created_at)
        out.append(max((played - start).total_seconds(), 0.0))
    return out


def summary(session: Session, settings: Settings) -> SummaryOut:
    routes = llm_routes(session, settings)
    calls = sum(r.calls for r in routes)
    escalated = sum(r.calls for r in routes if r.role == "escalation")
    llm_cost = sum(r.cost_usd for r in routes)
    tts = voice.stats(session)
    waits = seconds_to_first_play(session)
    return SummaryOut(
        events=session.scalar(select(func.count()).select_from(Event)) or 0,
        active_events=session.scalar(
            select(func.count()).select_from(Event).where(col(Event.state) == ACTIVE)
        )
        or 0,
        plays=session.scalar(select(func.count()).select_from(PlayLog)) or 0,
        announcements=counts(session),
        llm_routes=routes,
        llm_calls=calls,
        llm_escalation_share=escalated / calls if calls else 0.0,
        llm_cost_usd=llm_cost,
        tts_renders=tts.renders,
        tts_cache_hit_rate=tts.hit_rate,
        tts_segment_hit_rate=tts.segment_hit_rate,
        tts_characters_billed=tts.characters_billed,
        tts_characters_saved=tts.characters_saved,
        tts_cost_usd=tts.cost_usd,
        tts_cost_saved_usd=tts.cost_saved_usd,
        tts_models=[
            TTSModelOut(
                model=model,
                role=tts_role(model, settings),
                renders=m.renders,
                characters_billed=m.characters_billed,
                cost_usd=m.cost_usd,
            )
            for model, m in tts.by_model.items()
        ],
        total_cost_usd=llm_cost + tts.cost_usd,
        time_to_first_play=TimingOut(
            n=len(waits), p50_s=percentile(waits, 0.5), p95_s=percentile(waits, 0.95)
        ),
        avg_tts_ttfa_ms=tts.avg_ttfa_ms,
    )


def tts_role(model: str, settings: Settings) -> str:
    if model == settings.elevenlabs_model_live:
        return "live"
    if model == settings.elevenlabs_model_scheduled:
        return "scheduled"
    return "other"


def announcement_rows(session: Session, limit: int) -> list[AnnouncementRowOut]:
    a, e, v = Announcement, Event, VoiceRender
    rows = session.execute(
        select(a, e, v)
        .join(e, col(e.id) == col(a.event_id))
        .join(v, col(v.announcement_id) == col(a.id), isouter=True)
        .order_by(col(a.id).desc())
        .limit(limit)
    ).all()
    return [
        AnnouncementRowOut(
            id=ann.id or 0,
            event_id=event.id or 0,
            event_type=event.type,
            site_id=event.site_id,
            version=event.version,
            language=ann.language,
            urgency=ann.urgency,
            is_resolution=ann.is_resolution,
            source=ann.source,
            model=ann.model,
            rejection_layers=sorted(set(rejection_layers(ann.rejections))),
            tts_model=render.model_id if render else None,
            cache_hit=render.cache_hit if render else None,
            characters_billed=render.characters_billed if render else None,
            characters_saved=render.characters_saved if render else None,
            tts_cost_usd=render.cost_usd if render else None,
            ttfa_ms=render.ttfa_ms if render else None,
        )
        for ann, event, render in rows
    ]


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter()

    @router.get("/metrics/summary")
    def metrics_summary() -> SummaryOut:
        with Session(ctx.engine) as session:
            return summary(session, ctx.settings)

    @router.get("/metrics/announcements")
    def metrics_announcements(limit: int = Query(100, ge=1, le=500)) -> list[AnnouncementRowOut]:
        with Session(ctx.engine) as session:
            return announcement_rows(session, limit)

    return router
