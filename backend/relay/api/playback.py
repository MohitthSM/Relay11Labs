"""Playback endpoints. Play commands themselves go out on the WebSocket (api/ws.py, D8).

GET /audio/{clip_id}     a cached clip
GET /playback/zones      every zone: what is on air, what waits, the next repeat, held jobs
GET /playback/log        the audit log: what played, where, when, which script version, who
                         approved it (ids and versions only, no names)
"""

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, func, select

from relay import scheduler, timeutil
from relay.context import Context
from relay.models import HELD, SCHEDULED, Announcement, AudioClip, PlaybackJob, PlayLog
from relay.sites import load_sites
from relay.voice import clip_path

CLIP_ID = re.compile(r"^[0-9a-f]{64}$")  # a sha256: also rules out path tricks
MEDIA = {"mp3": "audio/mpeg", "wav": "audio/wav"}


class OnAirOut(BaseModel):
    event_id: int
    play_id: int | None
    announcement_id: int
    language: str
    priority: int
    until: datetime
    caption: str | None  # the script on air (the console shows the same text)


class QueuedOut(BaseModel):
    event_id: int
    priority: int
    clips_left: int
    interrupted: bool  # started, then a higher-priority play cut in: it resumes


class NextOut(BaseModel):
    event_id: int
    at: datetime
    is_resolution: bool


class ZoneOut(BaseModel):
    site_id: str
    zone_id: str
    name: str
    languages: list[str]
    on_air: OnAirOut | None
    queue: list[QueuedOut]
    next: NextOut | None  # the next scheduled repeat in this zone
    held: int  # jobs waiting for operator approval


class PlayLogOut(BaseModel):
    id: int
    job_id: int | None
    event_id: int | None
    event_version: int
    zone_id: str
    urgency: int
    is_resolution: bool
    priority: int
    announcement_ids: list[int]
    scripts: list[dict[str, Any]]
    approved_by: str | None
    approved_at: datetime | None
    played_at: datetime
    finished_at: datetime | None
    clips_played: int
    interruptions: int
    status: str


def build_router(ctx: Context, state: scheduler.State) -> APIRouter:
    router = APIRouter()

    @router.get("/audio/{clip_id}")
    def audio(clip_id: str) -> FileResponse:
        if not CLIP_ID.match(clip_id):
            raise HTTPException(404, "no such clip")
        with Session(ctx.engine) as session:
            clip = session.get(AudioClip, clip_id)
        if clip is None or not clip_path(ctx.settings, clip).exists():
            raise HTTPException(404, "no such clip")
        media = MEDIA.get(clip.path.rsplit(".", 1)[-1], "application/octet-stream")
        return FileResponse(clip_path(ctx.settings, clip), media_type=media)

    @router.get("/playback/zones")
    def zones(site_id: str | None = None) -> list[ZoneOut]:
        now = timeutil.now()
        live = scheduler.snapshot(state, now)
        out = []
        with Session(ctx.engine) as session:
            for site in load_sites().values():
                if site_id and site.id != site_id:
                    continue
                for zone in site.zones:
                    snap = live.get(zone.id, {"on_air": None, "queue": []})
                    on_air = None
                    if snap["on_air"]:
                        ann = session.get(Announcement, snap["on_air"]["announcement_id"])
                        on_air = OnAirOut(**snap["on_air"], caption=ann.script if ann else None)
                    out.append(
                        ZoneOut(
                            site_id=site.id,
                            zone_id=zone.id,
                            name=zone.name,
                            languages=zone.languages,
                            on_air=on_air,
                            queue=[QueuedOut(**q) for q in snap["queue"]],
                            next=_next_job(session, zone.id),
                            held=_held(session, zone.id),
                        )
                    )
        return out

    @router.get("/playback/log")
    def play_log(
        event_id: int | None = None,
        zone_id: str | None = None,
        limit: int = Query(200, ge=1, le=1000),
    ) -> list[PlayLogOut]:
        with Session(ctx.engine) as session:
            query = select(PlayLog).order_by(col(PlayLog.id).desc()).limit(limit)
            if event_id is not None:
                query = query.where(PlayLog.event_id == event_id)
            if zone_id:
                query = query.where(PlayLog.zone_id == zone_id)
            return [PlayLogOut.model_validate(r, from_attributes=True) for r in session.exec(query)]

    return router


def _next_job(session: Session, zone_id: str) -> NextOut | None:
    job = session.exec(
        select(PlaybackJob)
        .where(PlaybackJob.zone_id == zone_id, PlaybackJob.state == SCHEDULED)
        .order_by(col(PlaybackJob.next_at))
    ).first()
    if job is None:
        return None
    return NextOut(event_id=job.event_id, at=job.next_at, is_resolution=job.is_resolution)


def _held(session: Session, zone_id: str) -> int:
    return session.exec(
        select(func.count())
        .select_from(PlaybackJob)
        .where(PlaybackJob.zone_id == zone_id, PlaybackJob.state == HELD)
    ).one()
