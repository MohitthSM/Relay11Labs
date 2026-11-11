"""TTS provider interface and the plain data that crosses it. No vendor imports here."""

import io
from dataclasses import dataclass, field
from typing import Literal, Protocol

import mutagen

from relay.pronunciation import PronunciationRule


def audio_duration(audio: bytes) -> float:
    """Length in seconds of an MP3 or WAV, read from the bytes (mutagen)."""
    info = mutagen.File(io.BytesIO(audio))
    if info is None:
        raise ValueError("unrecognised audio format")
    return float(info.info.length)


@dataclass(frozen=True)
class PronunciationLocator:
    """One version of a pronunciation dictionary (ElevenLabs: id + version_id)."""

    dictionary_id: str
    version_id: str


@dataclass(frozen=True)
class VoiceSettings:
    """Per-site voice settings. None means "do not send": the voice's stored default applies."""

    stability: float | None = None
    similarity_boost: float | None = None
    style: float | None = None
    speed: float | None = None


@dataclass(frozen=True)
class RenderRequest:
    """Everything that decides what the audio sounds like. All of it goes into the cache key."""

    text: str
    voice_id: str
    language: str  # ISO 639-1
    model_id: str
    output_format: str = "mp3_44100_128"
    seed: int = 0
    voice_settings: VoiceSettings = field(default_factory=VoiceSettings)
    pronunciation: tuple[PronunciationLocator, ...] = ()
    previous_text: str | None = None  # neighbouring text, for continuity only (not billed)
    next_text: str | None = None


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    characters: int  # characters billed for this request
    duration_s: float
    ttfb_ms: float = 0.0  # request start to first audio byte
    total_ms: float = 0.0  # request start to last audio byte


@dataclass
class DictionarySync:
    action: Literal["created", "updated", "unchanged"]
    dictionary_id: str
    version_id: str
    added: list[str] = field(default_factory=list)  # graphemes added or changed
    removed: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VoiceInfo:
    """A voice the account can use, as the voices API describes it (scripts/pick_voice.py)."""

    voice_id: str
    name: str
    category: str  # premade | professional | generated | cloned | ...
    labels: dict[str, str] = field(default_factory=dict)  # accent, gender, use_case, ...
    description: str = ""
    languages: tuple[str, ...] = ()  # ISO 639-1 codes the voice is verified in
    legacy: bool = False  # being retired (the old default voices end on 2026-12-31)


class TTSProvider(Protocol):
    mode: str

    def synthesize(self, req: RenderRequest) -> TTSResult: ...

    def sync_dictionary(
        self, name: str, rules: list[PronunciationRule], existing_id: str | None
    ) -> DictionarySync:
        """Create the dictionary, or bring an existing one in line with `rules`."""
        ...

    def list_voices(self) -> list[VoiceInfo]:
        """Every voice this account can use with text_to_speech."""
        ...
