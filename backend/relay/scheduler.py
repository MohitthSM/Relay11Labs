"""Playback scheduler: plays announcements into zones, one clip at a time, on the right cadence.

One asyncio task (`run`) calls `step` once a tick (D10), and sooner when a zone's clip ends.
`step` is sync and takes `now` as a parameter, so tests drive it with a fake clock.

Each tick:
  1. Drop.     Plays whose job was cancelled, or whose event was updated or resolved, leave the
               queue. A clip already on air is never cut (R8); only the clips after it go.
  2. Due.      Each due job puts one play (its sequence of languages) in its zone's queue and
               moves next_at on by its interval. Plays of one job never stack: a repeat that
               falls due while the previous play has not started is merged into it, and one that
               falls due while the previous play is still airing waits until it has finished.
  3. Expire.   Scheduled jobs whose ends_at has passed are done, and their events auto-resolve
               (after this tick's clips are dispatched).
  4. Dispatch. Each free zone airs the next clip of its best play: highest priority, then the
               oldest. A higher-priority play cuts in at the next clip boundary, and the play it
               interrupted resumes where it stopped once the zone is free of it (D19).
  5. Publish.  Each clip goes out as a "play" message on the WebSocket (D8), and a PlayLog row
               records each play: the audit log.

Zone state is in memory (D9). busy_until comes from clip durations, scaled like every other
interval (D11), so a time-compressed demo keeps its cadence. Jobs live in SQLite, so after a
restart the next tick picks them up again (a play that was half way through is lost).
"""

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from relay import bus, timeutil
from relay import events as events_service
from relay.context import Context
from relay.models import (
    ACTIVE,
    CANCELLED,
    DONE,
    PLAYED,
    PLAYING,
    SCHEDULED,
    STOPPED,
    Announcement,
    AudioClip,
    Event,
    LLMCall,
    PlaybackJob,
    PlayLog,
)

log = logging.getLogger(__name__)

TICK_S = 1.0  # scenario seconds between ticks: an update or resolve stops plays within one (R9)
GAP_S = 0.4  # scenario seconds of silence between two clips in a zone


@dataclass
class Clip:
    announcement_id: int
    clip_id: str
    language: str
    duration_s: float


@dataclass
class Play:
    """One repeat of one job in one zone: its languages in order, from first clip to last."""

    job_id: int
    event_id: int
    priority: int
    order: int  # first come, first served among plays of equal priority
    urgency: int
    is_resolution: bool
    announcement_ids: list[int]  # the whole sequence, for the audit log
    clips: list[Clip]  # still to air
    log_id: int | None = None  # the PlayLog row, written when the first clip goes on air


@dataclass
class OnAir:
    play: Play
    clip: Clip
    until: datetime  # the zone is busy until then (clip plus gap)


@dataclass
class Zone:
    id: str
    queue: list[Play] = field(default_factory=list)  # waiting, including interrupted plays
    on_air: OnAir | None = None
    last: Play | None = None  # the play of the last clip aired: tells a resume from a continue


@dataclass
class State:
    """Everything the scheduler keeps in memory. The lock covers a tick and a snapshot."""

    zones: dict[str, Zone] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    counter: int = 0

    def zone(self, zone_id: str) -> Zone:
        return self.zones.setdefault(zone_id, Zone(zone_id))


# --- one tick --------------------------------------------------------------------------------


def step(ctx: Context, state: State, now: datetime) -> float | None:
    """Run one tick at `now`. Returns real seconds until the next clip ends (None: all idle)."""
    with state.lock, Session(ctx.engine) as session:
        drop_dead(session, state, now)
        enqueue_due(session, state, now)
        expired = expire(session, now)
        messages = dispatch(session, state, now)
        session.commit()
    for message in messages:
        bus.publish(message)
    if expired:
        auto_resolve(ctx, expired)
    return wake_in(state, now)


def expire(session: Session, now: datetime) -> list[int]:
    """Jobs past ends_at are done. Returns the active events that ran out of time."""
    for job in session.exec(
        select(PlaybackJob).where(PlaybackJob.state == SCHEDULED, col(PlaybackJob.ends_at) <= now)
    ).all():
        job.state = DONE
        session.add(job)
    rows = session.exec(
        select(PlaybackJob.event_id)
        .join(Event, col(Event.id) == PlaybackJob.event_id)
        .where(
            Event.state == ACTIVE,
            col(PlaybackJob.ends_at) <= now,
            col(PlaybackJob.is_resolution).is_(False),
        )
    ).all()
    return sorted(set(rows))


