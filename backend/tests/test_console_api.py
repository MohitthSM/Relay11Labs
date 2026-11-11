"""API behind the Phase 6 console: metrics, sites, preview, the hallucinate toggle, list detail."""

import json
from types import SimpleNamespace

import pytest

from relay.api.metrics import percentile
from tests.test_scenarios import run_scenario, wait_for

PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
}


@pytest.mark.parametrize(
    ("values", "q", "expected"),
    [([4.0], 0.95, 4.0), ([1, 2, 3, 4], 0.5, 2.5), ([1, 2, 3, 4, 5], 0.95, 4.8)],
)
def test_percentile(values, q, expected):
    assert percentile(values, q) == pytest.approx(expected)


def test_percentile_of_nothing_is_none():
    assert percentile([], 0.5) is None


def test_metrics_are_empty_and_sane_on_a_fresh_database(client):
    s = client.get("/metrics/summary").json()
    assert s["events"] == 0
    assert s["announcements"]["total"] == 0
    assert s["announcements"]["rejection_rate"] == 0
    assert s["time_to_first_play"] == {"n": 0, "p50_s": None, "p95_s": None}
    assert s["llm_escalation_share"] == 0
    assert client.get("/metrics/announcements").json() == []


def test_metrics_after_scenario_b(client):
    assert run_scenario(client, "b_ba117")["state"] == "done"
    s = client.get("/metrics/summary").json()
    assert s["announcements"]["total"] == 9
    assert s["announcements"]["llm"] == 9  # the mock LLM writes the reference wording
    assert s["announcements"]["rejected"] == 0
    assert s["llm_calls"] == 1
    assert s["llm_routes"][0]["role"] == "default"
    assert s["tts_renders"] == 9
    assert s["tts_cache_hit_rate"] == 0
    assert s["tts_characters_billed"] > 0
    assert s["tts_cost_usd"] > 0
    # levels 2 and 3 go to the scheduled model, level 1 to the live one (D17)
    assert {m["role"] for m in s["tts_models"]} == {"live", "scheduled"}
    rows = client.get("/metrics/announcements").json()
    assert len(rows) == 9
    assert {r["language"] for r in rows} == {"en", "es", "fr"}


def test_a_rerun_shows_cache_savings(client):
    run_scenario(client, "c_route25")
    client.post("/events/1/resolve")
    run_scenario(client, "c_route25")  # same text again: served from the cache
    s = client.get("/metrics/summary").json()
    assert s["tts_cache_hit_rate"] == 0.5
    assert s["tts_characters_saved"] > 0
    assert s["tts_cost_saved_usd"] > 0


def test_time_to_first_play_is_measured_from_the_plays(client, engine):
    run_scenario(client, "c_route25")
    wait_for(lambda: client.get("/metrics/summary").json()["plays"] >= 3)
    t = client.get("/metrics/summary").json()["time_to_first_play"]
    assert t["n"] == 1
    assert 0 <= t["p50_s"] <= t["p95_s"] < 10


def test_rejections_are_counted_by_layer(client, app):
    app.state.ctx.conns.llm.hallucinate = True
    assert client.post("/events", json={"site_id": "airport", "payload": PAGE}).status_code == 201
    s = client.get("/metrics/summary").json()
    assert s["announcements"]["rejected"] >= 1
    assert s["announcements"]["template"] >= 1
    assert s["announcements"]["template_rate"] > 0
    assert sum(s["announcements"]["by_layer"].values()) >= 1
    assert s["llm_escalation_share"] > 0  # the retry went to the escalation model
    roles = {r["role"] for r in s["llm_routes"]}
    assert roles == {"default", "escalation"}
    rows = client.get("/metrics/announcements").json()
    assert any(r["rejection_layers"] for r in rows)


def test_metrics_contain_no_passenger_name(client):
    run_scenario(client, "b_ba117")
    body = json.dumps(
        [client.get("/metrics/summary").json(), client.get("/metrics/announcements").json()]
    )
    assert "Smith" not in body and "smith" not in body


