import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from relay.config import Settings
from relay.main import create_app

PAYLOAD = {"line": "Jubilee", "status": "Severe Delays", "reason": "signal failure"}


def client(tmp_path, **kw):
    settings = Settings(relay_db_url=f"sqlite:///{tmp_path / 'api.db'}", **kw)
    return TestClient(create_app(settings))


def test_health_reports_mock(tmp_path):
    with client(tmp_path) as c:
        body = c.get("/health").json()
        assert body["llm"] == body["tts"] == body["chat"] == body["crm"] == "mock"


def test_webhook_idempotency_and_approval_flow(tmp_path):
    with client(tmp_path) as c:
        h = {"Idempotency-Key": "abc"}
        r1 = c.post("/webhooks/disruption_followup", json=PAYLOAD, headers=h).json()
        r2 = c.post("/webhooks/disruption_followup", json=PAYLOAD, headers=h).json()
        assert r1["id"] == r2["id"] and r1["status"] == "paused"
        done = c.post(
            f"/runs/{r1['id']}/approval", json={"approved": True, "approver": "sup"}
        ).json()
        assert done["status"] == "succeeded"
        assert [s["step_name"] for s in done["steps"]][-1] == "notify_slack"
        assert c.get("/runs", params={"status": "succeeded"}).json()[0]["id"] == r1["id"]


def test_webhook_requires_key_and_valid_payload(tmp_path):
    with client(tmp_path) as c:
        assert c.post("/webhooks/disruption_followup", json=PAYLOAD).status_code == 422
        bad = c.post("/webhooks/disruption_followup", json={}, headers={"Idempotency-Key": "z"})
        assert bad.status_code == 422
        assert (
            c.post("/webhooks/nope", json={}, headers={"Idempotency-Key": "z"}).status_code == 404
        )


def test_slack_signature_checked_when_secret_set(tmp_path):
    from relay.workflow.definitions import WORKFLOWS

    with client(tmp_path, slack_signing_secret="s3cret") as c:
        assert c.post("/slack/commands", content=b"command=%2Fx").status_code == 401
        body = b"command=%2Fnone&text=&trigger_id=T1"
        ts = str(int(time.time()))
        sig = (
            "v0="
            + hmac.new(b"s3cret", f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
        )
        r = c.post("/slack/commands", content=body,
                   headers={"x-slack-request-timestamp": ts, "x-slack-signature": sig})  # fmt: skip
        assert r.status_code == 200 and "Unknown command" in r.json()["text"]
    assert WORKFLOWS  # silence unused import in case of future edits


def test_slack_command_starts_run_once(tmp_path):
    from dataclasses import replace

    from relay.workflow.steps import Trigger

    with client(tmp_path) as c:
        ctx = c.app.state.ctx
        base = ctx.workflows["disruption_followup"]
        ctx.workflows = {
            base.name: replace(base, trigger=Trigger("slack_command", base.trigger.payload_model,
                                                     command="/disruption"))
        }  # fmt: skip
        body = b"command=%2Fdisruption&text=line%3Djubilee+status%3Dsuspended&trigger_id=T9"
        a = c.post("/slack/commands", content=body).json()["text"]
        b = c.post("/slack/commands", content=body).json()["text"]
        assert a == b and "paused" in a  # same run id, not a second run