def drop_dead(session: Session, state: State, now: datetime) -> None:
    """Remove plays whose job was cancelled or whose event is no longer active."""
    job_ids = {p.job_id for z in state.zones.values() for p in z.queue}
    if not job_ids:
        return
    rows = session.exec(
        select(PlaybackJob.id, PlaybackJob.state, PlaybackJob.is_resolution, Event.state)
        .join(Event, col(Event.id) == PlaybackJob.event_id)
        .where(col(PlaybackJob.id).in_(job_ids))
    ).all()
    alive = {
        job_id
        for job_id, job_state, is_resolution, event_state in rows
        if job_state != CANCELLED and (is_resolution or event_state == ACTIVE)
    }
    for zone in state.zones.values():
        for play in [p for p in zone.queue if p.job_id not in alive]:
            zone.queue.remove(play)
            if play.log_id is not None:  # it had started: the audit log says it was cut short
                row = session.get(PlayLog, play.log_id)
                if row:
                    on_air = zone.on_air and zone.on_air.play is play
                    row.status = STOPPED
                    row.finished_at = zone.on_air.until if on_air and zone.on_air else now
                    session.add(row)


def enqueue_due(session: Session, state: State, now: datetime) -> None:
    due = session.exec(
        select(PlaybackJob)
        .where(PlaybackJob.state == SCHEDULED, col(PlaybackJob.next_at) <= now)
        .order_by(col(PlaybackJob.priority).desc(), col(PlaybackJob.id))
    ).all()
    for job in due:
        assert job.id is not None
        zone = state.zone(job.zone_id)
        previous = next((p for p in zone.queue if p.job_id == job.id), None)
        if previous is not None and previous.log_id is not None:
            continue  # the previous play is still airing: this repeat waits for it
        if previous is None:  # otherwise the waiting play IS this repeat: merged
            urgency, announcement_ids, clips = sequence(session, job)
            state.counter += 1
            zone.queue.append(
                Play(
                    job_id=job.id,
                    event_id=job.event_id,
                    priority=job.priority,
                    order=state.counter,
                    urgency=urgency,
                    is_resolution=job.is_resolution,
                    announcement_ids=announcement_ids,
                    clips=clips,
                )
            )
            job.plays_done += 1
        job.next_at = advance(job.next_at, job.interval_s, now)
        out_of_plays = job.max_plays is not None and job.plays_done >= job.max_plays
        out_of_time = job.ends_at is not None and job.next_at >= job.ends_at
        if out_of_plays or out_of_time:
            job.state = DONE
        session.add(job)
    for zone in state.zones.values():  # a play with no audio at all is logged and skipped
        for play in [p for p in zone.queue if not p.clips]:
            zone.queue.remove(play)
            log_row = start_log(session, play, zone.id, now)
            log_row.status, log_row.finished_at = PLAYED, now
            log.warning("event %s: no audio for zone %s, play skipped", play.event_id, zone.id)


def advance(next_at: datetime, interval_s: int, now: datetime) -> datetime:
    """The next repeat, counted from the scheduled time so the cadence never drifts."""
    step_ = timedelta(seconds=timeutil.scaled(interval_s))
    later = next_at + step_
    return later if later > now else now + step_


def sequence(session: Session, job: PlaybackJob) -> tuple[int, list[int], list[Clip]]:
    """The urgency level to play and its clips, one per zone language, in the zone's order.

    Escalating jobs play level min(plays_done + 1, 3); others level 1. A language without a
    voiced clip at that level replays its highest voiced level below it: the later levels are
    voiced in the background (events.voice_later), and a play never waits for them. That is
    logged. A language with no voiced clip at all is left out (its text is still in the console).
    """
    urgency = min(job.plays_done + 1, 3) if job.escalate else 1
    rows = session.exec(
        select(Announcement).where(
            Announcement.event_id == job.event_id,
            Announcement.is_resolution == job.is_resolution,
        )
    ).all()
    announcement_ids, clips = [], []
    for language in job.languages:
        options = [a for a in rows if a.language == language and a.urgency <= urgency]
        voiced = [
            (a, clip)
            for a in options
            if a.clip_id and (clip := session.get(AudioClip, a.clip_id)) is not None
        ]
        if not voiced:
            if options:  # the audit log still says which announcement was due
                due = max(options, key=lambda a: a.urgency)
                assert due.id is not None
                announcement_ids.append(due.id)
            continue
        ann, clip = max(voiced, key=lambda pair: pair[0].urgency)
        if ann.urgency < urgency:
            log.warning(
                "event %s zone %s %s: level %d not voiced yet, replaying level %d",
                job.event_id, job.zone_id, language, urgency, ann.urgency,
            )  # fmt: skip
        assert ann.id is not None
        announcement_ids.append(ann.id)
        clips.append(Clip(ann.id, clip.id, language, clip.duration_s))
    return urgency, announcement_ids, clips


