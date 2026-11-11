"""The playback scheduler with a fake clock: no sleeping, `now` is passed to every tick.

Two kinds of test:
- Scheduler rules on hand-made rows (exact clip durations, a custom site where several event
  types share one zone): preemption at clip boundaries, resume, first come first served,
  never stack, no audio.
- The real path (events service -> script engine -> voice -> jobs -> scheduler) on the shipped
  sites in mock mode: replace on update, expiry, resolve stops repeats, escalation, resolution
  messages, approval, deduplication.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from relay import bus, jobs, scheduler, timeutil
from relay import events as events_service
from relay.context import Context
from relay.models import (
    CANCELLED,
    DONE,
    HELD,
    PLAYED,
    SCHEDULED,
    STOPPED,
    Announcement,
    AudioClip,
    Event,
    PlaybackJob,
    PlayLog,
)
from relay.providers import build_connectors
from relay.sites import PRIORITY, Policy, Site, Zone, load_sites

JUBILEE = {
    "type": "line_status",
    "line": "Jubilee",
    "status": "severe_delays",
    "reason": "signal_failure",
    "location": "Canary Wharf",
}
MINOR = {**JUBILEE, "status": "minor_delays"}
PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
    "page_type": "final_call",
}
STAND = {"type": "stand_change", "route": "25", "from_stand": "B", "to_stand": "F"}


class FakeClock:
    def __init__(self) -> None:
        self.t = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
        self.start = self.t

    def now(self) -> datetime:
        return self.t

    def at(self, seconds: float) -> datetime:
        return self.start + timedelta(seconds=seconds)

    def elapsed(self, when: datetime) -> float:
        return (when - self.start).total_seconds()


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    """Real seconds are scenario seconds (scale 1), and every timeutil.now() reads the fake."""
    timeutil.set_scale(1)
    fake = FakeClock()
    monkeypatch.setattr(timeutil, "now", fake.now)
    return fake


@pytest.fixture
def sent(monkeypatch) -> list[dict[str, Any]]:
    """Every message published on the bus, in order."""
    out: list[dict[str, Any]] = []
    monkeypatch.setattr(bus, "publish", out.append)
    return out


@pytest.fixture
def ctx(settings, engine) -> Context:
    return Context(conns=build_connectors(settings), settings=settings, engine=engine)


@pytest.fixture
def state() -> scheduler.State:
    return scheduler.State()


def run_until(ctx, state, clock, seconds: float, dt: float = 1.0) -> None:
    """Tick every `dt` seconds of fake time until `seconds` after the clock's start."""
    while clock.elapsed(clock.t) <= seconds:
        scheduler.step(ctx, state, clock.t)
        clock.t += timedelta(seconds=dt)


def plays(sent) -> list[dict[str, Any]]:
    return [m for m in sent if m["type"] == "play"]


def log_rows(engine, **where) -> list[PlayLog]:
    with Session(engine) as s:
        query = select(PlayLog).order_by(PlayLog.id)  # type: ignore[arg-type]
        for name, value in where.items():
            query = query.where(getattr(PlayLog, name) == value)
        return list(s.exec(query).all())


# --- scheduler rules on hand-made rows ---------------------------------------------------------

HALL = Site(
    id="test_hall",
    name="Test hall",
    hub_type="bus",
    style_guide="-",
    zones=[Zone(id="hall", name="Hall", languages=["en", "es"])],
    policies={
        "stand_change": Policy(zones=["hall"], interval_s=60, priority="info", approval="never"),
        "passenger_page": Policy(
            zones=["hall"], interval_s=600, max_plays=1, priority="paging", approval="never"
        ),
    },
)


