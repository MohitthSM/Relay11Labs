"""Mock TTS: a WAV of quiet silence, about 60 ms per character, stdlib only.

Works with no key, no network and no ffmpeg. Records every call so tests can assert on what the
voice layer sent (voice, model, language, pronunciation locators).
"""

import io
import wave

from relay.pronunciation import PronunciationRule
from relay.providers.errors import FailureQueue
from relay.providers.tts_base import (
    DictionarySync,
    RenderRequest,
    TTSResult,
    VoiceInfo,
)

RATE = 8000
SECONDS_PER_CHAR = 0.06


def silent_wav(duration_s: float, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * duration_s))
    return buf.getvalue()


class MockTTSProvider:
    mode = "mock"

    def __init__(self) -> None:
        self.calls: list[RenderRequest] = []
        self.failures = FailureQueue()
        self.dictionaries: dict[str, tuple[int, list[PronunciationRule]]] = {}  # id -> (ver, rules)

    def synthesize(self, req: RenderRequest) -> TTSResult:
        self.failures.maybe_raise()
        self.calls.append(req)
        duration = len(req.text) * SECONDS_PER_CHAR
        return TTSResult(silent_wav(duration), "audio/wav", len(req.text), duration)

    def sync_dictionary(
        self, name: str, rules: list[PronunciationRule], existing_id: str | None
    ) -> DictionarySync:
        """In-memory stand-in with the same create / update / unchanged behaviour."""
        if existing_id is None or existing_id not in self.dictionaries:
            dictionary_id = f"mock-dict-{len(self.dictionaries) + 1}"
            self.dictionaries[dictionary_id] = (1, list(rules))
            return DictionarySync("created", dictionary_id, "v1", added=[r.grapheme for r in rules])
        version, current = self.dictionaries[existing_id]
        wanted = {r.grapheme: r for r in rules}
        have = {r.grapheme: r for r in current}
        added = [g for g, r in wanted.items() if have.get(g) != r]
        removed = [g for g in have if g not in wanted]
        if not added and not removed:
            return DictionarySync("unchanged", existing_id, f"v{version}")
        self.dictionaries[existing_id] = (version + 1, list(rules))
        return DictionarySync("updated", existing_id, f"v{version + 1}", added, removed)

    def list_voices(self) -> list[VoiceInfo]:
        """Two made-up voices, enough to exercise scripts/pick_voice.py without a key."""
        return [
            VoiceInfo("mock-announcer", "Mock Announcer", "premade",
                      {"accent": "british", "use_case": "informative_educational"},
                      "Clear, calm announcer", ("en", "es", "fr")),
            VoiceInfo("mock-character", "Mock Character", "premade",
                      {"accent": "american", "use_case": "characters_animation"},
                      "Playful", ("en",), legacy=True),
        ]  # fmt: skip
