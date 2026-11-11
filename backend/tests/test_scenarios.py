"""The mock scenario feed: A, B and C end to end through the API, with speed control."""

import time

from sqlmodel import Session, select

from relay.models import Announcement, Event


def run_scenario(
    client, name: str, speed: float | None = None, timeout: float = 15, auto_approve: bool = False
) -> dict:
    params: dict = {"speed": speed} if speed else {}
    if auto_approve:
        params["auto_approve"] = "true"
    r = client.post(f"/scenarios/{name}/run", params=params)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/scenarios/runs/{run_id}").json()
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("scenario did not finish")


def test_list_scenarios(client):
    body = client.get("/scenarios").json()
    assert [s["name"] for s in body["scenarios"]] == ["a_jubilee", "b_ba117", "c_route25"]
    assert body["speed"] == 1000  # conftest time scale
    assert client.post("/scenarios/nope/run").status_code == 404
    assert client.get("/scenarios/runs/nope").status_code == 404


def test_scenario_a_update_supersedes_then_resolves(client, engine):
    status = run_scenario(client, "a_jubilee")
    assert (status["state"], status["steps_done"]) == ("done", 3)
    with Session(engine) as s:
        events = s.exec(select(Event).order_by(Event.id)).all()
        assert [(e.version, e.state) for e in events] == [(1, "superseded"), (2, "resolved")]
        scripts = [
            (a.event_id, a.is_resolution, a.script)
            for a in s.exec(select(Announcement).order_by(Announcement.id)).all()
        ]
    assert "severe delays" in scripts[0][2]
    assert "minor delays" in scripts[1][2]
    assert scripts[2][1] and "Good service has now resumed" in scripts[2][2]


def test_scenario_b_nine_clips_all_playable(client):
    assert run_scenario(client, "b_ba117")["state"] == "done"
    ev = client.get("/events", params={"state": "active"}).json()[0]
    anns = client.get(f"/events/{ev['id']}").json()["announcements"]
    assert len(anns) == 9
    for a in anns:
        assert client.get(a["audio_url"]).status_code == 200


def test_scenario_c_creates_one_announcement(client):
    assert run_scenario(client, "c_route25")["state"] == "done"
    ev = client.get("/events").json()[0]
    assert ev["type"] == "stand_change"
    assert len(client.get(f"/events/{ev['id']}").json()["announcements"]) == 1


def test_a_scenario_can_run_twice(client):
    assert run_scenario(client, "b_ba117")["state"] == "done"
    assert run_scenario(client, "b_ba117")["state"] == "done"  # identical: a duplicate (D21)
    versions = sorted(e["version"] for e in client.get("/events").json())
    assert versions == [1]


# --- end to end with the real scheduler loop (mock mode, RELAY_TIME_SCALE=1000) ---------------


def wait_for(predicate, timeout: float = 15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def play_log(client, **params) -> list[dict]:
    return sorted(client.get("/playback/log", params=params).json(), key=lambda r: r["id"])


def resolution_plays(client) -> list[dict]:
    rows = play_log(client)
    return rows if sum(r["is_resolution"] for r in rows) == 6 else []


def test_e2e_a_update_replaces_and_good_service_plays_twice(client):
    assert run_scenario(client, "a_jubilee", auto_approve=True)["state"] == "done"
    rows = wait_for(lambda: resolution_plays(client))
    v1, v2 = sorted({r["event_id"] for r in rows})
    events = {e["id"]: e for e in client.get("/events").json()}
    assert events[v1]["approved_by"] == "scenario runner"  # severe: held, then approved
    assert events[v2]["approved_by"] == "auto"  # minor delays: routine
    for zone in ("jubilee-eb", "jubilee-wb", "ticket-hall"):
        zrows = [r for r in rows if r["zone_id"] == zone]
        old = [r for r in zrows if r["event_id"] == v1]
        new = [r for r in zrows if r["event_id"] == v2 and not r["is_resolution"]]
        good = [r for r in zrows if r["is_resolution"]]
        assert old and new and len(good) == 2
        assert max(r["played_at"] for r in old) < events[v2]["created_at"]  # severe stopped
        assert max(r["played_at"] for r in new) < min(r["played_at"] for r in good)


def test_e2e_b_three_plays_per_zone_escalating(client):
    assert run_scenario(client, "b_ba117")["state"] == "done"
    ev = client.get("/events").json()[0]
    wait_for(lambda: client.get(f"/events/{ev['id']}").json()["state"] == "resolved")
    for zone in ("gate-23", "concourse-b"):
        rows = play_log(client, zone_id=zone)
        assert [r["urgency"] for r in rows] == [1, 2, 3]
        assert all(r["clips_played"] == 3 for r in rows)  # en, es, fr


def test_e2e_c_seven_plays_then_auto_resolve(client):
    assert run_scenario(client, "c_route25")["state"] == "done"
    ev = client.get("/events").json()[0]
    wait_for(lambda: client.get(f"/events/{ev['id']}").json()["state"] == "resolved")
    for zone in ("concourse", "stand-b", "stand-f"):
        assert len(play_log(client, zone_id=zone)) == 7
    clips = {a for r in play_log(client) for a in r["announcement_ids"]}
    assert len(clips) == 1  # one announcement, one render, 21 plays


def test_speed_control(client):
    assert client.get("/speed").json() == {"scale": 1000}
    assert client.put("/speed", json={"scale": 60}).json() == {"scale": 60}
    assert client.put("/speed", json={"scale": 0}).status_code == 422
    run_scenario(client, "c_route25", speed=2000)
    assert client.get("/speed").json() == {"scale": 2000}


def test_speed_scales_the_replay_timeline(client):
    t0 = time.time()
    run_scenario(client, "a_jubilee", speed=1000)  # 720 s of scenario time
    fast = time.time() - t0
    assert fast < 5  # at 1x this would take 12 minutes