def test_sites_endpoint_lists_zones_and_policies(client):
    sites = {s["id"]: s for s in client.get("/sites").json()}
    assert set(sites) == {"tube_station", "airport", "bus_station"}
    airport = sites["airport"]
    assert airport["languages"] == ["en", "es", "fr"]
    assert [z["id"] for z in airport["zones"]] == ["gate-23", "concourse-b"]
    assert airport["policies"][0]["event_type"] == "passenger_page"
    assert "Jubilee" in sites["tube_station"]["lines"]
    assert "voice_id" not in airport


def test_preview_returns_scripts_without_creating_anything(client):
    r = client.post("/events/preview", json={"site_id": "airport", "payload": PAGE})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "create"
    assert body["needs_approval"] is None  # a page from the form plays at once
    assert len(body["scripts"]) == 9  # 3 levels x 3 languages
    assert all(s["source"] == "llm" and s["rejections"] == [] for s in body["scripts"])
    assert client.get("/events").json() == []
    assert client.get("/metrics/summary").json()["llm_calls"] == 0


def test_preview_shows_a_fact_lock_rejection(client, app):
    app.state.ctx.conns.llm.hallucinate = True
    body = client.post("/events/preview", json={"site_id": "airport", "payload": PAGE}).json()
    rejected = [s for s in body["scripts"] if s["rejections"]]
    assert rejected and all(s["source"] == "template" for s in rejected)
    assert rejected[0]["rejections"][0]["violations"][0]["layer"]


def test_preview_knows_about_updates_duplicates_and_approval(client):
    jubilee = {"type": "line_status", "line": "Jubilee", "status": "severe_delays"}
    first = client.post("/events/preview", json={"site_id": "tube_station", "payload": jubilee})
    assert first.json()["action"] == "create"
    assert first.json()["needs_approval"].startswith("high severity")
    event = client.post("/events", json={"site_id": "tube_station", "payload": jubilee}).json()
    again = client.post("/events/preview", json={"site_id": "tube_station", "payload": jubilee})
    assert (again.json()["action"], again.json()["active_event_id"]) == ("duplicate", event["id"])
    minor = {**jubilee, "status": "minor_delays"}
    upd = client.post("/events/preview", json={"site_id": "tube_station", "payload": minor}).json()
    assert (upd["action"], upd["active_event_id"]) == ("update", event["id"])
    assert upd["needs_approval"] is None
    assert upd["scripts"][0]["text"].startswith("This is an update")


def test_preview_from_a_staff_note_needs_approval(client):
    r = client.post(
        "/events/preview", json={"site_id": "airport", "payload": PAGE, "source": "staff_note"}
    )
    assert r.json()["needs_approval"] == "staff note"


@pytest.mark.parametrize(
    ("site", "payload", "code"),
    [
        ("nowhere", PAGE, 404),
        ("tube_station", PAGE, 422),  # the tube station does not announce pages
        ("airport", {**PAGE, "flight_number": "nope"}, 422),
    ],
)
def test_preview_refuses_bad_input(client, site, payload, code):
    assert (
        client.post("/events/preview", json={"site_id": site, "payload": payload}).status_code
        == code
    )


def test_list_events_detail_flag(client):
    client.post("/events", json={"site_id": "airport", "payload": PAGE})
    plain = client.get("/events").json()[0]
    detailed = client.get("/events", params={"detail": "true"}).json()[0]
    assert plain["announcements"] == []
    assert len(detailed["announcements"]) == 9


def test_hallucinate_toggle_is_mock_only_and_reported_by_health(client, app):
    assert client.get("/health").json()["hallucinate"] is False
    assert client.put("/demo/hallucinate", json={"enabled": True}).json() == {"hallucinate": True}
    assert client.get("/health").json()["hallucinate"] is True
    app.state.ctx.conns.llm = SimpleNamespace(mode="live")  # anything that is not the mock
    assert client.put("/demo/hallucinate", json={"enabled": True}).status_code == 409
    assert client.get("/health").json()["hallucinate"] is None
