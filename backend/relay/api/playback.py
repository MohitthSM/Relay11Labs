"""GET /audio/{clip_id}: serve a cached clip. Phase 5 adds the SSE play stream to this module."""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.engine import Engine
from sqlmodel import Session

from relay.config import Settings
from relay.models import AudioClip
from relay.voice import clip_path

CLIP_ID = re.compile(r"^[0-9a-f]{64}$")  # a sha256: also rules out path tricks
MEDIA = {"mp3": "audio/mpeg", "wav": "audio/wav"}


def build_router(db: Engine, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/audio/{clip_id}")
    def audio(clip_id: str) -> FileResponse:
        if not CLIP_ID.match(clip_id):
            raise HTTPException(404, "no such clip")
        with Session(db) as session:
            clip = session.get(AudioClip, clip_id)
        if clip is None or not clip_path(settings, clip).exists():
            raise HTTPException(404, "no such clip")
        media = MEDIA.get(clip.path.rsplit(".", 1)[-1], "application/octet-stream")
        return FileResponse(clip_path(settings, clip), media_type=media)

    return router
