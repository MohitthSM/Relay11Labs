"""TfL line-status poller (Phase 7): real Tube status in, `line_status` events out.

Endpoint (checked against the live API, 2026-11-11):
  GET https://api.tfl.gov.uk/Line/Mode/tube/Status   (optional ?app_key=...)
  -> a JSON list of lines: {id, name, lineStatuses: [{statusSeverity, statusSeverityDescription,
     reason?}]}. `reason` is free text and is present only when something is wrong.

Rules (SPEC F1, CLAUDE.md fact lock):
- Poll every `relay_tfl_poll_s` real seconds. Not time-scaled: TfL does not run at demo speed.
- Emit only on change. Same status again -> `events.update` sees identical facts and ignores it.
    no event and status good           -> nothing
    no event and status not good       -> create   (the "line delay" event)
    event and a different bad status   -> update
    event and status good              -> resolve  (the "line resumed" event: the good-service
                                                    message plays through the site's resolution)
- Facts come only from the feed: the status enum from the severity code, `reason` from keywords in
  TfL's text (none matches -> no reason, never a guess), `location` only if exactly one station of
  the site's gazetteer is named in that text.
- Stale data never announces. A failed or malformed poll changes nothing and announces nothing:
  the last state is kept, the status turns to "error" with a warning, and the poll backs off.
  A snapshot is applied in the same call that fetched it, never later.
- Events made here carry source "tfl" and key `tfl-<line>`, so they never collide with the
  scripted scenarios. Live mode refuses scenario runs and demo mode does not poll. Switching to
  demo withdraws every active TfL event (jobs cancelled, no resolution message), so live data
  never plays over a scenario.
- The raw TfL response is not stored on the event (that needs a new column and the project has no
  migrations); each applied reading is logged with the line, severity and event id.
- The app key goes only in the query string of the request. Errors are logged by type and status
  code, never with the URL, so the key cannot reach a log.
"""

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx
from sqlmodel import Session, select

from relay import bus, timeutil
from relay import events as events_service
from relay.config import Settings
from relay.context import Context
from relay.models import ACTIVE, Event
from relay.schemas import LinePayload, LineStatus, Reason
from relay.sites import Site

log = logging.getLogger(__name__)

TFL_URL = "https://api.tfl.gov.uk/Line/Mode/tube/Status"
SOURCE = "tfl"
MAX_BACKOFF_S = 600.0
STALE_AFTER_POLLS = 3  # no good poll for this many intervals: the data is stale
FETCH_TIMEOUT_S = 10.0

DataMode = Literal["demo", "live"]

# TfL severity code -> our status. Anything else (planned closure, reduced service, special
# service, ...) is not a statement Relay can make, so it changes nothing.
SEVERITY_TO_STATUS: dict[int, LineStatus] = {
    10: "good_service",
    9: "minor_delays",
    6: "severe_delays",
    3: "part_suspended",
    2: "suspended",
}

# Keyword -> reason, first match wins. Order matters: "signal" before the generic "fault".
REASON_KEYWORDS: list[tuple[str, Reason]] = [
    (r"\bsignal", "signal_failure"),
    (r"\bcustomer (incident|taken ill|action)|\bperson (on|at) the track", "customer_incident"),
    (r"\bpower\b|\belectrical", "power_failure"),
    (r"\bfaulty train|\btrain fault|\bfault on a train|\bfix a faulty train", "train_fault"),
    (r"\bstaff (shortage|unavailab)|\bshortage of staff", "staff_shortage"),
    (r"\bengineering works?|\bplanned engineering", "engineering_works"),
]  # fmt: skip


