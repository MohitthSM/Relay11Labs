"""Data source: scripted scenarios (demo) or the real TfL feed (live).

GET /source        mode, feed status (off | waiting | ok | error | stale), a warning if the data
                   is not fresh, the lines being tracked, and the flight feed's provider
PUT /source        {"mode": "demo" | "live"}; switching to demo withdraws active TfL events
                   and answers how many ("withdrawn")
GET /flights       the departures the (mock) flight feed knows about
Changes are also pushed on the WebSocket as {"type": "source", ...} (D8).
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from relay import bus, timeutil
from relay.context import Context
from relay.sources import tfl
from relay.sources.flights import Departure, FlightFeed


class SourceBody(BaseModel):
    mode: Literal["demo", "live"]


def build_router(ctx: Context, feed: tfl.FeedState, flights: FlightFeed) -> APIRouter:
    router = APIRouter()

    def status() -> dict:
        return {**tfl.feed_status(feed, ctx.settings, timeutil.now()), "flights": flights.mode}

    @router.get("/source")
    def get_source() -> dict:
        return status()

    @router.put("/source")
    def put_source(body: SourceBody) -> dict:
        withdrawn = tfl.set_mode(ctx, feed, body.mode)
        out = {**status(), "withdrawn": withdrawn}
        bus.publish({"type": "source", **out})
        return out

    @router.get("/flights")
    def get_flights() -> list[Departure]:
        return flights.departures()

    return router
