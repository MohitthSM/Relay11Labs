"""Voice layer: approved spoken text -> a cached audio clip, with metrics.

What happens to one announcement (`render_many`):
  1. Route: urgent/live clips use the lowest-latency model, scheduled and escalation clips the
     highest-quality one (ROUTES, set in .env). The model is part of the cache key.
  2. Split: fixed opening and closing phrases (site `segments`) are cut off the text. Only the
     dynamic middle is new each time; the fixed parts are voiced once and kept permanently.
  3. Cache: every piece is addressed by a hash of everything that decides the sound (cache_key).
     A hit costs zero characters. The stitched announcement is cached too, so a repeat is one
     lookup.
  4. Generate: missing segments go to the TTS provider in worker threads, at most
     ELEVENLABS_MAX_CONCURRENCY at a time. The same segment is never generated twice in a batch.
  5. Stitch: [chime] + opening + middle + closing are joined with short gaps (pydub).
  6. Record: one `VoiceRender` row per announcement (model, characters billed and saved, cost,
     time to first audio, total time). Never the text itself: scripts contain names.

Database work happens only in the calling thread; worker threads just call the provider.
This module is sync: FastAPI runs it in its threadpool, and async code uses `asyncio.to_thread`.
"""

import hashlib
import io
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydub import AudioSegment
from sqlalchemy import Integer, cast, func, select
from sqlmodel import Session, col

from relay.config import Settings
from relay.models import AudioClip, VoiceRender
from relay.providers.errors import ProviderError
from relay.providers.pricing import tts_cost
from relay.providers.tts_base import (
    PronunciationLocator,
    RenderRequest,
    TTSProvider,
    TTSResult,
    VoiceSettings,
    audio_duration,
)
from relay.retry import call_with_retry
from relay.sites import STATIC_DIR, Segments, Site

log = logging.getLogger(__name__)

RenderKind = Literal["live", "scheduled"]

# Model routing table: render kind -> the Settings field that holds the model id.
# live      = needed within seconds (first play, updates, resolution messages): lowest latency.
# scheduled = pre-rendered for a later play (escalation levels 2 and 3): highest quality.
ROUTES: dict[RenderKind, str] = {
    "live": "elevenlabs_model_live",
    "scheduled": "elevenlabs_model_scheduled",
}

GAP_MS = 150  # silence between stitched segments
CHIME_GAP_MS = 250  # silence after the chime
TTS_MAX_ATTEMPTS = 2  # one retry for retryable provider errors (429, 5xx, timeout)
TTS_RETRY_DELAY_S = 0.5
CHIME_FILE = "chime.wav"
EXT = {"audio/mpeg": "mp3", "audio/wav": "wav"}


# --- routing -------------------------------------------------------------------------------


def route_model(kind: RenderKind, settings: Settings) -> str:
    return getattr(settings, ROUTES[kind])


def render_kind(urgency: int, *, is_resolution: bool = False) -> RenderKind:
    """First-play and resolution clips are urgent. Escalation levels are rendered ahead of time."""
    return "scheduled" if urgency >= 2 and not is_resolution else "live"


# --- splitting text into fixed and dynamic parts --------------------------------------------


def split_segments(text: str, segments: Segments | None) -> list[tuple[str, str]]:
    """Cut a known opening and closing off `text`. Returns [(role, text), ...] in play order.

    Roles are "opening", "middle" and "closing". The phrases must match exactly and sit at the
    start or end on a sentence boundary. If nothing dynamic would be left, the text is kept whole.
    """
    if segments is None:
        return [("middle", text)]
    opening = _longest(segments.openings, lambda p: text.startswith(p + " "))
    rest = text[len(opening) :].lstrip() if opening else text
    closing = _longest(segments.closings, lambda p: rest.endswith(" " + p))
    if closing:
        rest = rest[: -len(closing)].rstrip()
    if not rest:
        return [("middle", text)]
    parts = [("opening", opening)] if opening else []
    parts.append(("middle", rest))
    if closing:
        parts.append(("closing", closing))
    return parts


def _longest(phrases: list[str], matches) -> str | None:
    return next((p for p in sorted(phrases, key=len, reverse=True) if matches(p)), None)


# --- cache keys and requests ----------------------------------------------------------------


def provider_name(tts: TTSProvider) -> str:
    return "elevenlabs" if tts.mode == "live" else "mock"


