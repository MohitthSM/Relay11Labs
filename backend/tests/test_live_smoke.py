"""Live smoke test against Claude. Runs only with `-m live` and ANTHROPIC_API_KEY set."""

import os
from datetime import UTC, datetime

import pytest

from relay.config import Settings
from relay.providers import build_llm
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
