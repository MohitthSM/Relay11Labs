"""Voice layer in mock mode: routing, cache, segments, stitching, metrics. No keys, no network."""

import io
import logging
import shutil
import threading
import time
import wave
from datetime import UTC, datetime

import pytest
from sqlmodel import select

from relay import voice
from relay.config import Settings
from relay.models import AudioClip, VoiceRender
from relay.pronunciation import PronunciationLock
from relay.providers.errors import ProviderError
from relay.providers.tts_base import TTSResult, audio_duration
from relay.providers.tts_mock import SECONDS_PER_CHAR, MockTTSProvider
from relay.schemas import EventInput
from relay.script import templates
from relay.script.engine import build_context, generate
from relay.sites import Segments, get_site
from relay.voice import Item

FACTS = {
    "line": "Jubilee",
    "status": "severe_delays",
    "reason": "signal_failure",
    "location": "Canary Wharf",
}
OPENING = "Customer information."
CLOSING = "Please consider alternative routes. We apologise for the disruption."


@pytest.fixture
def vsettings(tmp_path) -> Settings:
    return Settings(audio_dir=str(tmp_path / "audio"))


@pytest.fixture
def tts() -> MockTTSProvider:
    return MockTTSProvider()


@pytest.fixture
def tube():
    return get_site("tube_station")


def jubilee(status="severe_delays", *, update=False) -> str:
    facts = {**FACTS, "status": status}
    return templates.render("line_status", facts, 1, "en", update=update)