def make_event(session, clock, event_type: str, durations: dict[str, float]) -> Event:
    """An event with one announcement per language whose clip lasts `durations[lang]`."""
    event = Event(event_key=f"{event_type}-{clock.t.timestamp()}", site_id=HALL.id,
                  type=event_type, payload={"type": event_type}, source="api")  # fmt: skip
    session.add(event)
    session.flush()
    for lang, seconds in durations.items():
        clip_id = f"{event.id}-{lang}".ljust(64, "0")
        session.add(AudioClip(id=clip_id, path="x.wav", kind="announcement", provider="mock",
                              model_id="m", voice_id="v", language=lang, characters=0,
                              cost_usd=0, duration_s=seconds))  # fmt: skip
        session.add(Announcement(event_id=event.id, language=lang, draft="-", script="-",
                                 spoken_text="-", source="template", clip_id=clip_id))  # fmt: skip
    jobs.schedule_event(session, event, HALL, clock.t)
    session.commit()
    session.refresh(event)
    return event


def test_preemption_waits_for_the_clip_boundary_then_the_interrupted_play_resumes(
    ctx, state, clock, sent, session
):
    info = make_event(session, clock, "stand_change", {"en": 10, "es": 10})
    scheduler.step(ctx, state, clock.t)  # info/en on air from t=0 to t=10.4
    clock.t = clock.at(3)
    page = make_event(session, clock, "passenger_page", {"en": 10, "es": 10})
    run_until(ctx, state, clock, 50)

    order = [(m["event_id"], m["language"], m["resumed"]) for m in plays(sent)]
    assert order == [
        (info.id, "en", False),
        (page.id, "en", False),  # cut in after info/en, not in the middle of it
        (page.id, "es", False),
        (info.id, "es", True),  # the interrupted play resumes where it stopped
    ]
    info_log = log_rows(ctx.engine, event_id=info.id)[0]
    assert (info_log.status, info_log.clips_played, info_log.interruptions) == (PLAYED, 2, 1)
    assert info_log.priority == PRIORITY["info"]
    assert log_rows(ctx.engine, event_id=page.id)[0].priority == PRIORITY["paging"]


def test_nothing_is_cut_mid_clip(ctx, state, clock, sent, session):
    make_event(session, clock, "stand_change", {"en": 10, "es": 10})
    clock.t = clock.at(1)
    make_event(session, clock, "passenger_page", {"en": 4, "es": 4})
    starts: list[float] = []
    while clock.elapsed(clock.t) < 40:
        before = len(plays(sent))
        scheduler.step(ctx, state, clock.t)
        starts += [clock.elapsed(clock.t)] * (len(plays(sent)) - before)
        clock.t += timedelta(seconds=0.1)
    durations = [m["duration_s"] for m in plays(sent)]
    for i in range(1, len(starts)):  # each clip starts after the one before has ended
        assert starts[i] >= starts[i - 1] + durations[i - 1] + scheduler.GAP_S - 1e-6


def test_equal_priority_is_first_come_first_served_whole_sequence(ctx, state, clock, sent, session):
    first = make_event(session, clock, "stand_change", {"en": 5, "es": 5})
    clock.t = clock.at(1)
    second = make_event(session, clock, "stand_change", {"en": 5, "es": 5})
    run_until(ctx, state, clock, 30)
    assert [m["event_id"] for m in plays(sent)][:4] == [first.id, first.id, second.id, second.id]


def test_a_repeat_due_while_the_previous_one_waits_is_merged_not_stacked(
    ctx, state, clock, sent, session
):
    page = make_event(session, clock, "passenger_page", {"en": 300, "es": 1})  # zone busy 300 s
    info = make_event(session, clock, "stand_change", {"en": 1, "es": 1})  # due every 60 s
    run_until(ctx, state, clock, 250, dt=5)
    zone = state.zones["hall"]
    waiting = [p for p in zone.queue if p.event_id == info.id]
    assert len(waiting) == 1  # four repeats fell due; one play waits
    with Session(ctx.engine) as s:
        job = s.exec(select(PlaybackJob).where(PlaybackJob.event_id == info.id)).one()
    assert job.plays_done == 1
    assert {m["event_id"] for m in plays(sent)} == {page.id}


