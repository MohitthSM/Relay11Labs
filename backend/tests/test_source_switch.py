"""Live -> Demo: TfL events are withdrawn and never play over a scripted scenario (D23).

Drives the real path in mock mode with a fake clock: TfL poll -> events service -> scheduler.
"""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session, select

from relay import bus, scheduler, timeutil
from relay import events as events_service
from relay.context import Context
from relay.models import ACTIVE, CANCELLED, WITHDRAWN, Announcement, Event, PlaybackJob
from relay.providers import build_connectors
from relay.sources import replay, tfl

FIXTURES = Path(__file__).parent / "fixtures" / "tfl"
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
SCENARIO_MINOR = {
    "type": "line_status",
    "line": "Jubilee",
    "status": "minor_delays",
    "reason": "signal_failure",
    "location": "Canary Wharf",
}


def fetch_of(name: str):
    return lambda: json.loads((FIXTURES / f"{name}.json").read_text())


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
def ctx(settings, engine) -> Context:
    return Context(conns=build_connectors(settings), settings=settings, engine=engine)


def tfl_events(engine) -> list[Event]:
    with Session(engine) as s:
        return list(s.exec(select(Event).where(Event.source == "tfl")).all())


def tick_for(ctx, state, clock, seconds: float) -> None:
    end = clock.t + timedelta(seconds=seconds)
    while clock.t <= end:
        scheduler.step(ctx, state, clock.t)
        clock.t += timedelta(seconds=1)


def test_switch_to_demo_withdraws_tfl_events_silently(ctx, engine, clock, sent):
    feed = tfl.FeedState(mode="live")
    tfl.poll_once(ctx, feed, fetch_of("minor_with_reason"), clock.t)
    [event] = tfl_events(engine)
    assert event.state == ACTIVE

    assert tfl.set_mode(ctx, feed, "demo") == 1

    [event] = tfl_events(engine)
    assert event.state == WITHDRAWN and event.resolved_at is not None
    with Session(engine) as s:
        job_states = {j.state for j in s.exec(select(PlaybackJob)).all()}
        resolution = s.exec(select(Announcement).where(Announcement.is_resolution)).all()
    assert job_states == {CANCELLED}
    assert resolution == []  # the line did not recover: no "good service" message
    assert any(m["type"] == "event.withdrawn" and m["event_id"] == event.id for m in sent)


def test_held_tfl_event_is_withdrawn_too(ctx, engine, clock):
    """A severe TfL status waits for approval (D20); it must not be approvable after the switch."""
    feed = tfl.FeedState(mode="live")
    tfl.poll_once(ctx, feed, fetch_of("severe_with_reason"), clock.t)
    [event] = tfl_events(engine)
    assert event.approval == "pending"
    tfl.set_mode(ctx, feed, "demo")
    with Session(engine) as s, pytest.raises(events_service.EventError) as err:
        events_service.approve(s, event.id or 0, "op")
    assert err.value.status == 409


def test_tfl_never_plays_over_a_scripted_scenario(ctx, engine, clock, sent):
    feed = tfl.FeedState(mode="live")
    playback = scheduler.State()
    tfl.poll_once(ctx, feed, fetch_of("minor_with_reason"), clock.t)
    [tfl_event] = tfl_events(engine)
    tick_for(ctx, playback, clock, 3)  # the TfL announcement is on air in the tube zones
    assert any(m["type"] == "play" and m["event_id"] == tfl_event.id for m in sent)

    tfl.set_mode(ctx, feed, "demo")
    switched = len(sent)
    with Session(engine) as s:  # the scenario starts straight away, in the same zones
        scenario = events_service.create(
            s, ctx, site_id="tube_station", payload=SCENARIO_MINOR, source="replay"
        )
    tick_for(ctx, playback, clock, 30 * 60)  # 30 minutes: many repeat cycles

    after = [m for m in sent[switched:] if m["type"] == "play"]
    assert after, "the scenario must play"
    assert {m["event_id"] for m in after} == {scenario.id}
    assert all(m["type"] != "play" or m["event_id"] != tfl_event.id for m in sent[switched:])


def test_poll_in_flight_when_mode_switches_creates_nothing(ctx, engine, clock):
    """The fetch runs outside the lock; a switch to demo during it wins."""
    feed = tfl.FeedState(mode="live")

    def slow_fetch():
        tfl.set_mode(ctx, feed, "demo")  # the operator clicks Demo while the request is out
        return fetch_of("minor_with_reason")()

    tfl.poll_once(ctx, feed, slow_fetch, clock.t)
    assert tfl_events(engine) == []


def test_back_to_live_announces_again_from_fresh_data(ctx, engine, clock):
    feed = tfl.FeedState(mode="live")
    tfl.poll_once(ctx, feed, fetch_of("minor_with_reason"), clock.t)
    tfl.set_mode(ctx, feed, "demo")
    tfl.set_mode(ctx, feed, "live")
    tfl.poll_once(ctx, feed, fetch_of("minor_with_reason"), clock.t + timedelta(seconds=1))
    states = [e.state for e in tfl_events(engine)]
    assert states == [WITHDRAWN, ACTIVE]


def test_operator_and_scenario_events_are_not_withdrawn(ctx, engine, clock):
    feed = tfl.FeedState(mode="live")
    with Session(engine) as s:
        mine = events_service.create(
            s, ctx, site_id="tube_station", payload=SCENARIO_MINOR, source="form"
        )
    assert tfl.set_mode(ctx, feed, "demo") == 0
    with Session(engine) as s:
        assert (s.get(Event, mine.id) or Event()).state == ACTIVE


def test_source_api_reports_withdrawn(client):
    assert client.put("/source", json={"mode": "live"}).json()["withdrawn"] == 0
    assert client.put("/source", json={"mode": "demo"}).json()["withdrawn"] == 0


# --- Demo -> Live: the mirror image ------------------------------------------------------------


def test_switch_to_live_stops_a_running_scenario_and_withdraws_its_events(client, engine):
    """At speed 1 scenario A's second step is 6 real minutes away: the switch lands in between."""
    run_id = client.post("/scenarios/a_jubilee/run", params={"speed": 1}).json()["run_id"]
    deadline = time.time() + 10
    while client.get(f"/scenarios/runs/{run_id}").json()["steps_done"] < 1:
        assert time.time() < deadline, "first step never ran"
        time.sleep(0.02)

    assert client.put("/source", json={"mode": "live"}).json()["withdrawn"] == 1
    while client.get(f"/scenarios/runs/{run_id}").json()["state"] == "running":
        assert time.time() < deadline, "scenario kept running in live mode"
        time.sleep(0.02)
    status = client.get(f"/scenarios/runs/{run_id}").json()
    assert (status["state"], status["steps_done"]) == ("stopped", 1)
    with Session(engine) as s:
        rows = s.exec(select(Event).where(Event.source == "replay")).all()
    assert [e.state for e in rows] == [WITHDRAWN]  # step 2 (the update) never happened


def test_a_scenario_step_never_lands_in_live_mode(ctx, engine):
    feed = tfl.FeedState(mode="live")
    scenario = replay.get_scenario("c_route25")
    assert scenario is not None
    assert replay.apply_step_in_demo(ctx, feed, scenario, scenario.events[0], False) is False
    with Session(engine) as s:
        assert s.exec(select(Event)).all() == []
