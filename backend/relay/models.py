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
# Approval states
AUTO, PENDING, APPROVED = "auto", "pending", "approved"
# Playback job states
HELD, SCHEDULED, DONE, CANCELLED = "held", "scheduled", "done", "cancelled"
# Play (PlayLog) states
PLAYING, PLAYED, STOPPED = "playing", "played", "stopped"


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
    priority: int = 30  # rank of the policy's priority class (sites.PRIORITY)
    created_at: datetime = Field(default_factory=timeutil.now)
    resolved_at: datetime | None = None
    # Human in the loop (D20). auto: played without approval. pending: waiting for an operator.
    # approved: an operator approved it. approved_by is an operator name or "auto", never a
    # passenger's.
    approval: str = AUTO  # auto | pending | approved
    approval_reason: str | None = None  # why it waits: "staff note", "high severity: suspended"
    approved_by: str | None = None
    approved_at: datetime | None = None


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
    """ "Play this event's sequence in this zone on this cadence." One per (event version, zone).

    held: waiting for operator approval. scheduled: plays when next_at comes. done: ran out of
    plays or time. cancelled: the event was updated or resolved (its queued plays are dropped).
    """

    id: int | None = Field(default=None, primary_key=True)
    event_id: int = Field(foreign_key="event.id", index=True)
    zone_id: str = Field(index=True)
    languages: list[str] = Field(default_factory=list, sa_column=Column(JSON))  # play order
    is_resolution: bool = False  # plays the event's resolution announcements
    next_at: datetime
    interval_s: int  # scenario seconds; scaled when next_at is computed
    plays_done: int = 0
    max_plays: int | None = None
    duration_s: int | None = None  # ends_at = start + duration (set when the job is released)
    ends_at: datetime | None = None
    escalate: bool = False
    priority: int = 30
    state: str = Field(default=SCHEDULED, index=True)  # held | scheduled | done | cancelled
    created_at: datetime = Field(default_factory=timeutil.now)


class PlayLog(SQLModel, table=True):
    """The audit log: one row per play (one sequence of languages) in one zone.

    What played (announcement ids and their script provenance), where (zone), when (started,
    finished), which script version (event version, prompt version or template) and who approved
    it. Ids and versions only: no script text, no passenger names (R11).
    """

    id: int | None = Field(default=None, primary_key=True)
    job_id: int | None = Field(default=None, foreign_key="playbackjob.id", index=True)
    event_id: int | None = Field(default=None, foreign_key="event.id", index=True)
    event_version: int = 1
    zone_id: str = Field(index=True)
    urgency: int = 1
    is_resolution: bool = False
    priority: int = 30
    announcement_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    # one entry per announcement: {"announcement_id", "language", "source", "prompt_version"}
    scripts: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    approved_by: str | None = None
    approved_at: datetime | None = None
    played_at: datetime = Field(default_factory=timeutil.now)  # the first clip went on air
    finished_at: datetime | None = None  # the last clip ends (or the play was stopped)
    clips_played: int = 0
    interruptions: int = 0  # times a higher-priority play cut in between two clips
    status: str = PLAYING  # playing | played | stopped (event updated or resolved mid-sequence)