def wav_seconds(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


# --- routing -----------------------------------------------------------------------------


def test_routing_table_defaults(vsettings):
    assert voice.route_model("live", vsettings) == "eleven_v4_turbo"  # lowest latency
    assert voice.route_model("scheduled", vsettings) == "eleven_v4"  # highest quality


def test_routing_table_is_config(tts):
    s = Settings(elevenlabs_model_live="eleven_flash_v2_5", elevenlabs_model_scheduled="eleven_v3")
    assert voice.route_model("live", s) == "eleven_flash_v2_5"
    assert voice.route_model("scheduled", s) == "eleven_v3"


def test_first_play_and_resolution_are_live_escalation_is_scheduled():
    assert voice.render_kind(1) == "live"
    assert voice.render_kind(2) == voice.render_kind(3) == "scheduled"
    assert voice.render_kind(3, is_resolution=True) == "live"


def test_each_announcement_logs_the_model_it_used(session, tts, vsettings, tube, caplog):
    items = [
        Item(jubilee(), "en", kind="live", urgency=1, event_id=7),
        Item(jubilee("minor_delays"), "en", kind="scheduled", urgency=2, event_id=7),
    ]
    with caplog.at_level(logging.INFO, logger="relay.voice"):
        voice.render_many(session, tts, vsettings, tube, items)
    rows = session.exec(select(VoiceRender).order_by(VoiceRender.urgency)).all()
    assert [(r.kind, r.model_id) for r in rows] == [
        ("live", "eleven_v4_turbo"),
        ("scheduled", "eleven_v4"),
    ]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "model=eleven_v4_turbo" in logged and "model=eleven_v4 " in logged
    assert {c.model_id for c in tts.calls} == {"eleven_v4_turbo", "eleven_v4"}


# --- cache -------------------------------------------------------------------------------


def test_cache_hit_makes_no_provider_call(session, tts, vsettings, tube):
    item = Item(jubilee(), "en", event_id=1)
    first = voice.render(session, tts, vsettings, tube, item)
    calls = len(tts.calls)
    again = voice.render(session, tts, vsettings, tube, item)
    assert len(tts.calls) == calls  # zero characters billed the second time
    assert again.clip_id == first.clip_id and again.cache_hit
    assert again.characters_billed == 0 and again.characters_saved == again.characters_total
    assert not first.cache_hit and first.characters_billed > 0


def test_cache_key_changes_with_each_input(tube, vsettings):
    def req(**changes):
        base = voice.build_request(
            tube, vsettings, text="Hello.", language="en", model_id="eleven_v4_turbo"
        )
        from dataclasses import replace

        return replace(base, **changes)

    from relay.providers.tts_base import PronunciationLocator, VoiceSettings

    base = voice.cache_key("mock", req())
    assert base == voice.cache_key("mock", req())  # stable
    variants = [
        voice.cache_key("elevenlabs", req()),
        voice.cache_key("mock", req(text="Hello!")),
        voice.cache_key("mock", req(voice_id="other")),
        voice.cache_key("mock", req(model_id="eleven_v4")),
        voice.cache_key("mock", req(language="fr")),
        voice.cache_key("mock", req(voice_settings=VoiceSettings(stability=0.5))),
        voice.cache_key("mock", req(output_format="mp3_44100_64")),
        voice.cache_key("mock", req(pronunciation=(PronunciationLocator("d", "v2"),))),
        voice.cache_key("mock", req(previous_text="Before.")),
    ]
    assert len({base, *variants}) == len(variants) + 1


def test_model_change_is_a_cache_miss(session, tts, vsettings, tube):
    voice.render(session, tts, vsettings, tube, Item(jubilee(), "en", kind="live"))
    n = len(tts.calls)
    voice.render(session, tts, vsettings, tube, Item(jubilee(), "en", kind="scheduled"))
    assert len(tts.calls) > n


def test_purged_file_is_regenerated(session, tts, vsettings, tube):
    item = Item("A short test announcement.", "en")
    first = voice.render(session, tts, vsettings, tube, item)
    first.path.unlink()
    again = voice.render(session, tts, vsettings, tube, item)
    assert again.path.exists() and again.clip_id == first.clip_id and not again.cache_hit


def test_mock_wav_duration_matches_formula(tts):
    text = "x" * 50
    req = voice.build_request(
        get_site("tube_station"), Settings(), text=text, language="en", model_id="m"
    )
    result = tts.synthesize(req)
    assert result.duration_s == pytest.approx(50 * SECONDS_PER_CHAR)
    from relay.providers.tts_base import audio_duration

    assert audio_duration(result.audio) == pytest.approx(result.duration_s, abs=0.01)


def test_identical_items_in_one_batch_are_generated_once(session, tts, vsettings, tube):
    items = [Item(jubilee(), "en", event_id=i) for i in (1, 2)]
    a, b = voice.render_many(session, tts, vsettings, tube, items)
    texts = [c.text for c in tts.calls]
    assert len(texts) == len(set(texts))  # no segment generated twice
    assert a.clip_id == b.clip_id
    assert a.characters_billed > 0 and b.characters_billed == 0 and b.cache_hit


# --- segments ----------------------------------------------------------------------------


def test_split_segments():
    seg = Segments(
        openings=["This is an update. Customer information.", OPENING], closings=[CLOSING]
    )
    split = voice.split_segments
    assert split(f"{OPENING} Delays on Jubilee. {CLOSING}", seg) == [
        ("opening", OPENING),
        ("middle", "Delays on Jubilee."),
        ("closing", CLOSING),
    ]
    # the longest opening wins
    assert split(f"This is an update. {OPENING} Minor delays. {CLOSING}", seg)[0] == (
        "opening",
        "This is an update. Customer information.",
    )
    # no fixed parts: the whole text is the middle
    assert split("Good service has now resumed on the Jubilee line.", seg) == [
        ("middle", "Good service has now resumed on the Jubilee line.")
    ]
    assert split("anything", None) == [("middle", "anything")]
    # nothing dynamic left: keep it whole rather than voice nothing
    assert split(f"{OPENING} {CLOSING}", seg)[0][0] == "opening"  # middle is the closing text
    assert split(OPENING, seg) == [("middle", OPENING)]
    # a phrase must end on a sentence boundary, not match inside a word
    assert split("Customer informations are here.", seg) == [
        ("middle", "Customer informations are here.")
    ]


def test_only_the_dynamic_middle_is_billed_once_fixed_parts_are_cached(
    session, tts, vsettings, tube
):
    first = voice.render(session, tts, vsettings, tube, Item(jubilee("severe_delays"), "en"))
    assert [c.text for c in tts.calls].count(OPENING) == 1
    assert len(tts.calls) == 3  # opening, middle, closing
    tts.calls.clear()
    second = voice.render(session, tts, vsettings, tube, Item(jubilee("minor_delays"), "en"))
    assert [c.text for c in tts.calls] == [  # only the new middle is generated
        "There are minor delays on the Jubilee line due to a signal failure at Canary Wharf."
    ]
    assert second.characters_billed == len(tts.calls[0].text)
    assert second.segments_total == 3 and second.segments_cached == 2
    assert second.characters_saved == len(OPENING) + len(CLOSING)
    assert second.clip_id != first.clip_id


def test_fixed_segments_are_permanent_and_middle_is_not(session, tts, vsettings, tube):
    voice.render(session, tts, vsettings, tube, Item(jubilee(), "en"))
    clips = {c.kind: c for c in session.exec(select(AudioClip)).all() if c.kind == "announcement"}
    assert "announcement" in clips and not clips["announcement"].permanent
    segs = session.exec(select(AudioClip).where(AudioClip.kind == "segment")).all()
    assert sorted(c.permanent for c in segs) == [False, True, True]


def test_stitched_clip_is_the_parts_plus_gaps(session, tts, vsettings, tube):
    item = Item(jubilee(), "en")
    out = voice.render(session, tts, vsettings, tube, item)
    parts = sum(len(c.text) * SECONDS_PER_CHAR for c in tts.calls)
    gaps = 2 * voice.GAP_MS / 1000
    assert wav_seconds(out.path) == pytest.approx(parts + gaps, abs=0.05)
    assert out.duration_s == pytest.approx(wav_seconds(out.path), abs=0.05)


def test_chime_is_stitched_in_front_only_when_asked(session, tts, vsettings, tube):
    plain = voice.render(session, tts, vsettings, tube, Item(jubilee(), "en"))
    chimed = voice.render(session, tts, vsettings, tube, Item(jubilee(), "en", chime=True))
    assert chimed.clip_id != plain.clip_id
    assert chimed.characters_billed == 0  # the chime is a file, not TTS
    assert chimed.duration_s == pytest.approx(
        plain.duration_s + 1.2 + voice.CHIME_GAP_MS / 1000, abs=0.05
    )


def test_text_without_fixed_parts_is_one_segment_and_not_stitched(session, tts, vsettings):
    airport = get_site("airport")
    out = voice.render(session, tts, vsettings, airport, Item("Final call for a passenger.", "en"))
    assert out.segments_total == 1
    clip = session.get(AudioClip, out.clip_id)
    assert clip.kind == "segment" and clip.parts == []


def test_warm_fixed_segments_means_first_announcement_pays_only_for_the_middle(
    session, tts, vsettings, tube
):
    made = voice.warm_fixed_segments(session, tts, vsettings, tube)
    assert made == 2 * 3  # 2 routes x (2 openings + 1 closing)
    assert voice.warm_fixed_segments(session, tts, vsettings, tube) == 0  # idempotent
    tts.calls.clear()
    out = voice.render(session, tts, vsettings, tube, Item(jubilee(), "en"))
    assert len(tts.calls) == 1 and out.segments_cached == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_stitching_mp3_parts_with_the_wav_chime():
    """The live path: ElevenLabs returns MP3, the chime is WAV, the result is MP3 (needs ffmpeg)."""
    from pydub.generators import Sine

    def mp3(ms: int) -> bytes:
        buf = io.BytesIO()
        Sine(440).to_audio_segment(duration=ms).export(buf, format="mp3")
        return buf.getvalue()

    chime = (voice.STATIC_DIR / voice.CHIME_FILE).read_bytes()
    out = voice.stitch([(mp3(1000), "mp3"), (mp3(2000), "mp3")], chime=chime, ext="mp3")
    expected = 1.2 + voice.CHIME_GAP_MS / 1000 + 1.0 + voice.GAP_MS / 1000 + 2.0
    assert audio_duration(out) == pytest.approx(expected, abs=0.15)  # mp3 adds encoder padding
    assert out[:3] == b"ID3" or out[:2] == b"\xff\xfb" or out[:2] == b"\xff\xf3"


# --- pronunciation, voice, languages -----------------------------------------------------


def test_pronunciation_dictionary_is_sent_with_every_request(session, tts, vsettings, tube):
    locked = tube.model_copy(
        update={
            "pronunciation": PronunciationLock(dictionary_id="d1", version_id="v3", pls_sha256="x")
        }
    )
    voice.render(session, tts, vsettings, locked, Item(jubilee(), "en"))
    assert len(tts.calls) == 3  # opening, middle and closing all carry it
    for call in tts.calls:
        assert [(p.dictionary_id, p.version_id) for p in call.pronunciation] == [("d1", "v3")]


def test_new_dictionary_version_invalidates_the_cache(session, tts, vsettings, tube):
    def lock(v):
        return tube.model_copy(
            update={
                "pronunciation": PronunciationLock(dictionary_id="d", version_id=v, pls_sha256="")
            }
        )

    voice.render(session, tts, vsettings, lock("v1"), Item(jubilee(), "en"))
    n = len(tts.calls)
    voice.render(session, tts, vsettings, lock("v2"), Item(jubilee(), "en"))
    assert len(tts.calls) == 2 * n


def test_one_voice_per_hub_in_every_language(session, tts, vsettings):
    airport = get_site("airport")
    assert airport.voice_id
    ev = EventInput(
        event_key="page",
        site_id="airport",
        payload={
            "type": "passenger_page",
            "passenger_name": "John Smith",
            "flight_number": "BA117",
            "destination": "New York",
            "gate": "23",
        },
    )
    plan = generate(
        ev, build_context(airport, ev.type), voice_llm(), vsettings, now=datetime.now(UTC)
    )
    items = [
        Item(s.spoken_text, s.language, kind=voice.render_kind(s.urgency), urgency=s.urgency)
        for s in plan.scripts
    ]
    voice.render_many(session, tts, vsettings, airport, items)
    assert {c.voice_id for c in tts.calls} == {airport.voice_id}
    assert {c.language for c in tts.calls} == {"en", "es", "fr"}
    assert {c.seed for c in tts.calls} and all(0 <= c.seed < 2**32 for c in tts.calls)


def voice_llm():
    from relay.providers.llm_mock import MockLLMProvider

    return MockLLMProvider()


def test_scenario_b_renders_nine_clips_then_zero(session, tts, vsettings):
    airport = get_site("airport")
    ev = EventInput(
        event_key="page",
        site_id="airport",
        payload={
            "type": "passenger_page",
            "passenger_name": "John Smith",
            "flight_number": "BA117",
            "destination": "New York",
            "gate": "23",
        },
    )
    plan = generate(
        ev, build_context(airport, ev.type), voice_llm(), vsettings, now=datetime.now(UTC)
    )

    def items():
        first_language = plan.scripts[0].language
        return [
            Item(
                s.spoken_text,
                s.language,
                kind=voice.render_kind(s.urgency),
                chime=s.language == first_language,
                urgency=s.urgency,
                event_id=1,
            )
            for s in plan.scripts
        ]

    first = voice.render_many(session, tts, vsettings, airport, items())
    assert len(first) == 9 and len({r.clip_id for r in first}) == 9
    assert not any(r.cache_hit for r in first)
    generated = len(tts.calls)
    assert generated == 9  # no fixed phrases at the airport: 9 segments, 9 TTS calls

    again = voice.render_many(session, tts, vsettings, airport, items())
    assert len(tts.calls) == generated  # re-run renders 0 new clips
    assert all(r.cache_hit for r in again)
    s = voice.stats(session)
    assert s.renders == 18 and s.cache_hit_renders == 9 and s.hit_rate == 0.5
    assert s.characters_saved == s.characters_billed  # the rerun saved what the first run spent
    assert {m for m in s.by_model} == {"eleven_v4_turbo", "eleven_v4"}


def test_scenario_c_is_billed_once_for_seven_plays(session, tts, vsettings):
    bus = get_site("bus_station")
    text = templates.render(
        "stand_change", {"route": "25", "from_stand": "B", "to_stand": "F"}, 1, "en"
    )
    renders = [
        voice.render(session, tts, vsettings, bus, Item(text, "en", event_id=3)) for _ in range(7)
    ]
    assert len(tts.calls) == 2  # opening and middle, once
    assert [r.cache_hit for r in renders] == [False] + [True] * 6
    clip = session.get(AudioClip, renders[0].clip_id)
    assert clip.hits == 6
    s = voice.stats(session)
    assert s.renders == 7 and s.cache_hit_renders == 6


# --- metrics -----------------------------------------------------------------------------


class SlowTTS(MockTTSProvider):
    """Mock that takes 40 ms per call and reports a 15 ms time to first byte."""

    def __init__(self):
        super().__init__()
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def synthesize(self, req) -> TTSResult:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.04)
        with self.lock:
            self.active -= 1
        result = super().synthesize(req)
        result.ttfb_ms, result.total_ms = 15.0, 40.0
        return result


