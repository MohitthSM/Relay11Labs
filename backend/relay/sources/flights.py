"""Flights stay mocked (SPEC non-goal: no DCS/FIDS/AODB integration). This is the seam.

A FlightFeed answers "which departures does the airport know about right now". Nothing in Relay
announces from it automatically: a gate agent still pages a passenger by hand (scenario B). The
interface exists so that a real provider plugs in here without touching the rest of the code.

To add a real provider:
1. Write a class with the same two members as `FlightFeed` (`mode` and `departures()`).
2. Return `Departure` objects. Map the provider's gate/status values onto these fields and drop
   what does not validate: a flight number must match `schemas.FLIGHT_PATTERN`.
3. Return it from `build_flight_feed` when its API key is set (a missing key means mock, as for
   every other provider), and add the key to `.env.example`.
4. If it should drive announcements (a gate change, say), poll it from a source like `tfl.py`
   and call `events.create` / `update` / `resolve`, with the same stale-data rule: a failed poll
   changes nothing and announces nothing.
"""

from typing import Literal, Protocol

from pydantic import BaseModel

from relay.config import Settings


class Departure(BaseModel):
    flight_number: str
    destination: str
    gate: str
    status: Literal["scheduled", "boarding", "final_call", "departed"]


class FlightFeed(Protocol):
    mode: Literal["mock", "live"]

    def departures(self) -> list[Departure]: ...


class MockFlightFeed:
    """Fixed departures that match scenario B. Works with no key and no network."""

    mode: Literal["mock", "live"] = "mock"

    def departures(self) -> list[Departure]:
        return [
            Departure(flight_number="BA117", destination="New York", gate="23", status="boarding"),
            Departure(flight_number="IB3166", destination="Madrid", gate="14", status="scheduled"),
            Departure(flight_number="AF1081", destination="Paris", gate="9", status="boarding"),
        ]


class ExampleRealFlightFeed:
    """STUB: where a real provider plugs in. It is never built today (see `build_flight_feed`)."""

    mode: Literal["mock", "live"] = "live"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def departures(self) -> list[Departure]:
        raise NotImplementedError("no real flight provider is wired in yet (see the module doc)")


def build_flight_feed(settings: Settings) -> FlightFeed:
    """Always the mock today. A real provider would be returned here when its key is set."""
    return MockFlightFeed()