class FeedError(Exception):
    """A poll that must not change anything. `retry_after` is a real-seconds floor for backoff."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class Reading:
    """One line as TfL reports it right now, reduced to what Relay can say."""

    line_id: str
    name: str
    severity: int
    status: LineStatus | None  # None: a status Relay does not announce
    reason: Reason | None
    location: str | None


@dataclass
class FeedState:
    mode: DataMode = "demo"
    last_ok: datetime | None = None
    last_error: str | None = None
    failures: int = 0  # consecutive failed polls
    next_poll: datetime | None = None
    readings: dict[str, Reading] = field(default_factory=dict)  # last good reading per line id
    line_errors: dict[str, str] = field(default_factory=dict)  # a line the last good poll skipped
    # Held while a poll applies readings and while the mode changes, so a poll that started in
    # live mode can never create an event after the switch to demo has withdrawn them.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


Fetch = Callable[[], Any]  # returns TfL's decoded JSON (blocking; runs in a worker thread)


# --- pure core: no I/O, time comes in as a parameter ----------------------------------------


def line_ids(settings: Settings) -> list[str]:
    return [x.strip().lower() for x in settings.relay_tfl_lines.split(",") if x.strip()]


def infer_reason(text: str) -> Reason | None:
    for pattern, reason in REASON_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return reason
    return None


def find_location(text: str, stations: list[str]) -> str | None:
    """The one gazetteer station named in the text. Two different ones: ambiguous, so none."""
    found = {s for s in stations if re.search(rf"\b{re.escape(s)}\b", text)}
    return found.pop() if len(found) == 1 else None


def parse_status(raw: Any, wanted: list[str], stations: list[str]) -> dict[str, Reading]:
    """TfL JSON -> a reading per wanted line. A response that is not a list of lines raises
    FeedError. A single malformed line is left out (the caller reports it as missing)."""
    if not isinstance(raw, list):
        raise FeedError("unexpected response: not a list of lines")
    out: dict[str, Reading] = {}
    for item in raw:
        reading = _parse_line(item, stations)
        if reading and reading.line_id in wanted:
            out[reading.line_id] = reading
    if wanted and not out:
        raise FeedError("none of the configured lines is in the response")
    return out


def _parse_line(item: Any, stations: list[str]) -> Reading | None:
    try:
        line_id, name = str(item["id"]).lower(), str(item["name"])
        statuses = item["lineStatuses"]
        worst = min((s for s in statuses if isinstance(s["statusSeverity"], int)),
                    key=lambda s: s["statusSeverity"])  # fmt: skip
        severity = worst["statusSeverity"]
    except (KeyError, TypeError, ValueError):  # min() of nothing is a ValueError
        return None
    status = SEVERITY_TO_STATUS.get(severity)
    text = worst.get("reason")
    text = text if isinstance(text, str) else ""
    bad = status is not None and status != "good_service"
    return Reading(
        line_id=line_id,
        name=name,
        severity=severity,
        status=status,
        reason=infer_reason(text) if bad else None,
        location=find_location(text, stations) if bad else None,
    )


def to_payload(reading: Reading) -> dict[str, Any]:
    """The LinePayload facts for a reading (the event's payload)."""
    assert reading.status is not None
    return LinePayload(
        line=reading.name, status=reading.status, reason=reading.reason, location=reading.location
    ).model_dump()


Action = Literal["none", "create", "update", "resolve"]


def decide(reading: Reading, active_payload: dict[str, Any] | None) -> Action:
    """What the reading means for the current event. `update` is sent even when the facts are
    unchanged: the events service ignores an identical update, so nothing is announced twice."""
    if reading.status is None:
        return "none"
    if active_payload is None:
        return "none" if reading.status == "good_service" else "create"
    return "resolve" if reading.status == "good_service" else "update"


def backoff_s(failures: int, poll_s: float, retry_after: float | None = None) -> float:
    """Real seconds to wait after `failures` failed polls in a row: doubles, capped at 10 min."""
    wait = min(poll_s * 2 ** max(failures - 1, 0), MAX_BACKOFF_S)
    return max(wait, retry_after or 0.0)


def feed_status(state: FeedState, settings: Settings, now: datetime) -> dict[str, Any]:
    """What the UI shows. `status`: off (demo mode) | waiting (no poll yet) | ok | error | stale.
    A warning is set whenever the data on screen is not from a poll that just worked."""
    age = (now - state.last_ok).total_seconds() if state.last_ok else None
    if state.mode != "live":
        status = "off"
    elif state.failures > 0:
        status = "error"
    elif age is None:
        status = "waiting"
    elif age > settings.relay_tfl_poll_s * STALE_AFTER_POLLS:
        status = "stale"
    else:
        status = "ok"
    warning = None
    if status == "error":
        when = f"last good poll {int(age)} s ago" if age is not None else "no poll has worked yet"
        warning = f"TfL feed failing ({state.last_error}); keeping the last state, {when}. "
        warning += "No announcement is made from stale data."
    elif status == "stale":
        warning = "TfL data is stale; no announcement is made until a poll works."
    return {
        "mode": state.mode,
        "status": status,
        "warning": warning,
        "lines": [
            {"id": i, "name": r.name, "status": r.status, "severity": r.severity}
            for i, r in state.readings.items()
        ],
        "line_errors": state.line_errors,
        "last_ok_at": state.last_ok.isoformat() if state.last_ok else None,
        "failures": state.failures,
        "poll_s": settings.relay_tfl_poll_s,
        "app_key": bool(settings.tfl_app_key),
    }


# --- thin edges: HTTP and the events service -------------------------------------------------


def make_fetch(settings: Settings, transport: httpx.BaseTransport | None = None) -> Fetch:
    """The real HTTP fetch. Raises FeedError, never an httpx error (those can carry the URL)."""
    params = {"app_key": settings.tfl_app_key} if settings.tfl_app_key else {}

    def fetch() -> Any:
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT_S, transport=transport) as client:
                res = client.get(TFL_URL, params=params)
        except httpx.HTTPError as exc:
            raise FeedError(f"network error ({type(exc).__name__})") from None
        if res.status_code == 429 or res.status_code >= 500:
            retry = res.headers.get("Retry-After", "")
            raise FeedError(
                f"TfL answered {res.status_code}", float(retry) if retry.isdigit() else None
            )
        if res.status_code != 200:
            raise FeedError(f"TfL answered {res.status_code}")
        try:
            return res.json()
        except ValueError:
            raise FeedError("response is not JSON") from None

    return fetch


