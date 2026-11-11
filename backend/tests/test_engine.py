"""Script engine: routing, retry with feedback, template fallback, escalation, all scenarios."""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from relay.providers.errors import REFUSED, TIMEOUT, ProviderError
from relay.providers.llm_mock import MockLLMProvider
from relay.schemas import EventInput
from relay.script import templates
from relay.script.engine import build_context, generate, plan_slots
from relay.script.factlock import check, urgency_score
from relay.script.lexicons import get_lexicon
from relay.sites import get_site

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
PAGE = {
    "type": "passenger_page",
    "passenger_name": "John Smith",
    "flight_number": "BA117",
    "destination": "New York",
    "gate": "23",
}
STAND = {"type": "stand_change", "route": "25", "from_stand": "B", "to_stand": "F"}


def page_event() -> EventInput:
    return EventInput(event_key="page-BA117-john-smith", site_id="airport", payload=PAGE)


def run(event: EventInput, llm, settings, **ctx_kwargs):
    site = get_site(event.site_id)
    return generate(event, build_context(site, event.type, **ctx_kwargs), llm, settings, now=NOW)


def batch(*scripts: tuple[str, int, str]) -> str:
    return json.dumps(
        {"scripts": [{"language": lang, "urgency": u, "text": t} for lang, u, t in scripts]}
    )


def stand_event() -> EventInput:
    return EventInput(event_key="stand-25", site_id="bus_station", payload=STAND)


# ---------------------------------------------------------------- happy path and output shape


def test_clean_run_uses_default_model_once(settings):
    llm = MockLLMProvider()
    plan = run(page_event(), llm, settings)
    assert [(s.language, s.urgency) for s in plan.scripts] == [
        (lang, u) for u in (1, 2, 3) for lang in ("en", "es", "fr")
    ]
    assert all(s.source == "llm" and s.model == "claude-haiku-5-5" for s in plan.scripts)
    assert [(c.attempt, c.model, c.outcome) for c in plan.llm_calls] == [
        (1, "claude-haiku-5-5", "accepted")
    ]
    assert len(llm.calls) == 1  # one batched call for all 9 slots


def test_plan_carries_policy_expiry_and_facts(settings):
    plan = run(page_event(), MockLLMProvider(), settings)
    assert plan.priority == 70 and plan.repeat_interval_s == 120 and plan.max_plays == 3
    assert plan.escalate and plan.zones == ["gate-23", "concourse-b"]
    assert plan.expires_at == NOW + timedelta(seconds=360)
    assert plan.facts_used == [
        "destination",
        "flight_number",
        "gate",
        "page_type",
        "passenger_name",
    ]
    en1 = plan.script("en", 1)
    assert en1.text == templates.render("passenger_page", page_event().facts(), 1, "en")
    assert "B A 1 1 7" in en1.spoken_text and "{" not in en1.text
    assert plan.llm_calls[0].cost_usd > 0 and plan.llm_calls[0].prompt_version.startswith(
        "script_airport.v1+script_rules.v1"
    )


def test_line_status_without_end_has_no_expiry(settings):
    ev = EventInput(
        event_key="j",
        site_id="tube_station",
        payload={"type": "line_status", "line": "Jubilee", "status": "minor_delays"},
    )
    plan = run(ev, MockLLMProvider(), settings)
    assert plan.expires_at is None and plan.repeat_interval_s == 300
    assert [(s.language, s.urgency) for s in plan.scripts] == [("en", 1)]


# ---------------------------------------------------------------- fact lock -> retry -> template


def test_invented_fact_is_caught_and_retry_carries_the_error(settings):
    bad = "Route {route} now leaves from Stand {to_stand}, not Stand {from_stand}. Go to Stand 7."
    good = (
        "Route {route} now leaves from Stand {to_stand}, not Stand {from_stand}. "
        "Please go to Stand {to_stand}."
    )
    llm = MockLLMProvider([batch(("en", 1, bad)), batch(("en", 1, good))])
    plan = run(stand_event(), llm, settings)

    slot = plan.script("en")
    assert slot.source == "llm" and slot.model == "claude-sonnet-5-5"
    assert slot.text.endswith("Please go to Stand F.")
    assert [r.attempt for r in slot.rejections] == [1]
    assert "digits/digit" in str(slot.rejections[0].violations[0])
    assert [(c.attempt, c.model, c.outcome) for c in plan.llm_calls] == [
        (1, "claude-haiku-5-5", "rejected"),
        (2, "claude-sonnet-5-5", "accepted"),
    ]
    retry_prompt = llm.calls[1]["prompt"]
    assert "were rejected" in retry_prompt and "en/1: digits/digit" in retry_prompt


