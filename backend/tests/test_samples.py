"""The committed real samples (docs/samples) still match what the app would render.

No network: the ElevenLabs provider is built with a fake key and its synthesize raises, so any
cache miss fails the test instead of billing. If this fails after a change to a template, the
voice settings or the pronunciation lock, the samples no longer match and a live demo would pay
for them again: re-render with scripts/render_samples.py (it plans the cost first).
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relay.config import Settings
from relay.main import create_app
from relay.providers.tts_elevenlabs import ElevenLabsProvider

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


@pytest.mark.skipif(not (SAMPLES / "manifest.json").exists(), reason="no committed samples")
def test_scenario_b_plays_from_the_committed_samples(tmp_path, monkeypatch):
    calls: list[int] = []

    def refuse(self, req):
        calls.append(len(req.text))
        raise RuntimeError("cache miss: a live run would bill this")

    monkeypatch.setattr(ElevenLabsProvider, "synthesize", refuse)
    settings = Settings(
        elevenlabs_api_key="not-a-key", relay_tts_mode="live", relay_llm_mode="mock",
        relay_db_url=f"sqlite:///{tmp_path / 'db.sqlite'}", audio_dir=str(tmp_path / "audio"),
        relay_samples_dir=str(SAMPLES),
    )  # fmt: skip
    payload = json.loads((SCENARIOS / "b_ba117.json").read_text())["events"][0]["payload"]
    with TestClient(create_app(settings)) as client:
        event = client.post("/events", json={"site_id": "airport", "payload": payload}).json()
        summary = client.get("/metrics/summary").json()
    assert calls == []
    assert len(event["announcements"]) == 9
    assert all(a["audio_url"] and a["audio_error"] is None for a in event["announcements"])
    assert summary["tts_characters_billed"] == 0
