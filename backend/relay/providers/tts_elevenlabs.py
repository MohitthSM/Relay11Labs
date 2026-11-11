"""ElevenLabs via the official `elevenlabs` SDK. The only module that imports it.

- `text_to_speech.convert` is a plain HTTP call that returns an iterator of byte chunks (D6). The
  first chunk gives the time to first audio; the last gives the total time.
- `model_id`, `language_code`, `output_format` and `seed` are always sent explicitly.
- No SSML and no `<break>`: v4 does not support them. The text is sent exactly as given.
- No retries here beyond what the caller does (D14). SDK errors become ProviderError, with
  `retryable` set for 429 and 5xx, and `kind="timeout"` for timeouts.
- Pronunciation dictionaries are managed here too (create, or bring an existing one in line),
  because outside this module nothing may import the SDK.
"""

import time
from collections.abc import Callable
from typing import Any

import elevenlabs
import httpx
from elevenlabs import (
    BodyAddAPronunciationDictionaryV1PronunciationDictionariesAddFromRulesPostRulesItem_Alias as CreateAlias,  # noqa: E501
)
from elevenlabs import (
    BodyAddAPronunciationDictionaryV1PronunciationDictionariesAddFromRulesPostRulesItem_Phoneme as CreatePhoneme,  # noqa: E501
)
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError
from elevenlabs.pronunciation_dictionaries.rules.types import (
    PronunciationDictionaryRule_Alias as AddAlias,
)
from elevenlabs.pronunciation_dictionaries.rules.types import (
    PronunciationDictionaryRule_Phoneme as AddPhoneme,
)

from relay.pronunciation import PronunciationRule
from relay.providers.errors import ERROR, TIMEOUT, ProviderError
from relay.providers.tts_base import (
    DictionarySync,
    RenderRequest,
    TTSResult,
    VoiceInfo,
    audio_duration,
)

MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg", "pcm": "audio/pcm"}


class ElevenLabsProvider:
    mode = "live"

    def __init__(self, api_key: str, *, timeout_s: float) -> None:
        self.client = ElevenLabs(api_key=api_key, timeout=timeout_s)

    def synthesize(self, req: RenderRequest) -> TTSResult:
        started = time.perf_counter()
        first_byte: float | None = None
        chunks: list[bytes] = []

        def run() -> None:
            nonlocal first_byte
            for chunk in self.client.text_to_speech.convert(**_convert_kwargs(req)):
                if chunk and first_byte is None:
                    first_byte = time.perf_counter()
                chunks.append(chunk)

        _call(run)
        done = time.perf_counter()
        audio = b"".join(chunks)
        if not audio:
            raise ProviderError("empty audio", kind=ERROR)
        try:
            duration = audio_duration(audio)
        except ValueError as exc:
            raise ProviderError("unreadable audio", kind=ERROR) from exc
        return TTSResult(
            audio=audio,
            mime=MIME.get(req.output_format.split("_")[0], "application/octet-stream"),
            characters=len(req.text),  # ElevenLabs bills the characters of `text`
            duration_s=duration,
            ttfb_ms=((first_byte or done) - started) * 1000,
            total_ms=(done - started) * 1000,
        )

    def sync_dictionary(
        self, name: str, rules: list[PronunciationRule], existing_id: str | None
    ) -> DictionarySync:
        api = self.client.pronunciation_dictionaries
        if existing_id is None:
            made = _call(lambda: api.create_from_rules(name=name, rules=_create_rules(rules)))
            return DictionarySync(
                "created", made.id, made.version_id, added=[r.grapheme for r in rules]
            )
        current = _call(lambda: api.get(existing_id))
        have = {r.string_to_replace: _from_api(r) for r in current.rules}
        wanted = {r.grapheme: r for r in rules}
        added = [g for g, r in wanted.items() if have.get(g) != r]
        removed = [g for g in have if g not in wanted]
        version_id = current.latest_version_id
        if added:  # add-rules replaces a rule with the same string_to_replace
            changed = [wanted[g] for g in added]
            res = _call(lambda: api.rules.add(existing_id, rules=_add_rules(changed)))
            version_id = res.version_id
        if removed:
            res = _call(lambda: api.rules.remove(existing_id, rule_strings=removed))
            version_id = res.version_id
        action = "updated" if added or removed else "unchanged"
        return DictionarySync(action, existing_id, version_id, added, removed)

    def list_voices(self) -> list[VoiceInfo]:
        """GET /v2/voices, every page. Read-only; costs no characters."""
        out: list[VoiceInfo] = []
        token: str | None = None
        while True:
            page = _call(
                lambda token=token: self.client.voices.search(page_size=100, next_page_token=token)
            )
            out += [_voice_info(v) for v in page.voices]
            if not page.has_more or not page.next_page_token:
                return out
            token = page.next_page_token


def _voice_info(v: Any) -> VoiceInfo:
    languages = {lang.language for lang in (v.verified_languages or []) if lang.language}
    return VoiceInfo(
        voice_id=v.voice_id,
        name=v.name or "",
        category=str(v.category or ""),
        labels={str(k): str(val) for k, val in (v.labels or {}).items()},
        description=v.description or "",
        languages=tuple(sorted(languages)),
        legacy=bool(v.is_legacy),
    )


def _convert_kwargs(req: RenderRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "voice_id": req.voice_id,
        "text": req.text,
        "model_id": req.model_id,
        "language_code": req.language,
        "output_format": req.output_format,
        "seed": req.seed,
    }
    settings = {k: v for k, v in vars(req.voice_settings).items() if v is not None}
    if settings:
        kwargs["voice_settings"] = elevenlabs.VoiceSettings(**settings)
    if req.pronunciation:
        kwargs["pronunciation_dictionary_locators"] = [
            elevenlabs.PronunciationDictionaryVersionLocator(
                pronunciation_dictionary_id=p.dictionary_id, version_id=p.version_id
            )
            for p in req.pronunciation
        ]
    if req.previous_text:
        kwargs["previous_text"] = req.previous_text
    if req.next_text:
        kwargs["next_text"] = req.next_text
    return kwargs


def _create_rules(rules: list[PronunciationRule]) -> list[Any]:
    return [_rule(r, CreatePhoneme, CreateAlias) for r in rules]


def _add_rules(rules: list[PronunciationRule]) -> list[Any]:
    return [_rule(r, AddPhoneme, AddAlias) for r in rules]  # the SDK uses a class set per endpoint


def _rule(r: PronunciationRule, phoneme_cls: type, alias_cls: type) -> Any:
    if r.alias is not None:
        return alias_cls(string_to_replace=r.grapheme, alias=r.alias)
    return phoneme_cls(string_to_replace=r.grapheme, phoneme=r.phoneme, alphabet=r.alphabet)


def _from_api(rule: Any) -> PronunciationRule:
    if rule.type == "alias":
        return PronunciationRule(rule.string_to_replace, alias=rule.alias)
    return PronunciationRule(rule.string_to_replace, rule.phoneme, rule.alphabet)


def _call(fn: Callable[[], Any]) -> Any:
    """Run one SDK call; turn SDK and transport errors into ProviderError."""
    try:
        return fn()
    except httpx.TimeoutException as exc:  # check before TransportError (its parent)
        raise ProviderError("timeout", retryable=True, kind=TIMEOUT) from exc
    except httpx.TransportError as exc:
        raise ProviderError("connection error", retryable=True) from exc
    except ApiError as exc:
        status = exc.status_code or 0
        raise ProviderError(f"HTTP {status}", retryable=status == 429 or status >= 500) from exc
