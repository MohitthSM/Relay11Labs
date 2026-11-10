"""Deterministic wording per (event type, urgency, language). The fallback and the reference.

Templates write the same `{placeholders}` as the LLM, and `fill()` puts the facts in, so LLM and
template output have the same shape (D1). Every template passes the fact lock (tested).
Pure: no I/O.
"""

import re
from typing import Any

URGENCY_LEVELS = {"line_status": [1], "passenger_page": [1, 2, 3], "stand_change": [1]}

UPDATE_OPENER = {"en": "This is an update.", "es": "Actualización.", "fr": "Mise à jour."}
OPENER = {
    "en": "Customer information.",
    "es": "Información para los viajeros.",
    "fr": "Information voyageurs.",
}

REASONS = {
    "en": {
        "signal_failure": "due to a signal failure",
        "train_fault": "due to a faulty train",
        "customer_incident": "due to a customer incident",
        "power_failure": "due to a power failure",
        "staff_shortage": "due to a shortage of staff",
        "engineering_works": "due to engineering works",
        "roadworks": "due to roadworks",
        "operational_reasons": "for operational reasons",
    },
    "es": {
        "signal_failure": "debido a una avería de señalización",
        "train_fault": "debido a un tren averiado",
        "customer_incident": "debido a un incidente con un viajero",
        "power_failure": "debido a un corte de corriente",
        "staff_shortage": "debido a la falta de personal",
        "engineering_works": "debido a obras de mantenimiento",
        "roadworks": "debido a obras en la calzada",
        "operational_reasons": "por motivos operativos",
    },
    "fr": {
        "signal_failure": "en raison d'une panne de signalisation",
        "train_fault": "en raison d'un train en panne",
        "customer_incident": "en raison d'un incident voyageur",
        "power_failure": "en raison d'une panne de courant",
        "staff_shortage": "en raison d'un manque de personnel",
        "engineering_works": "en raison de travaux de maintenance",
        "roadworks": "en raison de travaux de voirie",
        "operational_reasons": "pour des raisons opérationnelles",
    },
}

LINE = {
    "en": {
        "status": {
            "good_service": "Good service has now resumed on the {line} line.",
            "minor_delays": "There are minor delays on the {line} line",
            "severe_delays": "There are severe delays on the {line} line",
            "part_suspended": "There is a part suspension on the {line} line",
            "suspended": "The {line} line is suspended",
        },
        "at": "at {location}",
        "around": "around {location}",
        "delay": "Journeys may take up to {delay_minutes} minutes longer.",
        "advice": "Please consider alternative routes. We apologise for the disruption.",
    },
    "es": {
        "status": {
            "good_service": "Se ha restablecido el servicio normal en la línea {line}.",
            "minor_delays": "Hay retrasos leves en la línea {line}",
            "severe_delays": "Hay retrasos importantes en la línea {line}",
            "part_suspended": "Hay una suspensión parcial del servicio en la línea {line}",
            "suspended": "El servicio está suspendido en la línea {line}",
        },
        "at": "en {location}",
        "around": "en la zona de {location}",
        "delay": "Los trayectos pueden durar hasta {delay_minutes} minutos más.",
        "advice": "Por favor, considere rutas alternativas. Disculpe las molestias.",
    },
    "fr": {
        "status": {
            "good_service": "Le service normal a repris sur la ligne {line}.",
            "minor_delays": "Le trafic connaît de légers retards sur la ligne {line}",
            "severe_delays": "Le trafic est fortement perturbé sur la ligne {line}",
            "part_suspended": "Le trafic est partiellement interrompu sur la ligne {line}",
            "suspended": "Le trafic est interrompu sur la ligne {line}",
        },
        "at": "à {location}",
        "around": "dans le secteur de {location}",
        "delay": "Les trajets peuvent être rallongés de {delay_minutes} minutes au maximum.",
        "advice": (
            "Nous vous invitons à emprunter un autre itinéraire. "
            "Veuillez nous excuser pour la gêne occasionnée."
        ),
    },
}

