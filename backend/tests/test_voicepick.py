"""Ranking voices for PA use: every site language first, never a legacy voice."""

from relay.providers.tts_base import VoiceInfo
from relay.providers.tts_mock import MockTTSProvider
from relay.voicepick import pa_score, shortlist

ALL = ["en", "es", "fr"]


def v(voice_id, languages=(), legacy=False, description="", **labels) -> VoiceInfo:
    return VoiceInfo(voice_id, voice_id, "premade", labels, description, tuple(languages), legacy)


def test_languages_count_most():
    trilingual = v("tri", ALL)
    english_announcer = v("en", ["en"], use_case="news", accent="british")
    assert pa_score(trilingual, ALL) > pa_score(english_announcer, ALL)


def test_legacy_voices_sink():
    assert pa_score(v("old", ALL, legacy=True), ALL) < pa_score(v("new", ["en"]), ALL)


def test_character_voices_rank_below_announcers():
    announcer = v("a", ALL, description="Calm, clear announcer")
    character = v("c", ALL, use_case="characters_animation")
    assert [x.voice_id for x in shortlist([character, announcer], ALL)] == ["a", "c"]


def test_mock_voices_work_without_a_key():
    best = shortlist(MockTTSProvider().list_voices(), ALL, top=1)
    assert best[0].voice_id == "mock-announcer"
