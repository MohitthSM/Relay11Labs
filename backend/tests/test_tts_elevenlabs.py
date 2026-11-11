"""ElevenLabsProvider with a stub SDK client: request shape, error mapping, dictionaries."""

import io
import wave
from types import SimpleNamespace

import httpx
import pytest
from elevenlabs.core.api_error import ApiError

from relay.pronunciation import PronunciationRule
from relay.providers import build_tts
from relay.providers.errors import TIMEOUT, ProviderError
from relay.providers.tts_base import PronunciationLocator, RenderRequest, VoiceSettings
from relay.providers.tts_elevenlabs import ElevenLabsProvider
from relay.providers.tts_mock import MockTTSProvider, silent_wav


class StubTTS:
    def __init__(self, chunks=None, error=None):
        self.kwargs = None
        self.chunks = chunks if chunks is not None else [silent_wav(0.5)]
        self.error = error

    def convert(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        yield from self.chunks


class StubDictionaries:
    def __init__(self, rules=()):
        self.calls: list[tuple] = []
        self.current = list(rules)
        self.rules = SimpleNamespace(add=self.add, remove=self.remove)

    def create_from_rules(self, *, name, rules):
        self.calls.append(("create", name, [r.string_to_replace for r in rules]))
        return SimpleNamespace(id="dict1", version_id="v1")

    def get(self, dictionary_id):
        return SimpleNamespace(latest_version_id="v1", rules=self.current)

    def add(self, dictionary_id, *, rules):
        self.calls.append(("add", [r.string_to_replace for r in rules]))
        return SimpleNamespace(id=dictionary_id, version_id="v2")

    def remove(self, dictionary_id, *, rule_strings):
        self.calls.append(("remove", list(rule_strings)))
        return SimpleNamespace(id=dictionary_id, version_id="v3")


def provider(stub_tts=None, dictionaries=None) -> ElevenLabsProvider:
    p = ElevenLabsProvider("test-key", timeout_s=5)
    p.client = SimpleNamespace(
        text_to_speech=stub_tts or StubTTS(),
        pronunciation_dictionaries=dictionaries or StubDictionaries(),
    )
    return p


REQ = RenderRequest(
    text="Mind the gap.", voice_id="voice123", language="fr", model_id="eleven_v4_turbo", seed=42
)


def test_request_sends_everything_explicitly_and_nothing_extra():
    stub = StubTTS()
    result = provider(stub).synthesize(REQ)
    assert stub.kwargs == {
        "voice_id": "voice123",
        "text": "Mind the gap.",
        "model_id": "eleven_v4_turbo",
        "language_code": "fr",
        "output_format": "mp3_44100_128",
        "seed": 42,
    }
    assert result.characters == len("Mind the gap.")  # what ElevenLabs bills
    assert result.mime == "audio/mpeg"  # from output_format
    assert result.duration_s == pytest.approx(0.5, abs=0.01)
    assert result.total_ms >= result.ttfb_ms >= 0


def test_pronunciation_settings_and_context_are_passed_when_set():
    stub = StubTTS()
    req = RenderRequest(
        **{
            **REQ.__dict__,
            "pronunciation": (PronunciationLocator("d1", "v9"),),
            "voice_settings": VoiceSettings(stability=0.4, speed=1.0),
            "previous_text": "Before.",
            "next_text": "After.",
        }
    )
    provider(stub).synthesize(req)
    locator = stub.kwargs["pronunciation_dictionary_locators"][0]
    assert (locator.pronunciation_dictionary_id, locator.version_id) == ("d1", "v9")
    settings = stub.kwargs["voice_settings"]
    assert (settings.stability, settings.speed, settings.style) == (0.4, 1.0, None)
    assert (stub.kwargs["previous_text"], stub.kwargs["next_text"]) == ("Before.", "After.")


def test_never_sends_ssml_or_break_tags_unless_text_has_them():
    stub = StubTTS()
    provider(stub).synthesize(REQ)
    assert "<" not in stub.kwargs["text"]


def test_wav_output_format_mime():
    stub = StubTTS()
    req = RenderRequest(**{**REQ.__dict__, "output_format": "wav_44100"})
    assert provider(stub).synthesize(req).mime == "audio/wav"


@pytest.mark.parametrize(
    "error, retryable, kind",
    [
        (ApiError(status_code=429), True, "error"),
        (ApiError(status_code=503), True, "error"),
        (ApiError(status_code=401), False, "error"),
        (ApiError(status_code=422), False, "error"),
        (httpx.ReadTimeout("slow"), True, TIMEOUT),
        (httpx.ConnectError("down"), True, "error"),
    ],
)
def test_sdk_errors_become_provider_errors(error, retryable, kind):
    with pytest.raises(ProviderError) as exc:
        provider(StubTTS(error=error)).synthesize(REQ)
    assert (exc.value.retryable, exc.value.kind) == (retryable, kind)


def test_empty_and_unreadable_audio_are_errors():
    with pytest.raises(ProviderError):
        provider(StubTTS(chunks=[])).synthesize(REQ)
    with pytest.raises(ProviderError):
        provider(StubTTS(chunks=[b"not audio at all"])).synthesize(REQ)


def test_factory_picks_live_only_with_a_key():
    from relay.config import Settings

    assert build_tts(Settings(elevenlabs_api_key="")).mode == "mock"
    assert build_tts(Settings(elevenlabs_api_key="k", relay_tts_mode="mock")).mode == "mock"
    live = build_tts(Settings(elevenlabs_api_key="k", relay_tts_mode="auto"))
    assert isinstance(live, ElevenLabsProvider)  # conftest forces mock mode, so say "auto"
    assert isinstance(build_tts(Settings()), MockTTSProvider)


# --- dictionaries --------------------------------------------------------------------------

RULES = [
    PronunciationRule("Marylebone", "ˈmɑːlɪbən"),
    PronunciationRule("Holborn", "ˈhəʊbən"),
]


def api_rule(r: PronunciationRule):
    return SimpleNamespace(
        type="phoneme", string_to_replace=r.grapheme, phoneme=r.phoneme, alphabet=r.alphabet
    )


def test_dictionary_is_created_the_first_time():
    d = StubDictionaries()
    result = provider(dictionaries=d).sync_dictionary("relay-x", RULES, None)
    assert (result.action, result.dictionary_id, result.version_id) == ("created", "dict1", "v1")
    assert d.calls == [("create", "relay-x", ["Marylebone", "Holborn"])]


def test_dictionary_update_adds_changes_and_removes_only_what_differs():
    old = [
        api_rule(RULES[0]),
        api_rule(PronunciationRule("Holborn", "old")),
        api_rule(PronunciationRule("Stale", "x")),
    ]
    d = StubDictionaries(old)
    result = provider(dictionaries=d).sync_dictionary("relay-x", RULES, "dict1")
    assert d.calls == [("add", ["Holborn"]), ("remove", ["Stale"])]
    assert result.action == "updated" and result.version_id == "v3"
    assert result.added == ["Holborn"] and result.removed == ["Stale"]


def test_dictionary_unchanged_makes_no_write_calls():
    d = StubDictionaries([api_rule(r) for r in RULES])
    result = provider(dictionaries=d).sync_dictionary("relay-x", RULES, "dict1")
    assert d.calls == [] and result.action == "unchanged" and result.version_id == "v1"


def test_wav_helper_is_valid():
    with wave.open(io.BytesIO(silent_wav(0.25))) as w:
        assert w.getnframes() == 2000


# --- voices (scripts/pick_voice.py) ------------------------------------------------------------


def _voice(voice_id, *, languages=(), legacy=False, **labels):
    return SimpleNamespace(
        voice_id=voice_id, name=voice_id.title(), category="premade", labels=labels,
        description="", is_legacy=legacy,
        verified_languages=[SimpleNamespace(language=lang) for lang in languages],
    )  # fmt: skip


def test_list_voices_reads_every_page():
    pages = {
        None: SimpleNamespace(voices=[_voice("a", languages=("en", "fr"))], has_more=True,
                              next_page_token="p2"),
        "p2": SimpleNamespace(voices=[_voice("b", legacy=True, accent="british")],
                              has_more=False, next_page_token=None),
    }  # fmt: skip
    seen = []

    def search(*, page_size, next_page_token):
        seen.append(next_page_token)
        return pages[next_page_token]

    p = provider()
    p.client.voices = SimpleNamespace(search=search)  # type: ignore[attr-defined]
    voices = p.list_voices()
    assert seen == [None, "p2"]
    assert [(v.voice_id, v.languages, v.legacy) for v in voices] == [
        ("a", ("en", "fr"), False),
        ("b", (), True),
    ]
    assert voices[1].labels == {"accent": "british"}


def test_list_voices_maps_errors():
    def search(**kwargs):
        raise ApiError(status_code=401, body="no key")

    p = provider()
    p.client.voices = SimpleNamespace(search=search)  # type: ignore[attr-defined]
    with pytest.raises(ProviderError):
        p.list_voices()