def test_per_announcement_metrics(session, vsettings, tube):
    tts = SlowTTS()
    cold = voice.render(session, tts, vsettings, tube, Item(jubilee(), "en", event_id=9))
    warm = voice.render(session, tts, vsettings, tube, Item(jubilee(), "en", event_id=9))
    assert cold.characters_billed == cold.characters_total > 0
    assert cold.cost_usd == pytest.approx(cold.characters_billed / 1000 * 0.04)  # v4_turbo price
    assert cold.ttfa_ms >= 15 and cold.total_ms >= 40 and cold.total_ms >= cold.ttfa_ms
    assert warm.ttfa_ms < cold.ttfa_ms and warm.cost_usd == 0
    row = session.exec(select(VoiceRender).where(VoiceRender.cache_hit == False)).one()  # noqa: E712
    assert row.characters_billed == cold.characters_billed
    assert row.characters_saved == 0 and row.cost_usd == cold.cost_usd
    assert row.ttfa_ms == pytest.approx(cold.ttfa_ms)


def test_stats_aggregate(session, tts, vsettings, tube):
    voice.render(session, tts, vsettings, tube, Item(jubilee(), "en"))
    voice.render(session, tts, vsettings, tube, Item(jubilee(), "en"))
    s = voice.stats(session)
    assert s.renders == 2 and s.hit_rate == 0.5
    one = len(jubilee())
    assert s.characters_billed + s.characters_saved == pytest.approx(2 * one, abs=8)
    assert s.cost_saved_usd == pytest.approx(s.characters_saved / 1000 * 0.04)
    assert s.by_model["eleven_v4_turbo"].renders == 2


