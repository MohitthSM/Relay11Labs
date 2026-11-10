"""Mock TTS: a WAV of quiet silence, about 60 ms per character, stdlib only."""

import io
import wave

from relay.providers.errors import FailureQueue
from relay.providers.tts_base import RenderRequest, TTSResult

RATE = 8000
SECONDS_PER_CHAR = 0.06


class MockTTSProvider:
    mode = "mock"

    def __init__(self) -> None:
        self.calls: list[RenderRequest] = []
        self.failures = FailureQueue()

    def synthesize(self, req: RenderRequest) -> TTSResult:
        self.failures.maybe_raise()
        self.calls.append(req)
        duration = len(req.text) * SECONDS_PER_CHAR
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"\x00\x00" * int(RATE * duration))
        return TTSResult(buf.getvalue(), "audio/wav", len(req.text), duration)