def test_hallucinating_mock_escalates_then_falls_back_to_template(settings):
    """Scenario B stage moment: FR/3 rejected twice with reasons, the template plays instead."""
    llm = MockLLMProvider(hallucinate=True)
    plan = run(page_event(), llm, settings)

    fr3 = plan.script("fr", 3)
    assert fr3.source == "template" and fr3.model is None
    assert fr3.text == templates.render("passenger_page", page_event().facts(), 3, "fr")
    assert [(r.attempt, r.model) for r in fr3.rejections] == [
        (1, "claude-haiku-5-5"),
        (2, "claude-sonnet-5-5"),
    ]
    assert all("offloaded" in str(v) for r in fr3.rejections for v in r.violations)
    others = [s for s in plan.scripts if (s.language, s.urgency) != ("fr", 3)]
    assert all(s.source == "llm" and not s.rejections for s in others)  # per slot, not per event
    assert [c.outcome for c in plan.llm_calls] == ["rejected", "rejected"]
    assert list(plan.llm_calls[1].violations) == ["fr/3"]  # only the failed slot was retried
    assert (
        "fr/3" in llm.calls[1]["prompt"]
        and "en/1" not in llm.calls[1]["prompt"].split("Write exactly")[1]
    )


def test_timeout_falls_straight_to_templates(settings):
    llm = MockLLMProvider([ProviderError("timeout", retryable=True, kind=TIMEOUT)])
    plan = run(page_event(), llm, settings)
    assert all(s.source == "template" for s in plan.scripts)
    assert [c.outcome for c in plan.llm_calls] == ["timeout"]  # no second wait (R10)


def test_refusal_goes_to_escalation_model(settings):
    llm = MockLLMProvider([ProviderError("refused", kind=REFUSED)])
    plan = run(stand_event(), llm, settings)
    assert [(c.model, c.outcome) for c in plan.llm_calls] == [
        ("claude-haiku-5-5", "refused"),
        ("claude-sonnet-5-5", "accepted"),
    ]
    assert plan.script("en").source == "llm"


def test_invalid_output_twice_uses_template(settings):
    llm = MockLLMProvider(["not json", '{"scripts": "nope"}'])
    plan = run(stand_event(), llm, settings)
    assert plan.script("en").source == "template"
    assert [c.outcome for c in plan.llm_calls] == ["invalid", "invalid"]


def test_missing_slot_is_retried_alone(settings):
    ev = EventInput(event_key="p", site_id="airport", payload=PAGE)
    drafts = {
        (lang, u): templates.draft("passenger_page", ev.facts(), u, lang)
        for u in (1, 2, 3)
        for lang in ("en", "es", "fr")
    }
    first = batch(*[(lang, u, t) for (lang, u), t in drafts.items() if (lang, u) != ("es", 2)])
    llm = MockLLMProvider([first])  # second call falls back to the mock's own scripts
    plan = run(ev, llm, settings)
    assert all(s.source == "llm" for s in plan.scripts)
    assert plan.script("es", 2).model == "claude-sonnet-5-5"
    assert plan.script("es", 2).rejections[0].violations[0].code == "missing_slot"


# ---------------------------------------------------------------- escalation and context


def test_repeat_three_sounds_more_urgent_than_repeat_one(settings):
    plan = run(page_event(), MockLLMProvider(), settings)
    for lang in ("en", "es", "fr"):
        lex = get_lexicon(lang)
        one, three = (urgency_score(plan.script(lang, u).text, lex) for u in (1, 3))
        assert three > one, lang
    assert (
        "last call" in plan.script("en", 3).text and "about to close" in plan.script("en", 3).text
    )
    assert "close" not in plan.script("en", 1).text


def test_calm_level_three_from_the_llm_is_rejected(settings):
    calm = "This is a final call for {passenger_name} on flight {flight_number}. Go to Gate {gate}."
    ev = page_event()
    site = get_site("airport")
    ctx = build_context(site, ev.type, repeat_count=2)
    ctx.languages = ["en"]
    llm = MockLLMProvider([batch(("en", 3, calm))])
    plan = generate(ev, ctx, llm, settings, now=NOW)
    (slot,) = plan.scripts
    assert slot.urgency == 3 and slot.source == "llm" and slot.model == "claude-sonnet-5-5"
    assert slot.rejections[0].violations[0].layer == "urgency"


def test_repeat_count_skips_levels_already_played():
    assert plan_slots("passenger_page", ["en", "fr"], 2, escalate=True) == [("en", 3), ("fr", 3)]
    assert plan_slots("passenger_page", ["en"], 9, escalate=True) == [("en", 3)]
    assert plan_slots("stand_change", ["en"], 4, escalate=False) == [("en", 1)]


