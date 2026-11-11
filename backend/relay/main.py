"""FastAPI app. The lifespan creates the tables. The scheduler joins it in Phase 5."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from relay.api import events as events_api
from relay.api import playback as playback_api
from relay.api import scenarios as scenarios_api
from relay.api import ws as ws_api
from relay.config import Settings, get_settings, resolve_mode
from relay.context import Context
from relay.db import init_db, make_engine
from relay.providers import build_connectors


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = make_engine(settings.relay_db_url)
    ctx = Context(conns=build_connectors(settings), settings=settings, engine=db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(db)
        yield

    app = FastAPI(title="Relay", lifespan=lifespan)
    app.state.ctx = ctx
    app.include_router(events_api.build_router(ctx))
    app.include_router(playback_api.build_router(db, settings))
    app.include_router(scenarios_api.build_router(ctx))
    app.include_router(ws_api.build_router(ctx))

    @app.get("/health")
    def health() -> dict:
        """Which provider is live and which is mock. The console header shows this."""
        return {
            "status": "ok",
            "llm": ctx.conns.llm.mode,
            "tts": ctx.conns.tts.mode,
            "llm_requested": resolve_mode(settings.relay_llm_mode, settings.anthropic_api_key),
            "tts_requested": resolve_mode(settings.relay_tts_mode, settings.elevenlabs_api_key),
        }

    return app


app = create_app()
