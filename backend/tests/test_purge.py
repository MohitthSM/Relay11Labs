"""PII purge (R11): names leave rows and the audio cache after the retention, nothing else does."""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from relay import events as events_service
from relay import purge, timeutil
from relay.context import Context
from relay.models import Announcement, AudioClip, Event, LLMCall, VoiceRender
from relay.providers import build_connectors
from relay.voice import clip_path

PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
    "page_type": "final_call",
}
STAND = {"type": "stand_change", "route": "25", "from_stand": "B", "to_stand": "F"}
DAY = timedelta(hours=24)


@pytest.fixture
def ctx(settings, engine) -> Context:
    return Context(conns=build_connectors(settings), settings=settings, engine=engine)


def page(ctx, engine, payload=PAGE, *, resolve=True) -> Event:
    with Session(engine) as s:
        event = events_service.create(s, ctx, site_id="airport", payload=payload)
        if resolve:
            event = events_service.resolve(s, ctx, event.event_key)
        return event


def everything(engine) -> str:
    """Every text column that could hold a name, as one string."""
    with Session(engine) as s:
        events = s.exec(select(Event)).all()
        anns = s.exec(select(Announcement)).all()
    parts = [f"{e.event_key} {e.payload} {e.staff_note}" for e in events]
    parts += [f"{a.script} {a.spoken_text} {a.draft}" for a in anns]
    return " ".join(parts)


def test_a_resolved_page_is_purged_after_the_retention(ctx, engine, settings):
    event = page(ctx, engine)
    with Session(engine) as s:
        clips = [s.get(AudioClip, a.clip_id) for a in s.exec(select(Announcement)).all()]
        files = [clip_path(settings, c) for c in clips if c]
    assert files and all(f.exists() for f in files)
    resolved_at = event.resolved_at
    assert resolved_at is not None

    assert purge.purge_due(ctx, resolved_at + DAY - timedelta(minutes=1)) == 0  # not yet
    assert "John Smith" in everything(engine)

    assert purge.purge_due(ctx, resolved_at + DAY) == 1
    text = everything(engine)
    assert "John Smith" not in text and "john-smith" not in text and "J O H N" not in text
    assert not any(f.exists() for f in files)
    with Session(engine) as s:
        row = s.get(Event, event.id)
        assert row is not None and row.event_key == f"purged-{event.id}"
        assert row.payload["passenger_name"] == purge.PURGED
        assert row.payload["gate"] == "23"  # only the PII field goes
        assert all(a.clip_id is None for a in s.exec(select(Announcement)).all())
        assert s.exec(select(LLMCall)).all() and s.exec(select(VoiceRender)).all()  # metrics stay
    assert purge.purge_due(ctx, resolved_at + 2 * DAY) == 0  # idempotent


def test_an_active_page_is_never_purged(ctx, engine):
    page(ctx, engine, resolve=False)
    assert purge.purge_due(ctx, timeutil.now() + 10 * DAY) == 0
    assert "John Smith" in everything(engine)


def test_every_version_goes_together(ctx, engine):
    with Session(engine) as s:
        v1 = events_service.create(s, ctx, site_id="airport", payload=PAGE)
        first_id, key = v1.id, v1.event_key
        events_service.update(s, ctx, key, {**PAGE, "gate": "24"})
        events_service.resolve(s, ctx, key)
    assert purge.purge_due(ctx, timeutil.now() + DAY) == 1
    with Session(engine) as s:
        keys = {e.event_key for e in s.exec(select(Event)).all()}
    assert keys == {f"purged-{first_id}"}
    assert "John Smith" not in everything(engine)


def test_a_clip_shared_with_a_running_page_is_kept(ctx, engine, settings):
    """Same name, same text: the content-addressed cache serves both pages one clip."""
    page(ctx, engine)  # ended
    with Session(engine) as s:
        running = events_service.create(
            s, ctx, site_id="airport", payload=PAGE, event_key="same-passenger-again"
        )
        rows = s.exec(select(Announcement).where(Announcement.event_id == running.id)).all()
        live_ids = [a.clip_id for a in rows]
    purge.purge_due(ctx, timeutil.now() + DAY)
    with Session(engine) as s:
        clips = [s.get(AudioClip, i) for i in live_ids if i]
        assert len(clips) == len(live_ids) == 9
        assert all(c is not None and clip_path(settings, c).exists() for c in clips)


def test_other_event_types_are_left_alone(ctx, engine):
    with Session(engine) as s:
        event = events_service.create(s, ctx, site_id="bus_station", payload=STAND)
        events_service.resolve(s, ctx, event.event_key)
    assert purge.purge_due(ctx, timeutil.now() + 10 * DAY) == 0
