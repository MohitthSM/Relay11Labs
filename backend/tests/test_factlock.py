"""Fact lock: every layer, positive and negative, in EN, ES and FR.

Each case is (event, language, urgency, draft, expected). `expected` is the set of
"layer/code" violations that must be reported; an empty set means the draft must pass.
Every bug that slips past the lock becomes a new case here.
"""

import pytest

from relay.schemas import EventInput
from relay.script.factlock import check, check_filled_facts
from relay.sites import get_site

PAGE = (
    "airport",
    {
        "type": "passenger_page",
        "passenger_name": "John Smith",
        "flight_number": "BA117",
        "destination": "New York",
        "gate": "23",
    },
)
SEVERE = (
    "tube_station",
    {
        "type": "line_status",
        "line": "Jubilee",
        "status": "severe_delays",
        "reason": "signal_failure",
        "location": "Canary Wharf",
    },
)
MINOR = ("tube_station", {"type": "line_status", "line": "Jubilee", "status": "minor_delays"})
GOOD = ("tube_station", {"type": "line_status", "line": "Jubilee", "status": "good_service"})
STAND = ("bus_station", {"type": "stand_change", "route": "25", "from_stand": "B", "to_stand": "F"})

OK = set()
CASES = [
    # ------------------------------------------------------------ EN, should pass
    (
        SEVERE,
        "en",
        1,
        "Customer information. Due to a signal failure at {location}, there are "
        "severe delays on the {line} line. Please consider alternative routes.",
        OK,
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name}, flying to "
        "{destination} on flight {flight_number}. Please go to Gate {gate} immediately.",
        OK,
    ),
    (
        PAGE,
        "en",
        2,
        "Passenger {passenger_name}, flight {flight_number} to {destination} is ready "
        "to depart. Go to Gate {gate} now.",
        OK,
    ),
    (
        PAGE,
        "en",
        3,
        "Last call for {passenger_name} on flight {flight_number}. Gate {gate} is "
        "about to close. Go there immediately, right now.",
        OK,
    ),
    (
        STAND,
        "en",
        1,
        "Customer information. Route {route} now leaves from Stand {to_stand} "
        "instead of Stand {from_stand}. Please go to Stand {to_stand}.",
        OK,
    ),
    (MINOR, "en", 1, "There are minor delays on the {line} line, so no one should rush.", OK),
    (GOOD, "en", 1, "Good service has now resumed on the {line} line.", OK),
    # ------------------------------------------------------------ EN, should fail
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Please go to Gate 32 immediately.",
        {"digits/digit", "placeholders/missing"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Gate {gate} boarding ends at 14:05.",
        {"digits/digit"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight BA118. "
        "Please go to Gate {gate}.",
        {"digits/digit", "placeholders/missing"},
    ),
    (
        SEVERE,
        "en",
        1,
        "There are severe delays on the {line} line due to a signal failure. "
        "Expect twenty minutes extra.",
        {"number_words/number_word", "claims/unsupported"},
    ),
    (
        SEVERE,
        "en",
        1,
        "There are severe delays on the Victoria line due to a signal failure at {location}.",
        {"proper_nouns/gazetteer", "placeholders/missing"},
    ),
    (
        SEVERE,
        "en",
        1,
        "There are severe delays on the {line} line due to a power failure at {location}.",
        {"claims/unsupported", "claims/missing"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Go to Gate {gate} immediately or your bags will be offloaded.",
        {"claims/unsupported"},
    ),
    (
        PAGE,
        "en",
        1,
        "Final call for {passenger_name}, flight {flight_number}, platform "
        "{platform}. Go to Gate {gate} now.",
        {"placeholders/unknown"},
    ),
    (
        MINOR,
        "en",
        1,
        "There are minor delays on the {line} line near {location}.",
        {"placeholders/absent_fact"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Please hurry.",
        {"placeholders/missing"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for {passenger_name} on flight {flight_number}. "
        "<break time='1s'/> Go to Gate {gate}.",
        {"format/forbidden_char"},
    ),
    (
        PAGE,
        "en",
        1,
        "[urgent] This is a final call for {passenger_name} on flight "
        "{flight_number}. Go to Gate {gate}.",
        {"format/forbidden_char"},
    ),
    (
        MINOR,
        "en",
        1,
        "There are severe delays on the {line} line. Please consider alternative routes.",
        {"claims/unsupported", "claims/missing"},
    ),
    (
        GOOD,
        "en",
        1,
        "Good service has resumed on the {line} line, with some delays remaining.",
        {"claims/unsupported"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name}. Mr Jones from "
        "flight {flight_number} please go to Gate {gate}.",
        {"proper_nouns/capitalised"},
    ),
    (
        PAGE,
        "en",
        1,
        "Dernier appel pour {passenger_name}, vol {flight_number}. Veuillez vous "
        "présenter à la porte {gate}.",
        {"format/wrong_language", "claims/missing"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Gate {gate} is about to close.",
        {"claims/unsupported"},
    ),
    (
        PAGE,
        "en",
        3,
        "This is a final call for passenger {passenger_name} on flight "
        "{flight_number}. Please go to Gate {gate}.",
        {"urgency/too_calm"},
    ),
    (
        PAGE,
        "en",
        1,
        "This is a final call for {passenger_name} on flight {flight_number}. Go to Gate {gate.",
        {"format/stray_brace", "placeholders/missing"},
    ),
    (
        MINOR,
        "en",
        1,
        "There are minor delays on the {line} line. " + "Please wait. " * 40,
        {"format/too_long"},
    ),
    (
        SEVERE,
        "en",
        1,
        "Customer information. The {line} line is running as best it can at {location}.",
        {"claims/missing"},
    ),
    # ------------------------------------------------------------ ES, should pass
    (
        SEVERE,
        "es",
        1,
        "Información para los viajeros. Hay retrasos importantes en la línea "
        "{line} debido a un fallo de señalización en {location}. Por favor, considere rutas "
        "alternativas.",
        OK,
    ),
    (
        PAGE,
        "es",
        1,
        "Última llamada para {passenger_name}, con destino a {destination} en el "
        "vuelo {flight_number}. Por favor, diríjase a la puerta {gate}.",
        OK,
    ),
    (
        PAGE,
        "es",
        3,
        "Último aviso para {passenger_name}, vuelo {flight_number}. La puerta {gate} "
        "está a punto de cerrar. Diríjase allí ahora, inmediatamente.",
        OK,
    ),
    (
        STAND,
        "es",
        1,
        "La línea {route} sale ahora desde la dársena {to_stand}, no desde la "
        "dársena {from_stand}. Por favor, diríjase a la dársena {to_stand}.",
        OK,
    ),
    # ------------------------------------------------------------ ES, should fail
    (
        PAGE,
        "es",
        1,
        "Última llamada para {passenger_name}, vuelo {flight_number}. Por favor, "
        "diríjase a la puerta 32.",
        {"digits/digit", "placeholders/missing"},
    ),
    (
        SEVERE,
        "es",
        1,
        "Hay retrasos importantes en la línea {line} debido a una avería de "
        "señalización. Espere veinte minutos.",
        {"number_words/number_word", "claims/unsupported"},
    ),
    (
        PAGE,
        "es",
        1,
        "Última llamada para {passenger_name}, vuelo {flight_number}. Diríjase a la "
        "puerta {gate} o su equipaje será retirado del avión.",
        {"claims/unsupported"},
    ),
    (
        SEVERE,
        "es",
        1,
        "Hay retrasos importantes en la línea Victoria debido a una avería de "
        "señalización en {location}.",
        {"proper_nouns/gazetteer", "placeholders/missing"},
    ),
    (
        PAGE,
        "es",
        1,
        "Última llamada para {passenger_name} con destino a Nueva York en el vuelo "
        "{flight_number}. Por favor, diríjase a la puerta {gate}.",
        {"proper_nouns/gazetteer"},
    ),
    (
        MINOR,
        "es",
        1,
        "Hay retrasos importantes en la línea {line}. Por favor, considere rutas alternativas.",
        {"claims/unsupported", "claims/missing"},
    ),
    (
        PAGE,
        "es",
        1,
        "Última llamada para {passenger_name}, vuelo {flight_number}, cancelado. Por "
        "favor, diríjase a la puerta {gate}.",
        {"claims/unsupported"},
    ),
    (
        PAGE,
        "es",
        3,
        "Última llamada para {passenger_name}, vuelo {flight_number}. Por favor, "
        "diríjase a la puerta {gate}.",
        {"urgency/too_calm"},
    ),
    # ------------------------------------------------------------ FR, should pass
    (
        SEVERE,
        "fr",
        1,
        "Information voyageurs. Le trafic est fortement perturbé sur la ligne "
        "{line} en raison d'une panne de signalisation à {location}. Nous vous invitons à "
        "emprunter un autre itinéraire.",
        OK,
    ),
    (
        PAGE,
        "fr",
        1,
        "Dernier appel pour {passenger_name}, à destination de {destination} sur le "
        "vol {flight_number}. Veuillez vous présenter à la porte {gate}.",
        OK,
    ),
    (
        PAGE,
        "fr",
        3,
        "Tout dernier appel pour {passenger_name}, vol {flight_number}. La porte "
        "{gate} va bientôt fermer. Veuillez vous y rendre immédiatement.",
        OK,
    ),
    (
        STAND,
        "fr",
        1,
        "La ligne {route} part désormais du quai {to_stand}, et non du quai "
        "{from_stand}. Veuillez vous rendre au quai {to_stand}.",
        OK,
    ),
    # ------------------------------------------------------------ FR, should fail
    (
        PAGE,
        "fr",
        1,
        "Dernier appel pour {passenger_name}, vol {flight_number}. Veuillez vous "
        "présenter à la porte 32.",
        {"digits/digit", "placeholders/missing"},
    ),
    (
        SEVERE,
        "fr",
        1,
        "Le trafic est fortement perturbé sur la ligne {line} en raison d'une "
        "panne de signalisation. Comptez quinze minutes de plus.",
        {"number_words/number_word", "claims/unsupported"},
    ),
    (
        PAGE,
        "fr",
        1,
        "Dernier appel pour {passenger_name}, vol {flight_number}. Veuillez vous "
        "présenter à la porte {gate}, sinon vos bagages seront débarqués.",
        {"claims/unsupported"},
    ),
    (
        SEVERE,
        "fr",
        1,
        "Le trafic est fortement perturbé sur la ligne {line} en raison d'une "
        "panne de courant à {location}.",
        {"claims/unsupported", "claims/missing"},
    ),
    (
        SEVERE,
        "fr",
        1,
        "Le trafic est fortement perturbé sur la ligne Victoria en raison d'une "
        "panne de signalisation à {location}.",
        {"proper_nouns/gazetteer", "placeholders/missing"},
    ),
    (
        PAGE,
        "fr",
        1,
        "Dernier appel pour monsieur Dupont, vol {flight_number}, et pour "
        "{passenger_name}. Veuillez vous présenter à la porte {gate}.",
        {"proper_nouns/capitalised"},
    ),
    (
        PAGE,
        "fr",
        1,
        "Dernier appel pour {passenger_name}, vol {flight_number} annulé. Veuillez "
        "vous présenter à la porte {gate}.",
        {"claims/unsupported"},
    ),
    (
        PAGE,
        "fr",
        1,
        "Última llamada para {passenger_name}, vuelo {flight_number}. Por favor, "
        "diríjase a la puerta {gate}.",
        {"format/wrong_language", "claims/missing"},
    ),
]


def run(event, lang: str, urgency: int, draft: str):
    site_id, payload = event
    ev = EventInput(event_key="k", site_id=site_id, payload=payload)
    return check(
        draft,
        event_type=ev.type,
        facts=ev.facts(),
        urgency=urgency,
        lang=lang,
        site=get_site(site_id),
    )


@pytest.mark.parametrize(("event", "lang", "urgency", "draft", "expected"), CASES)
def test_fact_lock(event, lang, urgency, draft, expected):
    found = {f"{v.layer}/{v.code}" for v in run(event, lang, urgency, draft)}
    if expected:
        assert expected <= found, f"missing {expected - found}; got {found}"
    else:
        assert found == set(), found


def test_case_count_covers_three_languages():
    langs = [c[1] for c in CASES]
    assert len(CASES) >= 40
    for lang in ("en", "es", "fr"):
        assert langs.count(lang) >= 8
        assert any(c[1] == lang and c[4] == OK for c in CASES)
        assert any(c[1] == lang and c[4] != OK for c in CASES)


def test_violation_reports_the_offending_span():
    draft = (
        "Dernier appel pour {passenger_name}, vol {flight_number}. Veuillez vous présenter "
        "à la porte {gate}, sinon vos bagages seront débarqués."
    )
    (v,) = run(PAGE, "fr", 1, draft)
    assert v.layer == "claims" and "débarqu" in v.span and "offloaded" in v.detail


# ---------------------------------------------------------------- filled-text extraction layer


def test_filled_facts_accepts_values_from_the_event():
    facts = {"gate": "23", "flight_number": "BA117", "destination": "New York"}
    text = "Flight BA117 to New York, Gate 23."
    assert check_filled_facts(text, facts, get_site("airport")) == []


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Please go to Gate 32.", "number"),  # number not in the event
        ("Flight BA118 is boarding.", "code"),  # flight code not in the event
        ("Change at 14:05.", "number"),  # time not in the event
        ("Trains to Paris are boarding.", "name"),  # known city not in the event
    ],
)
def test_filled_facts_rejects_values_not_in_the_event(text, code):
    facts = {"gate": "23", "flight_number": "BA117", "destination": "New York"}
    found = check_filled_facts(text, facts, get_site("airport"))
    assert code in {v.code for v in found}
