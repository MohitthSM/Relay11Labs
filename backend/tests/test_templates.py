"""Generated: a template exists for every slot, and every template passes its own fact lock."""

import itertools
import typing

import pytest

from relay.schemas import EventInput, LineStatus, Reason
from relay.script import templates
from relay.script.factlock import check, urgency_score
from relay.script.lexicons import LANGUAGES, get_lexicon
from relay.sites import get_site

STATUSES = typing.get_args(LineStatus)
REASONS = typing.get_args(Reason)


def line_events():
    for status, reason, location, delay in itertools.product(
        STATUSES, (None, *REASONS), (None, "Canary Wharf"), (None, 20)
    ):
        yield (
            "tube_station",
            {
                "type": "line_status",
                "line": "Jubilee",
                "status": status,
                "reason": reason,
                "location": location,
                "delay_minutes": delay,
            },
        )


def all_events():
    yield from line_events()
    yield (
        "airport",
        {
            "type": "passenger_page",
            "passenger_name": "Zoë O'Neill-Díaz",
            "flight_number": "IB3166",
            "destination": "Madrid",
            "gate": "B7",
        },
    )
    for reason in (None, *REASONS):
        yield (
            "bus_station",
            {
                "type": "stand_change",
                "route": "N25",
                "from_stand": "B",
                "to_stand": "F",
                "reason": reason,
            },
        )


SLOTS = [
    (site_id, payload, urgency, lang, update)
    for site_id, payload in all_events()
    for urgency in templates.URGENCY_LEVELS[payload["type"]]
    for lang in LANGUAGES
    for update in (False, True)
]


def test_every_slot_is_generated():
    assert len(SLOTS) > 1000


@pytest.mark.parametrize(("site_id", "payload", "urgency", "lang", "update"), SLOTS)
def test_template_passes_its_own_lock(site_id, payload, urgency, lang, update):
    ev = EventInput(event_key="k", site_id=site_id, payload=payload)
    draft = templates.draft(ev.type, ev.facts(), urgency, lang, update=update)
    violations = check(
        draft,
        event_type=ev.type,
        facts=ev.facts(),
        urgency=urgency,
        lang=lang,
        site=get_site(site_id),
    )
    assert violations == [], draft


@pytest.mark.parametrize("lang", LANGUAGES)
def test_page_templates_escalate(lang):
    scores = [urgency_score(templates.PAGE[lang][u], get_lexicon(lang)) for u in (1, 2, 3)]
    assert scores[0] < scores[1] < scores[2], scores


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_reason_and_status_has_wording(lang):
    assert set(templates.REASONS[lang]) == set(REASONS)
    assert set(templates.LINE[lang]["status"]) == set(STATUSES)
    assert set(templates.PAGE[lang]) == {1, 2, 3}


def test_claim_lexicons_have_the_same_keys_in_every_language():
    keys = {lang: set(get_lexicon(lang).claims) for lang in LANGUAGES}
    assert keys["en"] == keys["es"] == keys["fr"]
    assert set(REASONS) <= keys["en"] and set(STATUSES) <= keys["en"]


def test_render_fills_placeholders():
    facts = {"route": "25", "from_stand": "B", "to_stand": "F"}
    text = templates.render("stand_change", facts, 1, "en")
    assert text == (
        "Customer information. Route 25 is now departing from Stand F, not Stand B. "
        "Please go to Stand F."
    )
