"""PII purge (R11): passenger names leave the database and the audio cache once a page has been
over for RELAY_PII_RETENTION_HOURS.

Where a name lives, and what happens to it:
  Event.payload.passenger_name   -> "[purged]"
  Event.event_key                -> "purged-<first id>" (the key is a slug of the name)
  Event.staff_note               -> None (the note named the passenger)
  Announcement.script, spoken_text -> "[purged]" (draft has placeholders only and is kept)
  audio clips voiced from them   -> file and row deleted, unless another announcement still uses
                                    the clip (the cache is content-addressed, so an identical
                                    page that is still running shares it) or it is permanent
Ids, counts and timings stay: LLMCall, VoiceRender and PlayLog hold no names (R13 keeps working).

An event key is purged when none of its versions is active and its latest end is older than the
retention. Retention is real time, never time-scaled: it is a promise to passengers, not a demo
interval (like the TfL poll, D23).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from relay import timeutil
from relay.context import Context
from relay.models import ACTIVE, Announcement, AudioClip, Event
from relay.voice import clip_path

log = logging.getLogger(__name__)

PURGED = "[purged]"
PII_FIELDS = {"passenger_page": ["passenger_name"]}  # event type -> payload fields that are PII
EVERY_S = 300.0  # real seconds between purge runs


def due_keys(session: Session, retention: timedelta, now: datetime) -> list[str]:
    """Event keys holding PII whose events have all ended before now - retention."""
    rows = session.exec(
        select(Event).where(
            col(Event.type).in_(list(PII_FIELDS)), col(Event.event_key).not_like("purged-%")
        )
    ).all()
    by_key: dict[str, list[Event]] = {}
    for event in rows:
        by_key.setdefault(event.event_key, []).append(event)
    cutoff = now - retention
    due = []
    for key, versions in by_key.items():
        if any(e.state == ACTIVE for e in versions):
            continue
        ended = max((e.resolved_at or e.created_at) for e in versions)
        if _aware(ended) <= cutoff:
            due.append(key)
    return due


def purge_key(session: Session, ctx: Context, event_key: str) -> int:
    """Scrub every version of one event key. Returns the number of clips deleted. Commits."""
    versions = session.exec(select(Event).where(Event.event_key == event_key)).all()
    ids = [e.id for e in versions if e.id is not None]
    new_key = f"purged-{min(ids)}"
    for event in versions:
        payload = dict(event.payload)
        for name in PII_FIELDS.get(event.type, []):
            if name in payload:
                payload[name] = PURGED
        event.payload, event.event_key, event.staff_note = payload, new_key, None
        session.add(event)

    anns = session.exec(select(Announcement).where(col(Announcement.event_id).in_(ids))).all()
    clip_ids = {a.clip_id for a in anns if a.clip_id}
    for ann in anns:
        ann.script = ann.spoken_text = PURGED
        ann.clip_id, ann.audio_error = None, "audio purged (R11)"
        session.add(ann)
    session.flush()
    deleted = _delete_clips(session, ctx, clip_ids)
    session.commit()
    log.info("purged %d event version(s) as %s, %d clip(s)", len(versions), new_key, deleted)
    return deleted


def _delete_clips(session: Session, ctx: Context, clip_ids: set[str]) -> int:
    """Delete announcement clips and their non-permanent segments that nothing else uses."""
    still_used = set(
        session.exec(
            select(Announcement.clip_id).where(col(Announcement.clip_id).is_not(None))
        ).all()
    )
    wanted: dict[str, AudioClip] = {}
    for clip_id in clip_ids - still_used:
        clip = session.get(AudioClip, clip_id)
        if clip is None or clip.permanent:
            continue
        wanted[clip.id] = clip
        for part_id in clip.parts:  # the stitched clip's segments: the middle holds the name
            part = session.get(AudioClip, part_id)
            if part is not None and not part.permanent:
                wanted[part.id] = part
    if wanted:  # a segment that is also the whole clip of a live announcement must stay
        kept_by_others = _parts_of(session, still_used)
        wanted = {k: v for k, v in wanted.items() if k not in kept_by_others}
    for clip in wanted.values():
        clip_path(ctx.settings, clip).unlink(missing_ok=True)
        session.delete(clip)
    return len(wanted)


def _parts_of(session: Session, clip_ids: set[str | None]) -> set[str]:
    out: set[str] = {c for c in clip_ids if c}
    for clip_id in list(out):
        clip = session.get(AudioClip, clip_id)
        if clip is not None:
            out.update(clip.parts)
    return out


def purge_due(ctx: Context, now: datetime) -> int:
    """Blocking. Purge every key that is due. Returns how many keys were purged."""
    retention = timedelta(hours=ctx.settings.relay_pii_retention_hours)
    with Session(ctx.engine) as session:
        keys = due_keys(session, retention, now)
        for key in keys:
            purge_key(session, ctx, key)
    return len(keys)


async def run(ctx: Context) -> None:
    """The purge task (started in the lifespan). A failing run is logged, never fatal."""
    while True:
        try:
            await asyncio.to_thread(purge_due, ctx, timeutil.now())
        except Exception:
            log.exception("PII purge failed")
        await timeutil.asleep_real(EVERY_S)


def _aware(when: datetime) -> datetime:
    """SQLite returns naive datetimes; Relay stores UTC."""
    return when if when.tzinfo else when.replace(tzinfo=UTC)