def dispatch(session: Session, state: State, now: datetime) -> list[dict[str, Any]]:
    """Every free zone airs one clip. Returns the play messages to publish."""
    messages = []
    for zone in state.zones.values():
        if zone.on_air and zone.on_air.until > now:
            continue  # busy: nothing is ever cut mid-clip
        zone.on_air = None
        if not zone.queue:
            continue
        play = min(zone.queue, key=lambda p: (-p.priority, p.order))
        clip = play.clips.pop(0)
        resumed = play.log_id is not None and zone.last is not play
        if play.log_id is None:
            row = start_log(session, play, zone.id, now)
        else:
            row = session.get(PlayLog, play.log_id) or start_log(session, play, zone.id, now)
        until = now + timedelta(seconds=timeutil.scaled(clip.duration_s + GAP_S))
        row.clips_played += 1
        row.interruptions += int(resumed)
        if not play.clips:
            zone.queue.remove(play)
            row.status, row.finished_at = PLAYED, until
        session.add(row)
        zone.on_air, zone.last = OnAir(play, clip, until), play
        messages.append(
            {
                "type": "play",
                "zone_id": zone.id,
                "event_id": play.event_id,
                "play_id": row.id,
                "announcement_id": clip.announcement_id,
                "language": clip.language,
                "url": f"/audio/{clip.clip_id}",
                "duration_s": clip.duration_s,
                "priority": play.priority,
                "is_resolution": play.is_resolution,
                "resumed": resumed,
            }
        )
    return messages


def start_log(session: Session, play: Play, zone_id: str, now: datetime) -> PlayLog:
    """The audit row for a play: what, where, when, which script version, who approved it."""
    event = session.get(Event, play.event_id)
    assert event is not None
    scripts = []
    for ann_id in play.announcement_ids:
        ann = session.get(Announcement, ann_id)
        if ann is None:
            continue
        call = session.get(LLMCall, ann.llm_call_id) if ann.llm_call_id else None
        scripts.append(
            {
                "announcement_id": ann_id,
                "language": ann.language,
                "urgency": ann.urgency,  # the level that played: below the play's if not ready
                "source": ann.source,
                "model": ann.model,
                "prompt_version": call.prompt_version if call else None,
            }
        )
    row = PlayLog(
        job_id=play.job_id,
        event_id=play.event_id,
        event_version=event.version,
        zone_id=zone_id,
        urgency=play.urgency,
        is_resolution=play.is_resolution,
        priority=play.priority,
        announcement_ids=play.announcement_ids,
        scripts=scripts,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
        played_at=now,
        status=PLAYING,
    )
    session.add(row)
    session.flush()  # the id goes on the play message
    play.log_id = row.id
    return row


def auto_resolve(ctx: Context, event_ids: list[int]) -> None:
    """Events whose time ran out (policy duration_s) are resolved, as if the operator had."""
    with Session(ctx.engine) as session:
        for event_id in event_ids:
            event = session.get(Event, event_id)
            if event is None or event.state != ACTIVE:
                continue
            try:
                events_service.resolve(session, ctx, event.event_key)
                log.info("event %s expired and was resolved", event_id)
            except events_service.EventError:
                log.warning("event %s expired but could not be resolved", event_id)


def wake_in(state: State, now: datetime) -> float | None:
    ends = [z.on_air.until for z in state.zones.values() if z.on_air and z.on_air.until > now]
    return min((t - now).total_seconds() for t in ends) if ends else None


# --- the loop --------------------------------------------------------------------------------


async def run(ctx: Context, state: State) -> None:
    """The scheduler task, started by the app's lifespan. A failing tick is logged, never fatal."""
    log.info("scheduler started")
    while True:
        try:
            wake = await asyncio.to_thread(step, ctx, state, timeutil.now())
        except Exception:
            log.exception("scheduler tick failed")
            wake = None
        tick = timeutil.scaled(TICK_S)
        await timeutil.asleep_real(min(tick, wake) if wake is not None else tick)


# --- read side (the zones view) --------------------------------------------------------------


def snapshot(state: State, now: datetime) -> dict[str, dict[str, Any]]:
    """What each zone is airing and what waits, by zone id. Ids only."""
    out: dict[str, dict[str, Any]] = {}
    with state.lock:
        for zone in state.zones.values():
            on_air = None
            if zone.on_air and zone.on_air.until > now:
                play, clip = zone.on_air.play, zone.on_air.clip
                on_air = {
                    "event_id": play.event_id,
                    "play_id": play.log_id,
                    "announcement_id": clip.announcement_id,
                    "language": clip.language,
                    "priority": play.priority,
                    "until": zone.on_air.until,
                }
            queue = [
                {
                    "event_id": p.event_id,
                    "priority": p.priority,
                    "clips_left": len(p.clips),
                    "interrupted": p.log_id is not None,
                }
                for p in sorted(zone.queue, key=lambda p: (-p.priority, p.order))
                if not (on_air and p.log_id == on_air["play_id"])
            ]
            out[zone.id] = {"on_air": on_air, "queue": queue}
    return out
