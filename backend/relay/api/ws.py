"""WebSocket /ws/events: the one real-time channel (D8). Everything the bus publishes goes here.

On connect the client gets a snapshot of the active events, then one small message per change:
  event.created | event.updated | event.resolved | event.approved | event.withdrawn
                                                     (console state changes)
  play                                               (scheduler: one clip in one zone)
  source                                             (data source: demo/live, TfL feed status)
Ids, urls and state only, never names: the client re-fetches details (captions included) over HTTP.
Each client filters what it needs (the console listens to event.*, the zone players to play).
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

from relay import bus
from relay.context import Context
from relay.models import ACTIVE, Event


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter()

    def snapshot() -> list[dict]:
        with Session(ctx.engine) as session:
            rows = session.exec(select(Event).where(Event.state == ACTIVE)).all()
            return [
                {"event_id": e.id, "site_id": e.site_id, "event_type": e.type, "version": e.version}
                for e in rows
            ]

    @router.websocket("/ws/events")
    async def events_ws(ws: WebSocket) -> None:
        await ws.accept()
        queue = bus.subscribe()  # subscribe first, so nothing between snapshot and stream is lost
        try:
            await ws.send_json({"type": "snapshot", "active": await asyncio.to_thread(snapshot)})
            while True:
                await ws.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(queue)

    return router
