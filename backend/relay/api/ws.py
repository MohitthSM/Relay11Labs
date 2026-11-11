"""WebSocket /ws/events: pushes event state changes to the console (created, updated, resolved).

On connect the client gets a snapshot of the active events, then one small message per change
(ids and state only, no names; the client re-fetches details). Audio playback commands are a
separate stream (Phase 5, SSE, D8).
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