def test_a_repeat_due_while_the_previous_one_airs_waits_for_it(ctx, state, clock, sent, session):
    info = make_event(session, clock, "stand_change", {"en": 50, "es": 50})  # 100 s > 60 s cycle
    run_until(ctx, state, clock, 200)
    starts = [round(clock.elapsed(r.played_at)) for r in log_rows(ctx.engine, event_id=info.id)]
    assert starts == [0, 102]  # repeat 2 was due at 60; it waited, it was not lost or overlapped
    langs = [m["language"] for m in plays(sent)]
    assert langs == ["en", "es", "en", "es"]


def test_a_play_without_audio_is_logged_and_skipped(ctx, state, clock, sent, session):
    event = make_event(session, clock, "stand_change", {})
    scheduler.step(ctx, state, clock.t)
    assert plays(sent) == []
    row = log_rows(ctx.engine, event_id=event.id)[0]
    assert (row.status, row.clips_played) == (PLAYED, 0)


# --- the real path, shipped sites, mock providers ----------------------------------------------


def create(ctx, site_id: str, payload: dict, **kw) -> Event:
    with Session(ctx.engine) as s:
        return events_service.create(s, ctx, site_id=site_id, payload=payload, **kw)


def update(ctx, key: str, payload: dict) -> Event:
    with Session(ctx.engine) as s:
        return events_service.update(s, ctx, key, payload)


def resolve(ctx, key: str) -> Event:
    with Session(ctx.engine) as s:
        return events_service.resolve(s, ctx, key)


def approve(ctx, event_id: int, operator: str = "Sam") -> Event:
    with Session(ctx.engine) as s:
        return events_service.approve(s, event_id, operator)


def job_states(engine, event_id: int) -> list[str]:
    with Session(engine) as s:
        rows = s.exec(select(PlaybackJob).where(PlaybackJob.event_id == event_id)).all()
        return [j.state for j in rows]


def test_update_replaces_the_queued_announcement_and_never_stacks(ctx, state, clock, sent):
    v1 = create(ctx, "tube_station", MINOR)  # minor delays: routine, plays at once
    run_until(ctx, state, clock, 30)
    assert {m["zone_id"] for m in plays(sent)} == {"jubilee-eb", "jubilee-wb", "ticket-hall"}
    update_at = clock.t
    v2 = update(ctx, v1.event_key, {**MINOR, "reason": "train_fault"})
    run_until(ctx, state, clock, 400)

    assert set(job_states(ctx.engine, v1.id)) == {CANCELLED}
    v1_plays = [m for m in plays(sent) if m["event_id"] == v1.id]
    v2_plays = [m for m in plays(sent) if m["event_id"] == v2.id]
    assert len(v1_plays) == 3  # one per zone, before the update; its 300 s repeat never came
    assert len(v2_plays) == 6  # plays at once and again 300 s later, in each zone
    assert all(r.played_at < update_at for r in log_rows(ctx.engine, event_id=v1.id))
    with Session(ctx.engine) as s:  # exactly one live job per zone: replaced, not stacked
        live = s.exec(select(PlaybackJob).where(PlaybackJob.state == SCHEDULED)).all()
    assert sorted(j.zone_id for j in live) == ["jubilee-eb", "jubilee-wb", "ticket-hall"]
    assert {j.event_id for j in live} == {v2.id}
    assert {r.event_version for r in log_rows(ctx.engine, event_id=v2.id)} == {2}


def test_update_mid_sequence_drops_the_old_clips_after_the_one_on_air(ctx, state, clock, sent):
    v1 = create(ctx, "airport", PAGE)
    scheduler.step(ctx, state, clock.t)  # en on air in both zones; es and fr wait
    clock.t = clock.at(2)
    v2 = update(ctx, v1.event_key, {**PAGE, "gate": "25"})
    run_until(ctx, state, clock, 60, dt=0.5)

    v1_langs = [m["language"] for m in plays(sent) if m["event_id"] == v1.id]
    assert v1_langs == ["en", "en"]  # one clip per zone, never cut, then nothing more
    v2_seq = [m["language"] for m in plays(sent) if m["event_id"] == v2.id]
    assert v2_seq == ["en", "en", "es", "es", "fr", "fr"]
    stopped = log_rows(ctx.engine, event_id=v1.id)
    assert [(r.status, r.clips_played) for r in stopped] == [(STOPPED, 1), (STOPPED, 1)]


