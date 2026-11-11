"""Simulate a busy day at one station and print its TTS characters and cost (docs/COSTS.md).

    uv run python scripts/simulate_day.py

Runs the real events service, script engine, voice layer and scheduler, in mock mode, on a fake
clock, against a throwaway database. Mock TTS bills len(text) characters, which is how
ElevenLabs bills, so the character counts are the real ones for the template wording (an LLM
script is usually within a few characters of it). Every number comes from the VoiceRender and
PlayLog rows, the same rows the metrics page reads.

The day (Canary Wharf, Jubilee line, 05:30 to 00:30) is a plausible bad weekday built from the
kind of status changes TfL reports; the times are invented, the wording is Relay's. A severe
status waits for an operator (D20); the simulation approves it at once.
"""

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from relay import events as events_service
from relay import scheduler, timeutil
from relay.config import Settings
from relay.context import Context
from relay.db import init_db, make_engine
from relay.models import PENDING, Announcement, PlayLog, VoiceRender
from relay.providers import build_connectors
from relay.providers.pricing import tts_cost

SITE = "tube_station"
KEY = "tfl-jubilee"
OPEN, CLOSE = "05:30", "24:30"
TICK_S = 5  # fake seconds per scheduler tick (clips last about 10 s)


def status(s: str, reason: str | None = None, location: str | None = None) -> dict[str, Any]:
    return {"type": "line_status", "line": "Jubilee", "status": s, "reason": reason,
            "location": location}  # fmt: skip


# (time, payload or "good"). The same reading twice in a row is what a 60 s poll sends.
DAY: list[tuple[str, dict[str, Any] | str]] = [
    ("07:40", status("minor_delays", "train_fault")),
    ("07:41", status("minor_delays", "train_fault")),  # identical: a duplicate, costs nothing
    ("08:10", status("severe_delays", "train_fault")),
    ("08:50", status("minor_delays", "train_fault")),
    ("09:20", "good"),
    ("12:15", status("minor_delays", "customer_incident", "Canada Water")),
    ("12:45", "good"),
    ("17:20", status("severe_delays", "signal_failure", "London Bridge")),
    ("17:50", status("part_suspended", "signal_failure", "London Bridge")),
    ("18:40", status("severe_delays", "signal_failure", "London Bridge")),
    ("19:10", status("minor_delays", "signal_failure", "London Bridge")),
    ("19:40", "good"),
    ("22:00", status("minor_delays", "train_fault")),  # same words as the morning: cache hit
    ("22:30", "good"),
]


class Clock:
    def __init__(self, start: datetime) -> None:
        self.t = start

    def now(self) -> datetime:
        return self.t


def at(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return day + timedelta(hours=h, minutes=m)


def apply(ctx: Context, payload: dict[str, Any] | str) -> None:
    with Session(ctx.engine) as session:
        active = events_service.active_event(session, KEY)
        if payload == "good":
            if active:
                events_service.resolve(session, ctx, KEY)
            return
        assert isinstance(payload, dict)
        if active:
            event = events_service.update(session, ctx, KEY, payload, source="tfl")
        else:
            event = events_service.create(
                session, ctx, site_id=SITE, payload=payload, event_key=KEY, source="tfl"
            )
        if event.approval == PENDING and event.id is not None:
            events_service.approve(session, event.id, "simulation")


def run(tmp: str) -> dict[str, Any]:
    settings = Settings(
        relay_llm_mode="mock", relay_tts_mode="mock", audio_dir=f"{tmp}/audio",
        relay_db_url=f"sqlite:///{tmp}/day.db",
    )  # fmt: skip
    engine = make_engine(settings.relay_db_url)
    init_db(engine)
    ctx = Context(conns=build_connectors(settings), settings=settings, engine=engine)
    day = datetime(2026, 11, 11, tzinfo=UTC)
    clock = Clock(at(day, OPEN))
    timeutil.now = clock.now  # the whole app reads time through here (D11)
    timeutil.set_scale(1)
    state = scheduler.State()
    import relay.bus as bus

    bus.publish = lambda message: None  # no WebSocket clients in a simulation
    pending = list(DAY)
    end = at(day, CLOSE)
    while clock.t <= end:
        while pending and at(day, pending[0][0]) <= clock.t:
            apply(ctx, pending.pop(0)[1])
        scheduler.step(ctx, state, clock.t)
        clock.t += timedelta(seconds=TICK_S)
    return measure(ctx)


def measure(ctx: Context) -> dict[str, Any]:
    with Session(ctx.engine) as s:
        renders = s.exec(select(VoiceRender)).all()
        plays = s.exec(select(PlayLog)).all()
        anns = {a.id: a for a in s.exec(select(Announcement)).all()}
        by_ann = {r.announcement_id: r for r in renders}
        # Without any cache, every play is a fresh TTS request of the whole announcement.
        no_cache_chars = no_cache_cost = 0.0
        for p in plays:
            for ann_id in p.announcement_ids:
                r = by_ann.get(ann_id)
                if r is not None:
                    no_cache_chars += r.characters_total
                    no_cache_cost += tts_cost(r.model_id, r.characters_total)
        # Rendering once per announcement but with no shared fixed segments (D18 off).
        whole_chars = sum(r.characters_total for r in renders)
        whole_cost = sum(tts_cost(r.model_id, r.characters_total) for r in renders)
        return {
            "versions": len({a.event_id for a in anns.values()}),
            "announcements": len(renders),
            "plays": len(plays),
            "chars_per_announcement": [r.characters_total for r in renders],
            "billed_chars": sum(r.characters_billed for r in renders),
            "billed_cost": sum(r.cost_usd for r in renders),
            "full_hits": sum(r.cache_hit for r in renders),
            "segments_total": sum(r.segments_total for r in renders),
            "segments_cached": sum(r.segments_cached for r in renders),
            "whole_chars": whole_chars,
            "whole_cost": whole_cost,
            "no_cache_chars": no_cache_chars,
            "no_cache_cost": no_cache_cost,
        }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        m = run(tmp)
    plays, billed = m["plays"], m["billed_chars"]
    hit_rate = 1 - billed / m["no_cache_chars"] if m["no_cache_chars"] else 0.0
    chars = m["chars_per_announcement"]
    print(f"announcements voiced: {m['announcements']} ({m['versions']} event versions, "
          "plus resolution messages)")  # fmt: skip
    print(f"characters per announcement: min {min(chars)}, max {max(chars)}, "
          f"mean {sum(chars) / len(chars):.0f}")  # fmt: skip
    print(f"plays (zone x repeat): {plays}")
    print(f"announcements served whole from cache: {m['full_hits']}/{m['announcements']}")
    print(f"segments served from cache: {m['segments_cached']}/{m['segments_total']}")
    print(f"character cache hit rate over all plays: {hit_rate:.1%}")
    print("| Strategy | Characters | Cost (USD) |")
    print("|---|---|---|")
    print(f"| No cache: TTS on every play | {m['no_cache_chars']:,.0f} "
          f"| {m['no_cache_cost']:.2f} |")  # fmt: skip
    print(f"| One render per announcement, no shared segments | {m['whole_chars']:,} "
          f"| {m['whole_cost']:.3f} |")  # fmt: skip
    print(f"| Relay (content-hash cache + fixed segments) | {billed:,} "
          f"| {m['billed_cost']:.3f} |")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