def test_previous_announcements_mark_an_update_and_reach_the_prompt(settings):
    llm = MockLLMProvider()
    prev = ["Customer information. There are severe delays on the {line} line."]
    ev = EventInput(
        event_key="j",
        site_id="tube_station",
        version=2,
        payload={"type": "line_status", "line": "Jubilee", "status": "minor_delays"},
    )
    plan = run(ev, llm, settings, previous=prev)
    assert plan.script("en").text.startswith("This is an update.")
    assert "severe" not in plan.script("en").text  # the stale fact did not carry over
    assert "<previous_announcements>" in llm.calls[0]["prompt"]
    assert "updates an earlier announcement: yes" in llm.calls[0]["prompt"]


def test_stale_status_copied_from_previous_announcement_is_rejected(settings):
    stale = "Customer information. There are still severe delays on the {line} line."
    ev = EventInput(
        event_key="j",
        site_id="tube_station",
        version=2,
        payload={"type": "line_status", "line": "Jubilee", "status": "minor_delays"},
    )
    llm = MockLLMProvider([batch(("en", 1, stale)), batch(("en", 1, stale))])
    plan = run(ev, llm, settings, previous=[stale])
    assert plan.script("en").source == "template"
    assert "minor delays" in plan.script("en").text


# ---------------------------------------------------------------- prompts and PII


def test_prompt_per_hub_type_and_no_values_in_the_prompt(settings):
    llm = MockLLMProvider()
    run(page_event(), llm, settings)
    run(stand_event(), llm, settings)
    page_prompt, bus_prompt = llm.calls[0]["prompt"], llm.calls[1]["prompt"]
    assert "airport terminal" in page_prompt and "Level 3: last call" in page_prompt
    assert "bus station" in bus_prompt and "airport" not in bus_prompt
    assert "{passenger_name}" in page_prompt
    for value in ("John Smith", "BA117", "New York"):
        assert value not in page_prompt  # value facts are never sent; code fills them in


# ---------------------------------------------------------------- every scenario, mock mode


def scenario_events():
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text())
        for step in scenario["events"]:
            yield pytest.param(scenario["site_id"], step, id=f"{path.stem}@{step['at_s']}")


@pytest.mark.parametrize("hallucinate", [False, True])
@pytest.mark.parametrize(("site_id", "step"), list(scenario_events()))
def test_every_scenario_event_produces_valid_scripts(site_id, step, hallucinate, settings):
    ev = EventInput(event_key=step["event_key"], site_id=site_id, payload=step["payload"])
    site = get_site(site_id)
    plan = run(ev, MockLLMProvider(hallucinate=hallucinate), settings)
    assert {s.language for s in plan.scripts} == set(site.languages_for(ev.type))
    for s in plan.scripts:
        assert (
            check(
                s.draft,
                event_type=ev.type,
                facts=ev.facts(),
                urgency=s.urgency,
                lang=s.language,
                site=site,
            )
            == []
        )
        assert s.spoken_text and "{" not in s.text
    if hallucinate:
        assert plan.scripts[-1].source == "template" and plan.scripts[-1].rejections


def test_scenario_c_announcement_matches_spec(settings):
    plan = run(stand_event(), MockLLMProvider(), settings)
    assert plan.script("en").text == (
        "Customer information. Route 25 is now departing from Stand F, not Stand B. "
        "Please go to Stand F."
    )
    assert plan.expires_at == NOW + timedelta(minutes=20) and plan.repeat_interval_s == 180


# --- an announcement links to the LLM call that wrote it --------------------------------------


class _ServedAs(MockLLMProvider):
    """Reports a different model string than requested (as a server-side fallback would), and
    invents a fact on its first call only, so the last slot is written by attempt 2."""

    def generate_scripts(self, req, model):
        self.hallucinate = not self.calls
        result = super().generate_scripts(req, model)
        return dataclasses.replace(result, model=model + "-served")


def test_announcement_links_to_its_call_by_attempt_not_model_name(settings, engine):
    from sqlmodel import Session, select

    from relay import events as events_service
    from relay.context import Context
    from relay.models import Announcement, LLMCall
    from relay.providers import Connectors
    from relay.providers.tts_mock import MockTTSProvider

    same = settings.model_copy(update={"script_model_escalation": settings.script_model_default})
    ctx = Context(Connectors(llm=_ServedAs(), tts=MockTTSProvider()), same, engine)
    page = {"type": "passenger_page", "passenger_name": "John Smith", "flight_number": "BA117",
            "destination": "New York", "gate": "23", "page_type": "final_call"}  # fmt: skip
    with Session(engine) as s:
        event = events_service.create(s, ctx, site_id="airport", payload=page)
        calls = {c.id: c for c in s.exec(select(LLMCall).where(LLMCall.event_id == event.id))}
        anns = s.exec(select(Announcement).where(Announcement.event_id == event.id)).all()
    assert all(a.source == "llm" and a.llm_call_id in calls for a in anns)
    attempts = sorted(calls[a.llm_call_id].attempt for a in anns if a.llm_call_id)
    assert attempts == [1] * 8 + [2]  # the slot the first call got wrong came from attempt 2
