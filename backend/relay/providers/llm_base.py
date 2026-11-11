"""The LLM seam. The engine sees only these types, so it cannot tell mock from live."""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- structured outputs


class ScriptLine(BaseModel):
    language: Literal["en", "es", "fr"]
    urgency: int = Field(ge=1, le=3)
    text: str = Field(description="the script, with {placeholders} for every fact")


class ScriptBatch(BaseModel):
    scripts: list[ScriptLine]


class NoteExtraction(BaseModel):
    """Flat on purpose: every field optional, validated against the typed payload afterwards."""

    event_type: Literal["line_status", "passenger_page", "stand_change"] | None = None
    line: str | None = None
    status: str | None = None
    reason: str | None = None
    location: str | None = None
    delay_minutes: int | None = None
    passenger_name: str | None = None
    flight_number: str | None = None
    destination: str | None = None
    gate: str | None = None
    page_type: str | None = None
    route: str | None = None
    from_stand: str | None = None
    to_stand: str | None = None
    looks_like_instruction: bool = False


# ---------------------------------------------------------------- requests and results


@dataclass
class ScriptRequest:
    system: str  # rendered prompt file for the site's hub type
    user: str  # the event as placeholders + slots (+ rejection feedback on a retry)
    # Structured copy of what `user` says. The live provider ignores these; the mock uses them.
    event_type: str
    facts: dict[str, Any]
    slots: list[tuple[str, int]]  # (language, urgency)
    update: bool = False
    feedback: dict[str, list[str]] = field(default_factory=dict)  # "fr/3" -> violations


@dataclass
class ExtractRequest:
    system: str
    user: str  # the note, wrapped in <staff_note> tags
    note: str  # raw note, for the mock
    site: Any  # relay.sites.Site, for the mock's gazetteer lookups


@dataclass
class LLMResult:
    text: str  # JSON text; the caller validates it against its Pydantic model
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    stop_reason: str = "end_turn"


class LLMProvider(Protocol):
    mode: str

    def generate_scripts(self, req: ScriptRequest, model: str) -> LLMResult: ...

    def extract_event(self, req: ExtractRequest, model: str) -> LLMResult: ...
