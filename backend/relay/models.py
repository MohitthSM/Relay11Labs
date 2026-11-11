"""Relay tables. Phase 4 adds the two voice tables; the event tables arrive with Phase 2.

Neither table stores spoken text: scripts contain passenger names (CLAUDE.md rule 6).
"""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from relay import timeutil


class AudioClip(SQLModel, table=True):
    """A content-addressed cache entry: one audio file on disk.

    `id` is sha256 of everything that decides the sound (see voice.cache_key), so a repeat of
    the same text, voice, model, language and settings is the same row. `kind` is "segment" (one
    TTS call) or "announcement" (segments stitched together). Permanent clips (the fixed
    opening and closing phrases) are never purged.
    """

    __tablename__ = "audio_clip"

    id: str = Field(primary_key=True)
    path: str  # relative to the audio directory
    kind: str  # segment | announcement
    permanent: bool = False
    provider: str  # elevenlabs | mock
    model_id: str
    voice_id: str
    language: str
    characters: int  # characters billed when this clip was generated (0 for stitched clips)
    cost_usd: float
    duration_s: float
    parts: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # clip ids stitched
    created_at: datetime = Field(default_factory=timeutil.now)
    hits: int = 0  # times this clip was served from the cache


class VoiceRender(SQLModel, table=True):
    """One row per announcement voiced, cache hit or not. Feeds the metrics page (R7)."""

    __tablename__ = "voice_render"

    id: int | None = Field(default=None, primary_key=True)
    event_id: int | None = Field(default=None, index=True)  # ids only, never names
    announcement_id: int | None = None
    language: str
    urgency: int | None = None
    kind: str  # live | scheduled: the routing class
    model_id: str  # the model this announcement was routed to
    provider: str
    clip_id: str  # the announcement clip
    cache_hit: bool  # True when nothing had to be generated
    segments_total: int
    segments_cached: int
    characters_total: int  # characters in the whole spoken text
    characters_billed: int  # characters actually sent to TTS
    characters_saved: int  # characters_total - characters_billed
    cost_usd: float  # estimated, from the price table
    cost_saved_usd: float
    ttfa_ms: float  # render start to the first audio byte (0 for a pure cache read)
    total_ms: float  # render start to the finished clip on disk
    created_at: datetime = Field(default_factory=timeutil.now)
