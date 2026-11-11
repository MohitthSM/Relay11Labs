"""The mock scenario feed: A, B and C end to end through the API, with speed control."""

import time

from sqlmodel import Session, select

from relay.models import Announcement, Event


def run_scenario(client, name: str, speed: float | None = None, timeout: float = 15) -> dict:
    params = {"speed": speed} if speed else {}
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
    assert run_scenario(client, "b_ba117")["state"] == "done"  # create-while-active -> update
    versions = sorted(e["version"] for e in client.get("/events").json())
    assert versions == [1, 2]


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
