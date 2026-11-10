from dataclasses import dataclass
from typing import Protocol


@dataclass
class RenderRequest:
    text: str
    voice_id: str
    language: str
    model_id: str = "eleven_v4_turbo"


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    characters: int
    duration_s: float


class TTSProvider(Protocol):
    mode: str

    def synthesize(self, req: RenderRequest) -> TTSResult: ...
