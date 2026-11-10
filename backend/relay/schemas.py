"""Event payloads: one Pydantic model per event type, joined as a discriminated union on `type`.

This is the single definition of what a "fact" is. A payload field is a fact when it is declared
with `fact()`:
- `fact()` facts are VALUE facts. The LLM may only mention them through a `{field}` placeholder,
  and code fills the value in.
- `fact(claim=True)` facts are CLAIM facts (enums such as status or reason). They are said in
  words, and the claim lexicon checks that the words match the value.
The fact lock and the prompt both read this metadata, so the fact list has one source.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

EVENT_TYPES = ("line_status", "passenger_page", "stand_change")

LineStatus = Literal["good_service", "minor_delays", "severe_delays", "part_suspended", "suspended"]
Reason = Literal[
    "signal_failure",
    "train_fault",
    "customer_incident",
    "power_failure",
    "staff_shortage",
    "engineering_works",
    "roadworks",
    "operational_reasons",
]
PageType = Literal["final_call"]

NAME_PATTERN = (
    r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*( [A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]*){0,3}$"
)
FLIGHT_PATTERN = r"^([A-Z]{2}|[A-Z]\d|\d[A-Z])\d{1,4}$"
GATE_PATTERN = r"^[A-Z]?\d{1,3}[A-Z]?$"
ROUTE_PATTERN = r"^[A-Z]{0,2}\d{1,3}[A-Z]?$"
STAND_PATTERN = r"^[A-Z]{1,2}\d{0,2}$"
PLACE_PATTERN = r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’&\-. ]{0,59}$"


def fact(*, claim: bool = False, **kwargs: Any) -> Any:
    """A payload field that is a fact. `claim=True` means it is said in words, not a placeholder."""
    return Field(json_schema_extra={"fact": True, "claim": claim}, **kwargs)


class LinePayload(BaseModel):
    type: Literal["line_status"] = "line_status"
    line: str = fact(pattern=PLACE_PATTERN, description="line name")
    status: LineStatus = fact(claim=True)
    reason: Reason | None = fact(claim=True, default=None)
    location: str | None = fact(default=None, pattern=PLACE_PATTERN, description="station name")
    delay_minutes: int | None = fact(default=None, ge=1, le=180, description="expected delay")


class PassengerPagePayload(BaseModel):
    type: Literal["passenger_page"] = "passenger_page"
    passenger_name: str = fact(pattern=NAME_PATTERN, description="passenger's full name")
    flight_number: str = fact(pattern=FLIGHT_PATTERN, description="flight number")
    destination: str = fact(pattern=PLACE_PATTERN, description="destination city")
    gate: str = fact(pattern=GATE_PATTERN, description="gate number")
    page_type: PageType = fact(claim=True, default="final_call")


class StandChangePayload(BaseModel):
    type: Literal["stand_change"] = "stand_change"
    route: str = fact(pattern=ROUTE_PATTERN, description="bus route number")
    from_stand: str = fact(pattern=STAND_PATTERN, description="old stand letter")
    to_stand: str = fact(pattern=STAND_PATTERN, description="new stand letter")
    reason: Reason | None = fact(claim=True, default=None)


EventPayload = Annotated[
    LinePayload | PassengerPagePayload | StandChangePayload, Field(discriminator="type")
]
PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "line_status": LinePayload,
    "passenger_page": PassengerPagePayload,
    "stand_change": StandChangePayload,
}

# Facts derived in code, never by the LLM. Airline from the IATA prefix of the flight number.
AIRLINES = {"BA": "British Airways", "IB": "Iberia", "AF": "Air France", "VS": "Virgin Atlantic"}
DERIVED_FACTS = {"passenger_page": {"airline": "airline name, derived from the flight number"}}


class EventInput(BaseModel):
    """One version of an event, as the script engine sees it."""

    event_key: str
    site_id: str
    version: int = 1
    payload: EventPayload

    @property
    def type(self) -> str:
        return self.payload.type

    def facts(self) -> dict[str, Any]:
        """All facts present in this event: payload facts (not None) plus derived facts."""
        out = {
            name: getattr(self.payload, name)
            for name in fact_fields(self.type)
            if getattr(self.payload, name) is not None
        }
        if isinstance(self.payload, PassengerPagePayload):
            airline = AIRLINES.get(self.payload.flight_number[:2])
            if airline:
                out["airline"] = airline
        return out


def fact_fields(event_type: str, *, claim: bool | None = None) -> list[str]:
    """Names of the fact fields of an event type. claim=None: all; True/False: filter by kind."""
    names = []
    for name, info in PAYLOAD_MODELS[event_type].model_fields.items():
        extra = info.json_schema_extra
        if not isinstance(extra, dict) or not extra.get("fact"):
            continue
        if claim is None or bool(extra.get("claim")) == claim:
            names.append(name)
    return names


def placeholder_fields(event_type: str) -> dict[str, str]:
    """Placeholders the LLM may write for this type: value facts plus derived facts."""
    model = PAYLOAD_MODELS[event_type]
    out = {n: model.model_fields[n].description or n for n in fact_fields(event_type, claim=False)}
    return out | DERIVED_FACTS.get(event_type, {})
