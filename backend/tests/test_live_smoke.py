"""Live smoke test against Claude. Runs only with `-m live` and ANTHROPIC_API_KEY set."""

import os
from datetime import UTC, datetime

import pytest

from relay.config import Settings
from relay.providers import build_llm, build_tts
from relay.schemas import EventInput
from relay.script.engine import build_context, generate
from relay.sites import get_site

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("RELAY_LIVE_ANTHROPIC_API_KEY"), reason="no live key")
def test_scenario_b_scripts_from_claude():
    settings = Settings(
        anthropic_api_key=os.environ["RELAY_LIVE_ANTHROPIC_API_KEY"], relay_llm_mode="live"
    )
    llm = build_llm(settings)
    assert llm.mode == "live"
    ev = EventInput(
        event_key="page",
        site_id="airport",
        payload={
            "type": "passenger_page",
            "passenger_name": "John Smith",
            "flight_number": "BA117",
            "destination": "New York",
            "gate": "23",
        },
    )
    site = get_site("airport")
    plan = generate(ev, build_context(site, ev.type), llm, settings, now=datetime.now(UTC))
    assert len(plan.scripts) == 9
    assert plan.llm_calls[0].outcome in ("accepted", "rejected")  # structured output parsed
    assert plan.llm_calls[0].input_tokens > 0


@pytest.mark.skipif(not os.environ.get("RELAY_LIVE_ELEVENLABS_API_KEY"), reason="no live key")
def test_one_clip_from_elevenlabs(tmp_path):
    """Renders one real announcement on the live model: costs about 0.7 cent."""
    from sqlmodel import Session

    from relay import voice
    from relay.db import init_db, make_engine

    settings = Settings(
        elevenlabs_api_key=os.environ["RELAY_LIVE_ELEVENLABS_API_KEY"],
        relay_tts_mode="live",
        audio_dir=str(tmp_path / "audio"),
    )
    tts = build_tts(settings)
    assert tts.mode == "live"
    engine = make_engine(f"sqlite:///{tmp_path / 'live.db'}")
    init_db(engine)
    site = get_site("tube_station")
    item = voice.Item("Customer information. Holborn and Marylebone are open.", "en")
    with Session(engine) as session:
        first = voice.render(session, tts, settings, site, item)
        again = voice.render(session, tts, settings, site, item)
    assert first.model_id == "eleven_v4_turbo"
    assert first.path.suffix == ".mp3" and first.path.stat().st_size > 2000
    assert first.duration_s > 1.0 and first.characters_billed > 0 and first.ttfa_ms > 0
    assert again.cache_hit and again.characters_billed == 0  # the cache works on real audio