PAGE = {
    "en": {
        1: "This is a final call for passenger {passenger_name}, travelling to {destination} "
        "on flight {flight_number}. Please go to Gate {gate} immediately.",
        2: "Passenger {passenger_name}, travelling to {destination} on flight {flight_number}: "
        "your flight is ready to depart. Please make your way to Gate {gate} now.",
        3: "This is the last call for passenger {passenger_name} on flight {flight_number} "
        "to {destination}. Gate {gate} is about to close. Please go to Gate {gate} right now, "
        "immediately.",
    },
    "es": {
        1: "Última llamada para {passenger_name}, con destino a {destination} en el vuelo "
        "{flight_number}. Por favor, diríjase a la puerta {gate} inmediatamente.",
        2: "{passenger_name}, con destino a {destination} en el vuelo {flight_number}: su vuelo "
        "está listo para salir. Por favor, diríjase ahora a la puerta {gate}.",
        3: "Último aviso para {passenger_name}, del vuelo {flight_number} con destino a "
        "{destination}. La puerta {gate} está a punto de cerrar. Diríjase a la puerta {gate} "
        "ahora, inmediatamente.",
    },
    "fr": {
        1: "Dernier appel pour {passenger_name}, à destination de {destination} sur le vol "
        "{flight_number}. Veuillez vous présenter immédiatement à la porte {gate}.",
        2: "{passenger_name}, à destination de {destination} sur le vol {flight_number} : "
        "votre vol est prêt à partir. Veuillez vous rendre maintenant à la porte {gate}.",
        3: "Tout dernier appel pour {passenger_name}, vol {flight_number} à destination de "
        "{destination}. La porte {gate} va bientôt fermer. Veuillez vous y rendre "
        "immédiatement, maintenant.",
    },
}

STAND = {
    "en": "Route {route} is now departing from Stand {to_stand}, not Stand {from_stand}{reason}. "
    "Please go to Stand {to_stand}.",
    "es": "La línea {route} sale ahora desde la dársena {to_stand}, no desde la dársena "
    "{from_stand}{reason}. Por favor, diríjase a la dársena {to_stand}.",
    "fr": "La ligne {route} part désormais du quai {to_stand}, et non du quai {from_stand}"
    "{reason}. Veuillez vous rendre au quai {to_stand}.",
}


def draft(
    event_type: str, facts: dict[str, Any], urgency: int, lang: str, *, update: bool = False
) -> str:
    """The template text for one slot, with {placeholders} still in it."""
    if event_type == "line_status":
        text = _line(facts, lang)
    elif event_type == "passenger_page":
        text = PAGE[lang][urgency]
    elif event_type == "stand_change":
        reason = f", {REASONS[lang][facts['reason']]}" if facts.get("reason") else ""
        text = OPENER[lang] + " " + STAND[lang].replace("{reason}", reason)
    else:
        raise KeyError(f"no template for event type {event_type!r}")
    return f"{UPDATE_OPENER[lang]} {text}" if update else text


def _line(facts: dict[str, Any], lang: str) -> str:
    t = LINE[lang]
    status = facts["status"]
    if status == "good_service":
        return t["status"][status]
    sentence = t["status"][status]
    reason = facts.get("reason")
    if reason:
        sentence += " " + REASONS[lang][reason]
    if facts.get("location"):
        sentence += " " + (t["at"] if reason else t["around"])
    parts = [OPENER[lang], sentence + "."]
    if facts.get("delay_minutes"):
        parts.append(t["delay"])
    parts.append(t["advice"])
    return " ".join(parts)


PLACEHOLDER = re.compile(r"\{(\w+)\}")


def fill(text: str, facts: dict[str, Any]) -> str:
    """Replace each {name} with its fact value. Unknown names are left as they are."""
    return PLACEHOLDER.sub(lambda m: str(facts.get(m.group(1), m.group(0))), text)


def render(
    event_type: str, facts: dict[str, Any], urgency: int, lang: str, *, update: bool = False
) -> str:
    return fill(draft(event_type, facts, urgency, lang, update=update), facts)
