"""Events service and API in mock mode: versioning, resolve, the full path to playable audio."""

import time

import pytest
from sqlmodel import Session, select

from relay.models import Announcement, AudioClip, Event, LLMCall, VoiceRender

JUBILEE = {
    "type": "line_status",
    "line": "Jubilee",
    "status": "severe_delays",
    "reason": "signal_failure",
    "location": "Canary Wharf",
}
PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
    "page_type": "final_call",
}
STAND = {"type": "stand_change", "route": "25", "from_stand": "B", "to_stand": "F"}


def post(client, site_id, payload, **extra):
    return client.post("/events", json={"site_id": site_id, "payload": payload, **extra})


def test_health_reports_mock_per_provider(client):
    body = client.get("/health").json()
    assert (body["status"], body["llm"], body["tts"]) == ("ok", "mock", "mock")
    assert "chat" not in body and "crm" not in body


def test_scenario_b_from_post_to_playable_audio(client, engine, settings):
    """POST /events -> script engine rows -> voice -> clips you can fetch."""
    r = post(client, "airport", PAGE)
    assert r.status_code == 201
    ev = r.json()
    assert (ev["version"], ev["state"], ev["event_key"]) == (1, "active", "page-BA117-john-smith")
    anns = ev["announcements"]
    assert len(anns) == 9  # 3 languages x 3 urgency levels
    assert {(a["language"], a["urgency"]) for a in anns} == {
        (lang, u) for lang in ("en", "es", "fr") for u in (1, 2, 3)
    }
    assert all(a["audio_url"] and a["audio_error"] is None for a in anns)
    assert all(a["source"] == "template" or a["source"] == "llm" for a in anns)
    assert "John Smith" in next(a for a in anns if a["language"] == "en")["script"]

    audio = client.get(anns[0]["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF" and anns[0]["duration_s"] > 1  # chime + speech

    with Session(engine) as s:
        assert len(s.exec(select(LLMCall)).all()) >= 1  # script call logged
        assert len(s.exec(select(Announcement)).all()) == 9
        renders = s.exec(select(VoiceRender)).all()
        assert len(renders) == 9 and {r.model_id for r in renders} == {
            "eleven_v4_turbo",  # level 1: live
            "eleven_v4",  # levels 2 and 3: scheduled
        }
        assert all(r.event_id == ev["id"] for r in renders)


def test_chime_only_in_the_first_language_clip(client):
    anns = post(client, "airport", PAGE).json()["announcements"]
    level1 = {a["language"]: a for a in anns if a["urgency"] == 1}
    # en has the chime stitched in front (1.2 s + gap) so it is clearly longer than its text alone
    plain = len(level1["en"]["spoken_text"]) * 0.06
    assert level1["en"]["duration_s"] == pytest.approx(plain + 1.2 + 0.25, abs=0.1)
    assert level1["es"]["duration_s"] == pytest.approx(
        len(level1["es"]["spoken_text"]) * 0.06, abs=0.1
    )


def test_list_and_get(client):
    ev = post(client, "bus_station", STAND).json()
    listed = client.get("/events").json()
    assert [e["id"] for e in listed] == [ev["id"]] and listed[0]["announcements"] == []
    assert client.get("/events", params={"state": "resolved"}).json() == []
    assert client.get("/events", params={"site_id": "airport"}).json() == []
    got = client.get(f"/events/{ev['id']}").json()
    assert len(got["announcements"]) == 1
    assert client.get("/events/999").status_code == 404


def test_update_makes_a_new_version_and_supersedes_the_old(client, engine):
    first = post(client, "tube_station", JUBILEE).json()
    r = client.put(
        f"/events/{first['event_key']}", json={"payload": {**JUBILEE, "status": "minor_delays"}}
    )
    assert r.status_code == 200
    second = r.json()
    assert (second["version"], second["state"]) == (2, "active")
    assert "minor delays" in second["announcements"][0]["script"]
    assert second["announcements"][0]["script"].startswith("This is an update.")  # F3
    old = client.get(f"/events/{first['id']}").json()
    assert old["state"] == "superseded" and "severe" in old["announcements"][0]["script"]
    with Session(engine) as s:
        states = [e.state for e in s.exec(select(Event).order_by(Event.version)).all()]
    assert states == ["superseded", "active"]


def test_resolve_a_line_plays_good_service_message(client):
    ev = post(client, "tube_station", JUBILEE).json()
    r = client.post(f"/events/{ev['id']}/resolve")
    assert r.status_code == 200
    out = r.json()
    assert out["state"] == "resolved" and out["resolved_at"]
    res = [a for a in out["announcements"] if a["is_resolution"]]
    assert len(res) == 1 and "Good service has now resumed on the Jubilee line" in res[0]["script"]
    assert res[0]["audio_url"] and client.get(res[0]["audio_url"]).status_code == 200


def test_resolve_a_page_has_no_resolution_message(client):
    ev = post(client, "airport", PAGE).json()
    out = client.post(f"/events/{ev['id']}/resolve").json()
    assert out["state"] == "resolved"
    assert not any(a["is_resolution"] for a in out["announcements"])
    # and the key is free again
    assert post(client, "airport", PAGE).status_code == 201


@pytest.mark.parametrize(
    "site, payload, code",
    [
        ("airport", {**PAGE, "flight_number": "BAD"}, 422),  # bad flight number
        ("tube_station", {**JUBILEE, "status": "on_fire"}, 422),  # unknown status
        ("airport", {k: v for k, v in PAGE.items() if k != "gate"}, 422),  # missing gate
        ("airport", STAND, 422),  # the airport does not announce stand changes
        ("nowhere", PAGE, 404),
        ("airport", {"type": "evacuation"}, 422),  # out of scope: no such event type
    ],
)
def test_bad_events_are_4xx(client, site, payload, code):
    assert post(client, site, payload).status_code == code


def test_duplicate_active_key_conflicts_and_update_rules(client):
    ev = post(client, "tube_station", JUBILEE).json()
    minor = {**JUBILEE, "status": "minor_delays"}
    assert post(client, "tube_station", minor, event_key=ev["event_key"]).status_code == 409
    key = ev["event_key"]
    assert client.put("/events/unknown-key", json={"payload": JUBILEE}).status_code == 404
    assert client.put(f"/events/{key}", json={"payload": PAGE}).status_code == 422  # type change
    assert client.post("/events/999/resolve").status_code == 404


def test_tts_failure_is_not_a_500(client, app):
    app.state.ctx.conns.tts.failures.fail_next(50, retryable=False)
    r = post(client, "bus_station", STAND)
    assert r.status_code == 201
    anns = r.json()["announcements"]
    assert anns[0]["script"] and anns[0]["audio_url"] is None and anns[0]["audio_error"]


def test_llm_failure_falls_back_to_templates(client, app):
    app.state.ctx.conns.llm.failures.fail_next(5, retryable=False)
    anns = post(client, "airport", PAGE).json()["announcements"]
    assert len(anns) == 9 and {a["source"] for a in anns} == {"template"}
    assert all(a["audio_url"] for a in anns)


def test_hallucination_is_rejected_and_the_template_plays(settings):
    from fastapi.testclient import TestClient

    from relay.main import create_app

    settings.relay_mock_hallucinate = True
    with TestClient(create_app(settings)) as c:
        anns = post(c, "airport", PAGE).json()["announcements"]
    bad = [a for a in anns if a["rejections"]]
    assert bad and bad[0]["source"] == "template"  # the demo moment: rejection reasons shown
    assert bad[0]["rejections"][0]["violations"]


def test_rerun_hits_the_audio_cache(client, engine):
    first = post(client, "bus_station", STAND).json()
    client.post(f"/events/{first['id']}/resolve")
    post(client, "bus_station", STAND)
    with Session(engine) as s:
        renders = s.exec(select(VoiceRender).order_by(VoiceRender.id)).all()
    assert renders[0].characters_billed > 0 and not renders[0].cache_hit
    assert renders[-1].cache_hit and renders[-1].characters_billed == 0


def test_no_passenger_name_in_logs_or_voice_rows(client, engine, caplog):
    import logging

    with caplog.at_level(logging.DEBUG):
        post(client, "airport", PAGE)
    assert "Smith" not in " ".join(r.getMessage() for r in caplog.records)
    with Session(engine) as s:
        dump = str([r.model_dump() for r in s.exec(select(VoiceRender)).all()])
        dump += str([c.model_dump() for c in s.exec(select(AudioClip)).all()])
    assert "Smith" not in dump


def next_state_change(ws) -> dict:
    """The next event.* message; play commands share the socket (D8) and are skipped here."""
    while True:
        msg = ws.receive_json()
        if msg["type"] != "play":
            return msg


def test_websocket_pushes_state_changes(client):
    with client.websocket_connect("/ws/events") as ws:
        assert ws.receive_json() == {"type": "snapshot", "active": []}
        ev = post(client, "tube_station", JUBILEE).json()
        msg = next_state_change(ws)
        assert msg["type"] == "event.created" and msg["event_id"] == ev["id"]
        assert "John" not in str(msg) and "event_key" not in msg
        assert msg["approval"] == "pending"  # severe delays wait for an operator (D20)
        client.post(f"/events/{ev['id']}/approve", json={"operator": "Sam"})
        assert next_state_change(ws)["type"] == "event.approved"
        client.put(
            f"/events/{ev['event_key']}", json={"payload": {**JUBILEE, "status": "minor_delays"}}
        )
        assert next_state_change(ws)["type"] == "event.updated"
        client.post(f"/events/{ev['id'] + 1}/resolve")
        assert next_state_change(ws)["type"] == "event.resolved"
    with client.websocket_connect("/ws/events") as ws2:  # a late client gets the snapshot
        assert ws2.receive_json()["active"] == []


def test_websocket_snapshot_lists_active_events(client):
    ev = post(client, "airport", PAGE).json()
    with client.websocket_connect("/ws/events") as ws:
        snap = ws.receive_json()
    assert [e["event_id"] for e in snap["active"]] == [ev["id"]]


def test_segment_splitting_can_be_switched_off(settings):
    from fastapi.testclient import TestClient

    from relay.main import create_app

    def billed(split: bool) -> int:
        settings.voice_split_segments = split
        settings.relay_db_url = settings.relay_db_url.replace(".db", f"{split}.db")
        settings.audio_dir += str(split)
        with TestClient(create_app(settings)) as c:
            post(c, "tube_station", JUBILEE)
            eng = c.app.state.ctx.engine  # type: ignore[attr-defined]
        with Session(eng) as s:
            return len(s.exec(select(AudioClip).where(AudioClip.kind == "segment")).all())

    assert billed(True) == 3  # opening, middle, closing
    assert billed(False) == 1  # voiced whole


def test_approve_endpoint(client):
    ev = post(client, "tube_station", JUBILEE).json()
    assert (ev["approval"], ev["approval_reason"]) == ("pending", "high severity: severe_delays")
    url = f"/events/{ev['id']}/approve"
    assert client.post(url, json={"operator": "  "}).status_code == 422
    assert client.post("/events/999/approve", json={"operator": "Sam"}).status_code == 404
    out = client.post(url, json={"operator": "Sam"}).json()
    assert (out["approval"], out["approved_by"]) == ("approved", "Sam") and out["approved_at"]
    assert client.post(url, json={"operator": "Sam"}).status_code == 409  # already approved
    routine = post(client, "bus_station", STAND).json()
    assert (routine["approval"], routine["approved_by"]) == ("auto", "auto")
    r = client.post(f"/events/{routine['id']}/approve", json={"operator": "Sam"})
    assert r.status_code == 409


def test_duplicate_post_and_put_change_nothing(client):
    first = post(client, "bus_station", STAND)
    assert first.status_code == 201 and first.json()["duplicate"] is False
    again = post(client, "bus_station", STAND)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert again.json()["id"] == first.json()["id"]
    same = client.put(f"/events/{first.json()['event_key']}", json={"payload": STAND}).json()
    assert same["duplicate"] is True and same["version"] == 1
    assert len(client.get("/events").json()) == 1


def test_playback_zones_and_log(client):
    zones = client.get("/playback/zones").json()
    assert {z["zone_id"] for z in zones} >= {"gate-23", "concourse-b", "jubilee-eb", "stand-f"}
    assert [z["zone_id"] for z in client.get("/playback/zones?site_id=airport").json()] == [
        "gate-23",
        "concourse-b",
    ]
    held = post(client, "tube_station", JUBILEE).json()
    eb = next(z for z in client.get("/playback/zones").json() if z["zone_id"] == "jubilee-eb")
    assert eb["held"] == 1 and eb["next"] is None and eb["on_air"] is None
    client.post(f"/events/{held['id']}/approve", json={"operator": "Sam"})
    deadline = time.time() + 5
    while time.time() < deadline and not client.get("/playback/log").json():
        time.sleep(0.02)
    rows = client.get("/playback/log", params={"zone_id": "jubilee-eb"}).json()
    assert rows and rows[0]["approved_by"] == "Sam" and rows[0]["event_id"] == held["id"]
    assert "John" not in str(client.get("/playback/log").json())