def test_stats_empty(session):
    s = voice.stats(session)
    assert (s.renders, s.hit_rate, s.characters_billed) == (0, 0.0, 0)


# --- concurrency and failure ---------------------------------------------------------------


def test_renders_run_in_parallel_but_capped(session, vsettings, tube):
    tts = SlowTTS()
    airport = get_site("airport")
    items = [Item(f"Distinct announcement number {n} for you.", "en") for n in "abcdef"]
    voice.render_many(session, tts, vsettings, airport, items)
    assert tts.peak == vsettings.elevenlabs_max_concurrency == 2


def test_semaphore_caps_across_concurrent_batches():
    tts = SlowTTS()
    req = voice.build_request(
        get_site("airport"), Settings(), text="hello there", language="en", model_id="m"
    )
    threads = [threading.Thread(target=voice._synthesize, args=(tts, req, 2)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert tts.peak == 2


def test_retryable_failure_is_retried_once(session, tts, vsettings):
    tts.failures.fail_next(1, retryable=True)
    out = voice.render(session, tts, vsettings, get_site("airport"), Item("One short line.", "en"))
    assert out.clip_id and len(tts.calls) == 1  # first attempt raised before recording


def test_failure_of_one_announcement_does_not_stop_the_others(session, tts, vsettings):
    airport = get_site("airport")
    tts.failures.fail_next(2, retryable=False)  # first two calls fail, for good
    items = [Item(f"Announcement {n} text here.", "en") for n in "abc"]
    out = voice.render_many(session, tts, vsettings, airport, items)
    failed = [r for r in out if r.error]
    assert len(failed) == 2 and isinstance(failed[0].error, ProviderError)
    assert len([r for r in out if r.clip_id]) == 1
    assert session.exec(select(VoiceRender)).all().__len__() == 1  # failures are not recorded
    with pytest.raises(ProviderError):
        voice.render(session, tts_failing(), vsettings, airport, Item("Another text one.", "en"))


def tts_failing() -> MockTTSProvider:
    t = MockTTSProvider()
    t.failures.fail_next(5, retryable=False)
    return t


def test_unexpected_provider_exception_fails_one_clip_not_the_batch(session, vsettings):
    class Broken(MockTTSProvider):
        def synthesize(self, req):
            if "bad" in req.text:
                raise KeyError("an SDK change the provider does not map")
            return super().synthesize(req)

    items = [Item("A bad one here.", "en"), Item("A good one here.", "en")]
    out = voice.render_many(session, Broken(), vsettings, get_site("airport"), items)
    assert isinstance(out[0].error, ProviderError) and out[0].clip_id is None
    assert out[1].clip_id and out[1].error is None


def test_stitch_failure_comes_back_as_an_error(session, tts, vsettings, monkeypatch):
    def no_ffmpeg(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(voice, "stitch", no_ffmpeg)
    out = voice.render_many(session, tts, vsettings, get_site("airport"),
                            [Item("Chimed line here.", "en", chime=True)])  # fmt: skip
    assert out[0].error is not None and "stitch" in str(out[0].error)


def test_empty_text_is_refused(session, tts, vsettings, tube):
    with pytest.raises(ValueError):
        voice.render(session, tts, vsettings, tube, Item("  ", "en"))


# --- privacy -------------------------------------------------------------------------------


def test_no_passenger_name_in_rows_or_logs(session, tts, vsettings, caplog):
    airport = get_site("airport")
    name = "Zebediah Quillfeather"
    text = f"Final call for passenger {name}, flight B A 1 1 7. Please go to Gate 23."
    with caplog.at_level(logging.DEBUG):
        voice.render(session, tts, vsettings, airport, Item(text, "en", event_id=5))
    dump = " ".join(
        str(r.model_dump())
        for table in (VoiceRender, AudioClip)
        for r in session.exec(select(table)).all()
    )
    assert name not in dump and "Quillfeather" not in dump
    assert name not in " ".join(r.getMessage() for r in caplog.records)


# --- api -----------------------------------------------------------------------------------


def test_audio_endpoint_serves_clips(engine, tts, vsettings, session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from relay import scheduler
    from relay.api.playback import build_router
    from relay.context import Context
    from relay.providers import build_connectors

    out = voice.render(session, tts, vsettings, get_site("airport"), Item("Hello there all.", "en"))
    app = FastAPI()
    ctx = Context(conns=build_connectors(vsettings), settings=vsettings, engine=engine)
    app.include_router(build_router(ctx, scheduler.State()))
    client = TestClient(app)
    ok = client.get(f"/audio/{out.clip_id}")
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/wav"
    assert ok.content == out.path.read_bytes()
    assert client.get("/audio/" + "0" * 64).status_code == 404
    assert client.get("/audio/..%2F..%2Fetc%2Fpasswd").status_code == 404
