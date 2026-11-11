"""First play waits only for urgency level 1 (D4, amended in Phase 8).

The later levels are voiced on the background thread. Here their TTS calls (the scheduled model,
D17) block on a gate the test controls, so "the first play happened while levels 2 and 3 were
still being voiced" is checked directly, not timed.
"""

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from relay import background, bus, scheduler, timeutil
from relay import events as events_service
from relay.context import Context
from relay.models import Announcement, PlayLog
from relay.providers import Connectors
from relay.providers.llm_mock import MockLLMProvider
from relay.providers.tts_mock import MockTTSProvider

PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
    "page_type": "final_call",
}
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


class GatedTTS(MockTTSProvider):
    """Level 1 renders at once; levels 2 and 3 (the scheduled model) wait for `gate`."""

    def __init__(self, scheduled_model: str) -> None:
        super().__init__()
        self.scheduled_model = scheduled_model
        self.gate = threading.Event()
        self.models: list[str] = []
        self._lock = threading.Lock()

    def synthesize(self, req):
        with self._lock:
            self.models.append(req.model_id)
        if req.model_id == self.scheduled_model:
            assert self.gate.wait(10), "the gate was never opened"
        return super().synthesize(req)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> Clock:
    timeutil.set_scale(1)
    fake = Clock()
    monkeypatch.setattr(timeutil, "now", fake.now)
    return fake


@pytest.fixture
def sent(monkeypatch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    monkeypatch.setattr(bus, "publish", out.append)
    return out


@pytest.fixture
def tts(settings) -> GatedTTS:
    return GatedTTS(settings.elevenlabs_model_scheduled)


@pytest.fixture
def ctx(settings, engine, tts) -> Context:
    return Context(Connectors(llm=MockLLMProvider(), tts=tts), settings, engine)


def create_page(ctx: Context) -> int:
    """events.create on another thread, so a create that waited on the gate fails, not hangs."""
    out: dict[str, Any] = {}

    def run() -> None:
        with Session(ctx.engine) as s:
            out["id"] = events_service.create(s, ctx, site_id="airport", payload=PAGE).id

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "create waited for the later urgency levels"
    return out["id"]


def voiced_levels(engine, event_id: int) -> dict[int, int]:
    """urgency -> how many of its announcements have a clip."""
    with Session(engine) as s:
        rows = s.exec(select(Announcement).where(Announcement.event_id == event_id)).all()
    out: dict[int, int] = {}
    for a in rows:
        out[a.urgency] = out.get(a.urgency, 0) + (a.clip_id is not None)
    return out


def tick(ctx, state, clock, seconds: float) -> None:
    end = clock.t + timedelta(seconds=seconds)
    while clock.t <= end:
        scheduler.step(ctx, state, clock.t)
        clock.t += timedelta(seconds=1)


def test_first_play_does_not_wait_for_later_levels(ctx, engine, clock, sent, tts, settings):
    event_id = create_page(ctx)  # returns while levels 2 and 3 are stuck behind the gate

    assert voiced_levels(engine, event_id) == {1: 3, 2: 0, 3: 0}
    assert tts.models[:3] == [settings.elevenlabs_model_live] * 3  # level 1: EN, ES, FR

    state = scheduler.State()
    tick(ctx, state, clock, 30)  # the first play airs in both zones, gate still closed
    plays = [m for m in sent if m["type"] == "play" and m["event_id"] == event_id]
    assert {m["zone_id"] for m in plays} == {"gate-23", "concourse-b"}
    assert {m["language"] for m in plays} == {"en", "es", "fr"}
    assert not tts.gate.is_set()

    tts.gate.set()
    assert background.wait(10)
    assert voiced_levels(engine, event_id) == {1: 3, 2: 3, 3: 3}
    assert any(m["type"] == "event.audio" and m["event_id"] == event_id for m in sent)


def test_a_late_level_replays_the_previous_one_and_says_so(ctx, engine, clock, sent, tts, caplog):
    event_id = create_page(ctx)
    state = scheduler.State()
    with caplog.at_level(logging.WARNING, logger="relay.scheduler"):
        tick(ctx, state, clock, 150)  # play 2 is due at 120 s; level 2 is still not voiced
    assert "level 2 not voiced yet, replaying level 1" in caplog.text

    tts.gate.set()
    assert background.wait(10)
    tick(ctx, state, clock, 150)  # play 3 at 240 s: level 3 is voiced by now

    with Session(engine) as s:
        rows = s.exec(
            select(PlayLog).where(PlayLog.event_id == event_id, PlayLog.zone_id == "gate-23")
        ).all()
    due = [r.urgency for r in rows]
    played = [{x["urgency"] for x in r.scripts} for r in rows]
    assert due == [1, 2, 3]
    assert played == [{1}, {1}, {3}]  # play 2 replayed level 1; the audit log records it


def test_resolving_stops_the_background_before_the_next_level(ctx, engine, clock, tts, settings):
    event_id = create_page(ctx)
    with Session(engine) as s:  # the background is mid level 2, stuck at the gate
        events_service.resolve(s, ctx, "page-BA117-john-smith")
    tts.gate.set()
    assert background.wait(10)

    scheduled_calls = tts.models.count(settings.elevenlabs_model_scheduled)
    assert scheduled_calls <= 3  # level 2 was already on its way; level 3 never started
    assert voiced_levels(engine, event_id)[3] == 0