def test_resolve_stops_repeats_within_one_tick(ctx, state, clock, sent):
    ev = create(ctx, "airport", PAGE)
    scheduler.step(ctx, state, clock.t)  # play 1: en on air
    clock.t = clock.at(1)
    resolve(ctx, ev.event_key)  # "Passenger boarded"
    scheduler.step(ctx, state, clock.t)  # the very next tick drops es and fr
    assert all(not z.queue for z in state.zones.values())
    run_until(ctx, state, clock, 600, dt=5)

    assert [m["language"] for m in plays(sent)] == ["en", "en"]
    assert set(job_states(ctx.engine, ev.id)) == {CANCELLED}
    assert {r.status for r in log_rows(ctx.engine, event_id=ev.id)} == {STOPPED}


def test_page_escalates_over_three_plays_then_stops_and_expires(ctx, state, clock, sent):
    ev = create(ctx, "airport", PAGE)
    run_until(ctx, state, clock, 700, dt=2)

    for zone in ("gate-23", "concourse-b"):
        rows = log_rows(ctx.engine, event_id=ev.id, zone_id=zone)
        assert [r.urgency for r in rows] == [1, 2, 3]
        assert [round(clock.elapsed(r.played_at)) for r in rows] == [0, 120, 240]
        assert all(r.clips_played == 3 and r.status == PLAYED for r in rows)  # en, es, fr
    assert len(plays(sent)) == 2 * 3 * 3
    assert set(job_states(ctx.engine, ev.id)) == {DONE}
    with Session(ctx.engine) as s:  # duration_s 360: the page then auto-resolves
        assert s.get(Event, ev.id).state == "resolved"  # type: ignore[union-attr]


def test_expiry_stand_change_plays_seven_times_then_auto_resolves(ctx, state, clock, sent):
    ev = create(ctx, "bus_station", STAND)
    run_until(ctx, state, clock, 1500, dt=3)

    for zone in ("concourse", "stand-b", "stand-f"):
        rows = log_rows(ctx.engine, event_id=ev.id, zone_id=zone)
        assert [round(clock.elapsed(r.played_at)) for r in rows] == [
            0, 180, 360, 540, 720, 900, 1080
        ]  # fmt: skip
    with Session(ctx.engine) as s:
        event = s.get(Event, ev.id)
        assert event and event.state == "resolved"
        assert clock.elapsed(event.resolved_at) == pytest.approx(1200, abs=3)  # type: ignore[arg-type]
    assert len(plays(sent)) == 21  # one render, 21 plays (7 per zone)


def test_resolution_message_plays_twice_then_silence(ctx, state, clock, sent):
    ev = create(ctx, "tube_station", MINOR)
    run_until(ctx, state, clock, 10)
    resolve(ctx, ev.event_key)
    sent.clear()
    run_until(ctx, state, clock, 1500, dt=5)

    assert all(m["is_resolution"] for m in plays(sent))
    for zone in ("jubilee-eb", "jubilee-wb", "ticket-hall"):
        rows = [r for r in log_rows(ctx.engine, zone_id=zone) if r.is_resolution]
        assert len(rows) == 2 and rows[0].priority == PRIORITY["info"]


# --- human in the loop -------------------------------------------------------------------------


