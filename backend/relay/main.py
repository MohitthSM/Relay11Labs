"""FastAPI app. Lifespan creates tables and resumes runs that were mid-flight at shutdown."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session

from relay.api import events as events_api
from relay.api import playback as playback_api
from relay.api.workflows import build_router
from relay.config import Settings, get_settings, resolve_mode
from relay.db import init_db, make_engine
from relay.providers import build_connectors
from relay.workflow import engine as wf_engine
from relay.workflow.context import Context
from relay.workflow.definitions import WORKFLOWS


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = make_engine(settings.relay_db_url)
    ctx = Context(conns=build_connectors(settings), settings=settings, workflows=WORKFLOWS)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(db)
        with Session(db) as session:
            wf_engine.recover(session, ctx)
        yield

    app = FastAPI(title="Relay", lifespan=lifespan)
    app.state.ctx = ctx
    app.include_router(build_router(ctx, db))
    app.include_router(events_api.build_router(ctx))
    app.include_router(playback_api.build_router(db, settings))

    @app.get("/health")
    def health() -> dict:
        return {
            "llm": ctx.conns.llm.mode,
            "tts": ctx.conns.tts.mode,
            "chat": ctx.conns.chat.mode,
            "crm": ctx.conns.crm.mode,
            "llm_requested": resolve_mode(settings.relay_llm_mode, settings.anthropic_api_key),
        }

    return app


app = create_app()
