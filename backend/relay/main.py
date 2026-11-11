"""FastAPI app. The lifespan creates the tables and runs the playback scheduler."""

import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay import scheduler
from relay.api import events as events_api
from relay.api import metrics as metrics_api
from relay.api import playback as playback_api
from relay.api import scenarios as scenarios_api
from relay.api import sites as sites_api
from relay.api import source as source_api
from relay.api import ws as ws_api
from relay.config import Settings, get_settings, resolve_mode
from relay.context import Context
from relay.db import init_db, make_engine
from relay.providers import build_connectors
from relay.providers.llm_mock import MockLLMProvider
from relay.sources import tfl
from relay.sources.flights import build_flight_feed


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = make_engine(settings.relay_db_url)
    ctx = Context(conns=build_connectors(settings), settings=settings, engine=db)
    playback = scheduler.State()
    feed = tfl.FeedState(mode=settings.relay_data_mode)
    flights = build_flight_feed(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(db)
        tasks = [
            asyncio.create_task(scheduler.run(ctx, playback)),
            asyncio.create_task(tfl.run(ctx, feed)),  # idle until the mode is live
        ]
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Relay", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.playback = playback
    app.state.feed = feed
    app.include_router(events_api.build_router(ctx))
    app.include_router(playback_api.build_router(ctx, playback))
    app.include_router(scenarios_api.build_router(ctx, feed))
    app.include_router(source_api.build_router(ctx, feed, flights))
    app.include_router(metrics_api.build_router(ctx))
    app.include_router(sites_api.build_router())
    app.include_router(ws_api.build_router(ctx))

    @app.get("/health")
    def health() -> dict:
        """Which provider is live and which is mock. The console header shows this."""
        llm = ctx.conns.llm
        return {
            "status": "ok",
            "llm": llm.mode,
            "tts": ctx.conns.tts.mode,
            "llm_requested": resolve_mode(settings.relay_llm_mode, settings.anthropic_api_key),
            "tts_requested": resolve_mode(settings.relay_tts_mode, settings.elevenlabs_api_key),
            # the on-stage fact-lock toggle exists only for the mock LLM (null when live)
            "hallucinate": llm.hallucinate if isinstance(llm, MockLLMProvider) else None,
        }

    return app


app = create_app()
