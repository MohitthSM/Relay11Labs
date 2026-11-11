"""TfL poller (Phase 7) with recorded fixtures and a fake clock: no network, no sleeping.

Pure parts (parse, decide, backoff, status) are tested directly; the rest drives `poll_once`
against the real events service in mock mode.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from relay import events as events_service
from relay.context import Context
from relay.models import ACTIVE, RESOLVED, Announcement, Event
from relay.providers import build_connectors
from relay.sites import load_sites
from relay.sources import tfl
from relay.sources.flights import ExampleRealFlightFeed, MockFlightFeed, build_flight_feed

FIXTURES = Path(__file__).parent / "fixtures" / "tfl"
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
STATIONS = load_sites()["tube_station"].gazetteer.stations


def fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def fetch_of(name: str):
    return lambda: fixture(name)


def failing(message: str = "TfL answered 503", retry_after: float | None = None):
    def fetch():
        raise tfl.FeedError(message, retry_after)

    return fetch


@pytest.fixture
def ctx(settings, engine):
    return Context(conns=build_connectors(settings), settings=settings, engine=engine)


@pytest.fixture
def state():
    return tfl.FeedState(mode="live")


def events(engine) -> list[Event]:
    with Session(engine) as s:
        return list(s.exec(select(Event).order_by(Event.id)).all())


# --- parsing -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "status", "reason", "location"),
    [
        ("good", "good_service", None, None),
        ("minor_with_reason", "minor_delays", "train_fault", "Westminster"),
        ("severe_with_reason", "severe_delays", "signal_failure", "Canary Wharf"),
        ("part_suspended", "part_suspended", "customer_incident", None),  # two stations: ambiguous
        ("planned_closure", None, None, None),  # not a status Relay announces
    ],
)
def test_parse_fixtures(name, status, reason, location):
    got = tfl.parse_status(fixture(name), ["jubilee"], STATIONS)["jubilee"]
    assert (got.status, got.reason, got.location) == (status, reason, location)
    assert got.name == "Jubilee"


def test_parse_only_wanted_lines():
    assert list(tfl.parse_status(fixture("good"), ["victoria"], STATIONS)) == ["victoria"]


def test_malformed_response_is_an_error():
    with pytest.raises(tfl.FeedError):
        tfl.parse_status(fixture("malformed"), ["jubilee"], STATIONS)
    with pytest.raises(tfl.FeedError):  # a list, but none of the wanted lines in it
        tfl.parse_status([], ["jubilee"], STATIONS)


def test_malformed_line_is_left_out_not_guessed():
    got = tfl.parse_status(fixture("malformed_line"), ["jubilee", "victoria"], STATIONS)
    assert list(got) == ["victoria"]


def test_reason_is_never_guessed():
    assert tfl.infer_reason("Minor delays due to train cancellations.") is None
    assert tfl.infer_reason("Severe delays due to a signal failure") == "signal_failure"
    assert tfl.infer_reason("Part suspended due to a power failure") == "power_failure"
    assert tfl.find_location("Delays between Stratford and West Ham", STATIONS) is None
    assert tfl.find_location("Delays at Holborn", STATIONS) == "Holborn"
    assert tfl.find_location("Delays at Holborn Circus", STATIONS) == "Holborn"  # word boundary ok


@pytest.mark.parametrize(
    ("status", "active", "action"),
    [
        ("good_service", False, "none"),  # nothing wrong, nothing active: silent
        ("severe_delays", False, "create"),
        ("severe_delays", True, "update"),
        ("good_service", True, "resolve"),
        (None, True, "none"),
        (None, False, "none"),
    ],
)
def test_decide(status, active, action):
    reading = tfl.Reading("jubilee", "Jubilee", 6, status, None, None)
    assert tfl.decide(reading, {"x": 1} if active else None) == action


def test_backoff_doubles_and_caps():
    assert [tfl.backoff_s(n, 60) for n in (1, 2, 3, 4, 5, 6)] == [60, 120, 240, 480, 600, 600]
    assert tfl.backoff_s(1, 60, retry_after=900) == 900  # Retry-After wins when longer


# --- HTTP edge ---------------------------------------------------------------------------------


def make_client_fetch(settings, handler):
    return tfl.make_fetch(settings, httpx.MockTransport(handler))


def test_fetch_ok_and_app_key_only_when_set(settings):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.params.get("app_key"))
        return httpx.Response(200, json=fixture("good"))

    assert make_client_fetch(settings, handler)()[0]["id"] == "jubilee"
    keyed = settings.model_copy(update={"tfl_app_key": "k-123"})
    make_client_fetch(keyed, handler)()
    assert seen == [None, "k-123"]  # works without a key


@pytest.mark.parametrize("code", [429, 500, 503, 404])
def test_fetch_http_errors_become_feed_errors(settings, code):
    fetch = make_client_fetch(
        settings, lambda req: httpx.Response(code, headers={"Retry-After": "90"})
    )
    with pytest.raises(tfl.FeedError) as err:
        fetch()
    assert str(code) in str(err.value)
    assert err.value.retry_after == (90 if code in (429, 500, 503) else None)


def test_fetch_errors_never_carry_the_key(settings):
    keyed = settings.model_copy(update={"tfl_app_key": "SECRET-KEY"})

    def boom(req):
        raise httpx.ConnectError("could not connect", request=req)

    with pytest.raises(tfl.FeedError) as err:
        make_client_fetch(keyed, boom)()
    assert "SECRET-KEY" not in str(err.value)
    with pytest.raises(tfl.FeedError, match="not JSON"):
        make_client_fetch(keyed, lambda req: httpx.Response(200, text="<html>"))()


# --- polling against the events service --------------------------------------------------------


def test_demo_mode_does_not_poll(ctx):
    state = tfl.FeedState(mode="demo")

    def fetch():
        raise AssertionError("must not fetch in demo mode")

    assert tfl.poll_once(ctx, state, fetch, T0) is False


def test_good_service_with_nothing_active_announces_nothing(ctx, engine, state):
    tfl.poll_once(ctx, state, fetch_of("good"), T0)
    assert events(engine) == []
    assert state.last_ok == T0 and state.failures == 0


def test_delay_then_same_status_then_worse_then_resumed(ctx, engine, state):
    t = T0
    tfl.poll_once(ctx, state, fetch_of("minor_with_reason"), t)
    rows = events(engine)
    assert [(e.version, e.state, e.source, e.event_key) for e in rows] == [
        (1, ACTIVE, "tfl", "tfl-jubilee")
    ]
    assert rows[0].payload["status"] == "minor_delays"

    t += timedelta(seconds=61)  # same facts again: nothing new is emitted
    tfl.poll_once(ctx, state, fetch_of("minor_with_reason"), t)
    assert len(events(engine)) == 1

    t += timedelta(seconds=61)  # worse: a new version replaces the old one
    tfl.poll_once(ctx, state, fetch_of("severe_with_reason"), t)
    rows = events(engine)
    assert [(e.version, e.state) for e in rows] == [(1, "superseded"), (2, ACTIVE)]

    t += timedelta(seconds=61)  # back to normal: resolved, with the good-service message
    tfl.poll_once(ctx, state, fetch_of("good"), t)
    rows = events(engine)
    assert rows[-1].state == RESOLVED
    with Session(engine) as s:
        resolution = s.exec(select(Announcement).where(Announcement.is_resolution)).all()
    assert resolution and all("Good service" in a.script for a in resolution if a.language == "en")


def test_not_due_yet_means_no_fetch(ctx, state):
    tfl.poll_once(ctx, state, fetch_of("good"), T0)
    calls = []
    tfl.poll_once(ctx, state, lambda: calls.append(1), T0 + timedelta(seconds=30))
    assert calls == []


def test_unannounced_status_changes_nothing(ctx, engine, state):
    tfl.poll_once(ctx, state, fetch_of("planned_closure"), T0)
    assert events(engine) == []


def test_failure_keeps_last_state_and_announces_nothing(ctx, engine, state):
    tfl.poll_once(ctx, state, fetch_of("severe_with_reason"), T0)
    assert len(events(engine)) == 1
    t = T0 + timedelta(seconds=61)
    tfl.poll_once(ctx, state, failing(), t)
    rows = events(engine)
    assert [(e.version, e.state) for e in rows] == [(1, ACTIVE)]  # no resolve, no update
    assert state.readings["jubilee"].status == "severe_delays"  # last state kept
    status = tfl.feed_status(state, ctx.settings, t)
    assert status["status"] == "error" and "stale data" in status["warning"]
    assert state.next_poll == t + timedelta(seconds=60)  # first failure: base interval
    t2 = state.next_poll
    tfl.poll_once(ctx, state, failing(), t2)
    assert state.next_poll == t2 + timedelta(seconds=120)  # backs off


def test_malformed_response_never_resolves_an_active_line(ctx, engine, state):
    tfl.poll_once(ctx, state, fetch_of("severe_with_reason"), T0)
    tfl.poll_once(ctx, state, fetch_of("malformed"), T0 + timedelta(seconds=61))
    assert [e.state for e in events(engine)] == [ACTIVE]
    assert state.failures == 1


def test_recovery_clears_the_warning(ctx, state):
    tfl.poll_once(ctx, state, failing(), T0)
    assert tfl.feed_status(state, ctx.settings, T0)["status"] == "error"
    t = state.next_poll
    assert t is not None
    tfl.poll_once(ctx, state, fetch_of("good"), t)
    status = tfl.feed_status(state, ctx.settings, t)
    assert (status["status"], status["warning"]) == ("ok", None)


def test_missing_line_keeps_its_event(ctx, engine, state):
    tfl.poll_once(ctx, state, fetch_of("severe_with_reason"), T0)
    ctx.settings.relay_tfl_lines = "jubilee,victoria"
    only_victoria = [x for x in fixture("good") if x["id"] != "jubilee"]
    tfl.poll_once(ctx, state, lambda: only_victoria, T0 + timedelta(seconds=61))
    assert [e.state for e in events(engine)] == [ACTIVE]
    assert "jubilee" in state.line_errors


def test_stale_status_when_polls_stop(ctx, state):
    tfl.poll_once(ctx, state, fetch_of("good"), T0)
    later = T0 + timedelta(seconds=ctx.settings.relay_tfl_poll_s * tfl.STALE_AFTER_POLLS + 1)
    status = tfl.feed_status(state, ctx.settings, later)
    assert status["status"] == "stale" and status["warning"]


def test_never_takes_over_an_operator_event(ctx, engine, state):
    with Session(engine) as s:
        events_service.create(
            s, ctx, site_id="tube_station", event_key="tfl-jubilee", source="form",
            payload={"type": "line_status", "line": "Jubilee", "status": "suspended"},
        )  # fmt: skip
    tfl.poll_once(ctx, state, fetch_of("good"), T0)
    assert [(e.source, e.state) for e in events(engine)] == [("form", ACTIVE)]


def test_line_outside_the_gazetteer_is_reported_not_announced(ctx, engine, state):
    ctx.settings.relay_tfl_lines = "metropolitan"
    raw = [
        {"id": "metropolitan", "name": "Metro Line Z", "lineStatuses": [{"statusSeverity": 6}]},
    ]
    tfl.poll_once(ctx, state, lambda: raw, T0)
    assert events(engine) == []
    assert "gazetteer" in state.line_errors["metropolitan"]


def test_set_mode_live_polls_at_once(ctx, state):
    state.next_poll = T0 + timedelta(hours=1)
    state.mode = "demo"
    assert tfl.set_mode(ctx, state, "live") == 0
    assert state.mode == "live" and state.next_poll is None


# --- API and flights ---------------------------------------------------------------------------


def test_source_api_toggle_and_scenarios_refused_in_live(client):
    assert client.get("/source").json()["mode"] == "demo"
    assert client.get("/source").json()["flights"] == "mock"
    r = client.put("/source", json={"mode": "bogus"})
    assert r.status_code == 422
    assert client.put("/source", json={"mode": "live"}).json()["mode"] == "live"
    assert client.post("/scenarios/a_jubilee/run").status_code == 409
    assert client.put("/source", json={"mode": "demo"}).json()["status"] == "off"
    assert client.post("/scenarios/a_jubilee/run").status_code == 202


def test_source_change_is_pushed_on_the_websocket(client):
    with client.websocket_connect("/ws/events") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        client.put("/source", json={"mode": "live"})
        seen = [ws.receive_json() for _ in range(2)]
    assert any(m["type"] == "source" and m["mode"] == "live" for m in seen)


def test_flight_feed_is_mock_and_the_stub_says_so(settings):
    feed = build_flight_feed(settings)
    assert isinstance(feed, MockFlightFeed) and feed.mode == "mock"
    assert feed.departures()[0].flight_number == "BA117"
    with pytest.raises(NotImplementedError):
        ExampleRealFlightFeed("key").departures()


def test_flights_endpoint(client):
    assert client.get("/flights").json()[0]["flight_number"] == "BA117"