def cache_key(provider: str, req: RenderRequest) -> str:
    """sha256 of the provider and every field of the request: text, voice, model, language,
    output format, seed, voice settings, pronunciation dictionary version, neighbouring text.
    Change any one and the key changes, so a stale clip can never be served."""
    body = json.dumps({"provider": provider, **asdict(req)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def stable_seed(voice_id: str, language: str, text: str) -> int:
    """A fixed seed per (voice, language, text): same input, same best-effort sampling."""
    digest = hashlib.sha256(f"{voice_id}|{language}|{text}".encode()).digest()
    return int.from_bytes(digest[:4], "big")  # 0..4294967295, the range the API accepts


def build_request(
    site: Site,
    settings: Settings,
    *,
    text: str,
    language: str,
    model_id: str,
    previous_text: str | None = None,
    next_text: str | None = None,
) -> RenderRequest:
    """One voice per site, in every language. The site's pronunciation dictionary goes on every
    request, including the fixed segments."""
    pronunciation = ()
    if site.pronunciation:
        locator = PronunciationLocator(
            site.pronunciation.dictionary_id, site.pronunciation.version_id
        )
        pronunciation = (locator,)
    return RenderRequest(
        text=text,
        voice_id=site.voice_id,
        language=language,
        model_id=model_id,
        output_format=settings.elevenlabs_output_format,
        seed=stable_seed(site.voice_id, language, text),
        voice_settings=VoiceSettings(**site.voice_settings.model_dump()),
        pronunciation=pronunciation,
        previous_text=previous_text,
        next_text=next_text,
    )


# --- data in and out ------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One announcement to voice. `text` is the normalised spoken text."""

    text: str
    language: str
    kind: RenderKind = "live"
    chime: bool = False  # stitch the chime in front (the first language of a sequence)
    urgency: int | None = None
    event_id: int | None = None
    announcement_id: int | None = None


@dataclass
class Rendered:
    """What the caller gets back. `error` is set (and clip_id is None) if TTS failed."""

    clip_id: str | None
    path: Path | None
    duration_s: float
    kind: RenderKind
    model_id: str
    cache_hit: bool = False  # nothing had to be generated
    segments_total: int = 0
    segments_cached: int = 0
    characters_total: int = 0
    characters_billed: int = 0
    characters_saved: int = 0
    cost_usd: float = 0.0
    ttfa_ms: float = 0.0
    total_ms: float = 0.0
    error: ProviderError | None = None


@dataclass
class _Seg:
    role: str
    req: RenderRequest
    key: str
    permanent: bool  # fixed opening/closing: kept forever


@dataclass
class _Plan:
    item: Item
    model_id: str
    segs: list[_Seg]
    clip_key: str  # the announcement clip: a stitch, or the one segment when nothing is stitched
    stitched: bool
    chars_total: int
    waiting: set[str] = field(default_factory=set)  # segment keys still being generated
    paid: set[str] = field(default_factory=set)  # segment keys this announcement is billed for


@dataclass
class _Gen:
    result: TTSResult
    first_byte_at: float  # time.perf_counter() timestamps


# --- the audio directory and the clip table -------------------------------------------------


def audio_dir(settings: Settings) -> Path:
    path = Path(settings.audio_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def clip_path(settings: Settings, clip: AudioClip) -> Path:
    return audio_dir(settings) / clip.path


def _live_clip(session: Session, settings: Settings, key: str) -> AudioClip | None:
    """The clip row for `key`, but only if its file is still on disk."""
    clip = session.get(AudioClip, key)
    if clip is not None and not clip_path(settings, clip).exists():
        return None  # file was purged: treat as a miss and regenerate
    return clip


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)  # atomic: a reader never sees half a file


@lru_cache
def _chime(path: str, mtime: float) -> bytes:
    return Path(path).read_bytes()


def _chime_bytes() -> bytes | None:
    path = STATIC_DIR / CHIME_FILE
    if not path.exists():
        log.warning("chime file missing (%s): announcements play without it", path)
        return None
    return _chime(str(path), path.stat().st_mtime)


@lru_cache
def _semaphore(limit: int) -> threading.BoundedSemaphore:
    """Shared by every batch in the process, so concurrent events together stay under the cap."""
    return threading.BoundedSemaphore(limit)


# --- planning -------------------------------------------------------------------------------


def _plan(item: Item, provider: str, settings: Settings, site: Site) -> _Plan:
    if not item.text.strip():
        raise ValueError("cannot voice empty text")
    model_id = route_model(item.kind, settings)
    segments = site.segments.get(item.language) if settings.voice_split_segments else None
    parts = split_segments(item.text, segments)
    by_role = dict(parts)
    segs = []
    for role, text in parts:
        context = {}
        if role == "middle":  # continuity hints for the dynamic part only (not billed)
            context = {"previous_text": by_role.get("opening"), "next_text": by_role.get("closing")}
        req = build_request(
            site, settings, text=text, language=item.language, model_id=model_id, **context
        )
        segs.append(_Seg(role, req, cache_key(provider, req), permanent=role != "middle"))
    chime = item.chime and _chime_bytes() is not None
    stitched = chime or len(segs) > 1
    if stitched:
        recipe = {"chime": chime, "gap": GAP_MS, "segments": [s.key for s in segs]}
        if chime:
            recipe["chime_sha"] = hashlib.sha256(_chime_bytes() or b"").hexdigest()
        clip_key = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    else:
        clip_key = segs[0].key
    return _Plan(
        item, model_id, segs, clip_key, stitched, chars_total=sum(len(s.req.text) for s in segs)
    )


# --- generation (worker threads: no database access here) ------------------------------------


def _synthesize(tts: TTSProvider, req: RenderRequest, limit: int) -> _Gen:
    with _semaphore(limit):
        started = time.perf_counter()
        result, _ = call_with_retry(
            lambda: tts.synthesize(req),
            max_attempts=TTS_MAX_ATTEMPTS,
            base_delay_s=TTS_RETRY_DELAY_S,
        )
    return _Gen(result, first_byte_at=started + result.ttfb_ms / 1000)


def _store_segment(
    session: Session, settings: Settings, provider: str, seg: _Seg, gen: _Gen
) -> None:
    ext = EXT.get(gen.result.mime, "bin")
    name = f"{seg.key}.{ext}"
    _write(audio_dir(settings) / name, gen.result.audio)
    session.merge(  # merge: a stale row whose file was purged is replaced
        AudioClip(
            id=seg.key,
            path=name,
            kind="segment",
            permanent=seg.permanent,
            provider=provider,
            model_id=seg.req.model_id,
            voice_id=seg.req.voice_id,
            language=seg.req.language,
            characters=gen.result.characters,
            cost_usd=tts_cost(seg.req.model_id, gen.result.characters),
            duration_s=gen.result.duration_s,
        )
    )
    session.flush()


# --- stitching ------------------------------------------------------------------------------


def stitch(parts: list[tuple[bytes, str]], *, chime: bytes | None, ext: str) -> bytes:
    """Join audio parts (bytes, extension) in order: chime, gap, then each part with a gap.

    WAV needs no ffmpeg. MP3 in and out goes through ffmpeg, which pydub calls.
    """
    out = AudioSegment.empty()
    if chime is not None:
        out += AudioSegment.from_file(io.BytesIO(chime), format="wav")
        out += AudioSegment.silent(CHIME_GAP_MS, frame_rate=out.frame_rate)
    for i, (data, part_ext) in enumerate(parts):
        if i:
            out += AudioSegment.silent(GAP_MS, frame_rate=out.frame_rate)
        out += AudioSegment.from_file(io.BytesIO(data), format=part_ext)
    buf = io.BytesIO()
    out.export(buf, format=ext, **({"bitrate": "128k"} if ext == "mp3" else {}))
    return buf.getvalue()


def _build_announcement(
    session: Session, settings: Settings, provider: str, plan: _Plan
) -> AudioClip:
    clips = [session.get(AudioClip, s.key) for s in plan.segs]
    assert all(c is not None for c in clips)
    ext = clips[0].path.rsplit(".", 1)[1]  # type: ignore[union-attr]
    parts = [(clip_path(settings, c).read_bytes(), c.path.rsplit(".", 1)[1]) for c in clips if c]
    chime = _chime_bytes() if plan.item.chime else None
    data = stitch(parts, chime=chime, ext=ext)
    name = f"{plan.clip_key}.{ext}"
    _write(audio_dir(settings) / name, data)
    clip = AudioClip(
        id=plan.clip_key,
        path=name,
        kind="announcement",
        provider=provider,
        model_id=plan.model_id,
        voice_id=plan.segs[0].req.voice_id,
        language=plan.item.language,
        characters=0,  # the segments carry the billing
        cost_usd=0.0,
        duration_s=audio_duration(data),
        parts=[s.key for s in plan.segs],
    )
    clip = session.merge(clip)
    session.flush()
    return clip


# --- the main entry points ------------------------------------------------------------------


def render_many(
    session: Session, tts: TTSProvider, settings: Settings, site: Site, items: list[Item]
) -> list[Rendered]:
    """Voice a batch of announcements. Results are in the same order as `items`.

    A TTS failure for one announcement comes back as `Rendered.error`; the others still render.
    """
    t0 = time.perf_counter()
    provider = provider_name(tts)
    plans = [_plan(i, provider, settings, site) for i in items]
    results: list[Rendered | None] = [None] * len(plans)

    # 1. What is already cached? Decide who pays for each segment that is not.
    to_make: dict[str, _Seg] = {}
    for plan in plans:
        if _live_clip(session, settings, plan.clip_key):
            continue  # the finished announcement is cached
        for seg in plan.segs:
            plan.waiting.add(seg.key)
            if _live_clip(session, settings, seg.key):
                plan.waiting.discard(seg.key)
            elif seg.key not in to_make:
                to_make[seg.key] = seg
                plan.paid.add(seg.key)  # first announcement to need it pays; later ones reuse
    gens: dict[str, _Gen] = {}
    errors: dict[str, ProviderError] = {}

    def finish(index: int) -> None:
        plan = plans[index]
        failed = next((errors[s.key] for s in plan.segs if s.key in errors), None)
        results[index] = _finish(session, settings, provider, plan, gens, failed, t0)

    # 2. Announcements that need no generation are done straight away.
    for i, plan in enumerate(plans):
        if not plan.waiting:
            finish(i)

    # 3. Generate the missing segments. An announcement is finished as soon as its last segment
    #    arrives (or one of its segments fails), not when the whole batch is done.
    if to_make:
        limit = settings.elevenlabs_max_concurrency
        with ThreadPoolExecutor(max_workers=limit) as pool:
            futures = {pool.submit(_synthesize, tts, s.req, limit): s for s in to_make.values()}
            for future in as_completed(futures):
                seg = futures[future]
                try:
                    gens[seg.key] = future.result()
                    _store_segment(session, settings, provider, seg, gens[seg.key])
                except ProviderError as exc:
                    errors[seg.key] = exc
                for i, plan in enumerate(plans):
                    if results[i] is None and seg.key in plan.waiting:
                        plan.waiting.discard(seg.key)
                        if seg.key in errors or not plan.waiting:
                            finish(i)
    session.commit()
    assert all(r is not None for r in results)
    return [r for r in results if r is not None]


def render(
    session: Session, tts: TTSProvider, settings: Settings, site: Site, item: Item
) -> Rendered:
    """One announcement. Raises ProviderError if TTS failed."""
    result = render_many(session, tts, settings, site, [item])[0]
    if result.error:
        raise result.error
    return result


def _finish(
    session: Session,
    settings: Settings,
    provider: str,
    plan: _Plan,
    gens: dict[str, _Gen],
    failed: ProviderError | None,
    t0: float,
) -> Rendered:
    """Everything is generated (or failed): build the announcement clip and record the metrics."""
    item = plan.item
    out = Rendered(None, None, 0.0, item.kind, plan.model_id, characters_total=plan.chars_total)
    if failed is not None:
        log.warning(
            "voice failed event=%s ann=%s lang=%s model=%s: %s",
            item.event_id, item.announcement_id, item.language, plan.model_id, failed,
        )  # fmt: skip
        out.error = failed
        return out

    # Hit accounting: a clip served without being generated for this announcement gets hits += 1.
    served_whole = False
    if plan.stitched:
        clip = _live_clip(session, settings, plan.clip_key)
        served_whole = clip is not None  # cached before, or built by an identical item just now
        if clip is None:
            clip = _build_announcement(session, settings, provider, plan)
    else:
        clip = session.get(AudioClip, plan.clip_key)  # the single segment is the announcement
    assert clip is not None
    if served_whole:
        clip.hits += 1
        plan.paid.clear()  # nothing was generated for this announcement
    else:
        for seg in plan.segs:
            if seg.key not in plan.paid:
                seg_clip = session.get(AudioClip, seg.key)
                assert seg_clip is not None
                seg_clip.hits += 1
                session.add(seg_clip)
    session.add(clip)

    billed = sum(gens[k].result.characters for k in plan.paid if k in gens)
    saved = plan.chars_total - billed
    now = time.perf_counter()
    first_bytes = [gens[k].first_byte_at for k in _keys(plan) if k in gens]
    out.clip_id, out.path, out.duration_s = clip.id, clip_path(settings, clip), clip.duration_s
    out.cache_hit = billed == 0
    out.segments_total = len(plan.segs)
    out.segments_cached = len(plan.segs) - len(plan.paid)
    out.characters_billed, out.characters_saved = billed, saved
    out.cost_usd = tts_cost(plan.model_id, billed)
    # Time to first audio: the first byte of anything generated for it, or ~0 for a pure cache read.
    out.ttfa_ms = ((min(first_bytes) if first_bytes else now) - t0) * 1000
    out.total_ms = (now - t0) * 1000
    session.add(
        VoiceRender(
            event_id=item.event_id,
            announcement_id=item.announcement_id,
            language=item.language,
            urgency=item.urgency,
            kind=item.kind,
            model_id=plan.model_id,
            provider=provider,
            clip_id=clip.id,
            cache_hit=out.cache_hit,
            segments_total=out.segments_total,
            segments_cached=out.segments_cached,
            characters_total=plan.chars_total,
            characters_billed=billed,
            characters_saved=saved,
            cost_usd=out.cost_usd,
            cost_saved_usd=tts_cost(plan.model_id, saved),
            ttfa_ms=out.ttfa_ms,
            total_ms=out.total_ms,
        )
    )
    log.info(
        "voice event=%s ann=%s lang=%s kind=%s model=%s billed=%d saved=%d segs=%d/%d "
        "ttfa=%.0fms total=%.0fms",
        item.event_id, item.announcement_id, item.language, item.kind, plan.model_id,
        billed, saved, out.segments_cached, out.segments_total, out.ttfa_ms, out.total_ms,
    )  # fmt: skip
    return out


def _keys(plan: _Plan) -> list[str]:
    return [s.key for s in plan.segs]


# --- fixed segments, warmed ahead of time -----------------------------------------------------


def warm_fixed_segments(session: Session, tts: TTSProvider, settings: Settings, site: Site) -> int:
    """Voice every fixed opening and closing of the site, for both routes, so the first
    announcement does not pay for them. Returns the number of segments generated."""
    provider = provider_name(tts)
    wanted: dict[str, _Seg] = {}
    for language, segments in site.segments.items():
        for kind in ROUTES:
            model_id = route_model(kind, settings)
            for text in [*segments.openings, *segments.closings]:
                req = build_request(site, settings, text=text, language=language, model_id=model_id)
                key = cache_key(provider, req)
                if not _live_clip(session, settings, key):
                    wanted[key] = _Seg("fixed", req, key, permanent=True)
    limit = settings.elevenlabs_max_concurrency
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = {pool.submit(_synthesize, tts, s.req, limit): s for s in wanted.values()}
        for future in as_completed(futures):
            _store_segment(session, settings, provider, futures[future], future.result())
    session.commit()
    return len(wanted)


# --- metrics --------------------------------------------------------------------------------


class ModelStats(BaseModel):
    renders: int
    characters_billed: int
    cost_usd: float


class VoiceStats(BaseModel):
    renders: int
    cache_hit_renders: int  # announcements that cost nothing
    hit_rate: float  # cache_hit_renders / renders
    segments_total: int
    segments_cached: int
    segment_hit_rate: float
    characters_billed: int
    characters_saved: int
    cost_usd: float
    cost_saved_usd: float
    avg_ttfa_ms: float
    avg_total_ms: float
    by_model: dict[str, ModelStats]  # which model handled how much


def stats(session: Session) -> VoiceStats:
    """Aggregate the VoiceRender rows. No passenger data is in them."""
    v = VoiceRender
    row = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(cast(v.cache_hit, Integer)), 0),
            func.coalesce(func.sum(v.segments_total), 0),
            func.coalesce(func.sum(v.segments_cached), 0),
            func.coalesce(func.sum(v.characters_billed), 0),
            func.coalesce(func.sum(v.characters_saved), 0),
            func.coalesce(func.sum(v.cost_usd), 0.0),
            func.coalesce(func.sum(v.cost_saved_usd), 0.0),
            func.coalesce(func.avg(v.ttfa_ms), 0.0),
            func.coalesce(func.avg(v.total_ms), 0.0),
        )
    ).one()
    renders, hits, seg_total, seg_cached, billed, saved, cost, cost_saved, ttfa, total = row
    models = session.execute(
        select(col(v.model_id), func.count(), func.sum(v.characters_billed), func.sum(v.cost_usd))
        .group_by(col(v.model_id))
        .order_by(col(v.model_id))
    ).all()
    return VoiceStats(
        renders=renders,
        cache_hit_renders=hits,
        hit_rate=hits / renders if renders else 0.0,
        segments_total=seg_total,
        segments_cached=seg_cached,
        segment_hit_rate=seg_cached / seg_total if seg_total else 0.0,
        characters_billed=billed,
        characters_saved=saved,
        cost_usd=cost,
        cost_saved_usd=cost_saved,
        avg_ttfa_ms=ttfa,
        avg_total_ms=total,
        by_model={
            m: ModelStats(renders=n, characters_billed=c, cost_usd=u) for m, n, c, u in models
        },
    )