def event_key(line_id: str) -> str:
    return f"{SOURCE}-{line_id}"


def apply_reading(ctx: Context, site: Site, reading: Reading) -> Action:
    """Blocking (worker thread). Returns what it did."""
    if reading.name not in site.gazetteer.lines:
        raise FeedError(f"line {reading.name!r} is not in the site gazetteer")
    key = event_key(reading.line_id)
    with Session(ctx.engine) as session:
        active = events_service.active_event(session, key)
        if active is not None and active.source != SOURCE:
            return "none"  # never take over an event an operator or a scenario made
        action = decide(reading, active.payload if active else None)
        if action == "create":
            events_service.create(
                session, ctx, site_id=site.id, payload=to_payload(reading), event_key=key,
                source=SOURCE,
            )  # fmt: skip
        elif action == "update":
            events_service.update(session, ctx, key, to_payload(reading), source=SOURCE)
        elif action == "resolve":
            events_service.resolve(session, ctx, key)
        return action


def poll_once(
    ctx: Context, state: FeedState, fetch: Fetch, now: datetime, site: Site | None = None
) -> bool:
    """One scheduled poll. Blocking. Returns True if the status the UI shows changed.
    A failure changes nothing but the error fields (stale data never announces)."""
    if state.mode != "live" or (state.next_poll and now < state.next_poll):
        return False
    settings = ctx.settings
    site = site or events_service.get_site_for(settings.relay_tfl_site, "line_status")
    before = feed_status(state, settings, now)
    try:
        readings = parse_status(fetch(), line_ids(settings), site.gazetteer.stations)
    except FeedError as exc:
        state.failures += 1
        state.last_error = str(exc)
        state.next_poll = now + timedelta(
            seconds=backoff_s(state.failures, settings.relay_tfl_poll_s, exc.retry_after)
        )
        log.warning("TfL poll failed (%d in a row): %s", state.failures, exc)
        return feed_status(state, settings, now) != before
    state.failures, state.last_error, state.last_ok = 0, None, now
    state.next_poll = now + timedelta(seconds=settings.relay_tfl_poll_s)
    state.line_errors = {}
    with state.lock:
        if state.mode == "live":  # the mode may have changed while the fetch was in flight
            _apply_all(ctx, state, site, readings)
    return True


def _apply_all(ctx: Context, state: FeedState, site: Site, readings: dict[str, Reading]) -> None:
    for line_id in line_ids(ctx.settings):
        reading = readings.get(line_id)
        if reading is None:
            state.line_errors[line_id] = "missing or malformed in the response"
            continue  # keep this line's last reading and event as they are
        try:
            action = apply_reading(ctx, site, reading)
        except Exception as exc:  # one line must not stop the others (a provider can fail too)
            state.line_errors[line_id] = f"{type(exc).__name__}: {exc}"
            log.warning("TfL line %s not applied: %s", line_id, type(exc).__name__)
            continue
        state.readings[line_id] = reading
        if action != "none":
            log.info("TfL %s severity %d -> %s", line_id, reading.severity, action)


def withdraw_all(ctx: Context) -> int:
    """Withdraw every active TfL event (jobs cancelled, no "good service" message). Returns how
    many. The scheduler drops their queued clips on its next tick; a clip already on air ends
    first, and a zone airs one clip at a time, so nothing from TfL overlaps a scenario."""
    with Session(ctx.engine) as session:
        rows = session.exec(select(Event).where(Event.state == ACTIVE, Event.source == SOURCE))
        withdrawn = [events_service.withdraw(session, e) for e in rows.all()]
    return len(withdrawn)


def set_mode(ctx: Context, state: FeedState, mode: DataMode) -> int:
    """Switch the data source. Blocking. Returns how many TfL events were withdrawn.

    live -> polls at once.
    demo -> the poller stops and every active TfL event is withdrawn, so live data never plays
            over a scripted scenario. Back in live mode the first poll announces a line that is
            still disrupted as a new event, from fresh data.
    """
    with state.lock:
        if mode == "live" and state.mode != "live":
            state.next_poll = None
            state.failures, state.last_error = 0, None
        state.mode = mode
        return withdraw_all(ctx) if mode == "demo" else 0


async def run(ctx: Context, state: FeedState, fetch: Fetch | None = None) -> None:
    """The poller task (started in the lifespan). Wakes every real second, like the scheduler,
    and lets `poll_once` decide whether a poll is due. Pushes the feed status on the WebSocket
    whenever it changes (D8), including when it turns stale without any poll failing."""
    fetch = fetch or make_fetch(ctx.settings)
    last: dict[str, Any] | None = None
    while True:
        now = timeutil.now()
        try:
            await asyncio.to_thread(poll_once, ctx, state, fetch, now)
        except Exception:  # the poller must never take the app down
            log.exception("TfL poller step failed")
        status = feed_status(state, ctx.settings, now)
        if status != last:
            last = status
            bus.publish({"type": "source", **status})
        await timeutil.asleep_real(1.0)