def test_high_severity_waits_for_an_operator_and_the_audit_log_names_them(ctx, state, clock, sent):
    ev = create(ctx, "tube_station", JUBILEE)  # severe delays
    assert (ev.approval, ev.approval_reason) == ("pending", "high severity: severe_delays")
    run_until(ctx, state, clock, 60)
    assert plays(sent) == [] and set(job_states(ctx.engine, ev.id)) == {HELD}

    approve(ctx, ev.id, "Sam")
    run_until(ctx, state, clock, 70)
    assert len(plays(sent)) == 3
    row = log_rows(ctx.engine, event_id=ev.id)[0]
    assert (row.approved_by, row.event_version, row.zone_id) == ("Sam", 1, "jubilee-eb")
    assert clock.elapsed(row.approved_at) == 61  # type: ignore[arg-type]
    assert row.scripts and row.scripts[0]["source"] in ("llm", "template")
    assert row.scripts[0]["announcement_id"] == row.announcement_ids[0]
    with pytest.raises(events_service.EventError) as exc:
        approve(ctx, ev.id)
    assert exc.value.status == 409


def test_staff_note_waits_for_approval_low_risk_auto_plays(ctx, state, clock, sent):
    note = create(ctx, "bus_station", STAND, source="staff_note", staff_note="25 moved to F")
    assert (note.approval, note.approval_reason) == ("pending", "staff note")
    page = create(ctx, "airport", PAGE)
    assert (page.approval, page.approved_by) == ("auto", "auto")
    run_until(ctx, state, clock, 5)
    assert {m["event_id"] for m in plays(sent)} == {page.id}
    assert log_rows(ctx.engine, event_id=page.id)[0].approved_by == "auto"


def test_update_of_a_held_event_cancels_the_held_jobs(ctx, state, clock, sent):
    v1 = create(ctx, "tube_station", JUBILEE)
    v2 = update(ctx, v1.event_key, MINOR)  # stale severe message is never played
    run_until(ctx, state, clock, 5)
    assert set(job_states(ctx.engine, v1.id)) == {CANCELLED}
    assert {m["event_id"] for m in plays(sent)} == {v2.id}


@pytest.mark.parametrize(
    "mode, source, payload, reason",
    [
        ("risk", "api", MINOR, None),
        ("risk", "api", JUBILEE, "high severity: severe_delays"),
        ("risk", "api", {**JUBILEE, "status": "suspended"}, "high severity: suspended"),
        ("risk", "staff_note", MINOR, "staff note"),
        ("risk", "form", PAGE, None),
        ("always", "api", MINOR, "site policy: always"),
        ("never", "staff_note", JUBILEE, None),
    ],
)
def test_approval_rule(mode, source, payload, reason):
    got = jobs.approval_reason(mode, source=source, event_type=payload["type"], payload=payload)
    assert got == reason


def test_approval_policy_is_configurable_per_site(ctx, state, clock, monkeypatch):
    site = load_sites()["bus_station"]
    strict = site.model_copy(deep=True)
    strict.policies["stand_change"].approval = "always"
    monkeypatch.setattr(events_service, "load_sites", lambda: {"bus_station": strict})
    ev = create(ctx, "bus_station", STAND)
    assert (ev.approval, ev.approval_reason) == ("pending", "site policy: always")


# --- deduplication -------------------------------------------------------------------------


def test_identical_events_are_deduplicated(ctx, state, clock, sent):
    first = create(ctx, "bus_station", STAND)
    again = create(ctx, "bus_station", dict(STAND))
    same = update(ctx, first.event_key, dict(STAND))
    assert again.id == first.id and same.id == first.id and same.version == 1
    with Session(ctx.engine) as s:
        assert len(s.exec(select(Event)).all()) == 1
        assert len(s.exec(select(PlaybackJob)).all()) == 3  # one per zone, not two
    run_until(ctx, state, clock, 5)
    assert len(plays(sent)) == 3


def test_play_messages_carry_ids_and_urls_never_names(ctx, state, clock, sent):
    create(ctx, "airport", PAGE)
    scheduler.step(ctx, state, clock.t)
    msg = plays(sent)[0]
    assert msg["url"].startswith("/audio/") and "John" not in str(msg)
    assert set(msg) >= {"zone_id", "event_id", "play_id", "announcement_id", "language"}
