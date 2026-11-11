"""Relay tables. Sites and zones are config (sites/*.yaml, D12), so there is no Zone table.

Event and Announcement hold passenger names (shown in the console, purged later, R11). The voice
tables (AudioClip, VoiceRender) hold no text at all, and metrics read only those and LLMCall.
"""

from datetime import datetime
from typing import Any

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


# Event states
ACTIVE, SUPERSEDED, RESOLVED = "active", "superseded", "resolved"


class Event(SQLModel, table=True):
    """One row per VERSION of an event. The latest active row with an event_key is current."""

    id: int | None = Field(default=None, primary_key=True)
    event_key: str = Field(index=True)  # stable across versions; may hold a name: never log it
    site_id: str
    type: str  # line_status | passenger_page | stand_change
    version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # the facts
    source: str = "api"  # form | staff_note | tfl | replay | api
    staff_note: str | None = None
    state: str = Field(default=ACTIVE, index=True)  # active | superseded | resolved
    priority: int = 50
    created_at: datetime = Field(default_factory=timeutil.now)
    resolved_at: datetime | None = None


class LLMCall(SQLModel, table=True):
    """One row per LLM attempt. Feeds the metrics page (R7)."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: int | None = Field(default=None, foreign_key="event.id", index=True)
    purpose: str  # script | extract
    attempt: int
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    outcome: str  # accepted | rejected | timeout | error | refused | invalid
    error: str | None = None
    violations: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=timeutil.now)


class Announcement(SQLModel, table=True):
    """One per (event version, language, urgency level), plus resolution messages."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id", index=True)
    language: str
    urgency: int = 1
    is_resolution: bool = False
    draft: str  # as written, with {placeholders}
    script: str  # placeholders filled: what the fact lock approved
    spoken_text: str  # after normalize.py: what is sent to TTS
    source: str  # llm | template
    model: str | None = None  # model that wrote an accepted LLM script
    llm_call_id: int | None = Field(default=None, foreign_key="llmcall.id")
    rejections: list[Any] = Field(default_factory=list, sa_column=Column(JSON))  # fact-lock reasons
    clip_id: str | None = Field(default=None, foreign_key="audioclip.id")
    audio_error: str | None = None  # set when TTS failed: the text still shows in the console


class PlaybackJob(SQLModel, table=True):
    """ "Play this event's sequence in this zone on this cadence." Used from Phase 5."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id", index=True)
    zone_id: str
    next_at: datetime
    interval_s: int
    plays_done: int = 0
    max_plays: int | None = None
    ends_at: datetime | None = None
    escalate: bool = False
    priority: int = 50
    state: str = "scheduled"  # scheduled | done | cancelled


class PlayLog(SQLModel, table=True):
    """What actually played, where and when. Used from Phase 5."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="playbackjob.id")
    zone_id: str
    announcement_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    played_at: datetime = Field(default_factory=timeutil.now)
