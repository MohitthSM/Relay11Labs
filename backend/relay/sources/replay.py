"""Mock scenario feed: plays scenarios/*.json (A, B, C) as timed events through the events service.

`at_s` is scenario time. Waits go through timeutil, so RELAY_TIME_SCALE and the runtime speed
control apply: at 60x, scenario A's 12 minutes take 12 seconds.
A replayed `create` on an event that is still active becomes an update, so a scenario can be
run twice in a row without clearing anything (and an identical one is a duplicate, D21).
With auto_approve, an event that waits for approval (D20) is approved as "scenario runner", so
an unattended run plays everything; the audit log says who approved it.
Scenarios belong to demo mode: a switch to live stops the run before its next step (the step and
the switch share the feed lock, so a step can never land after the switch), and the switch
withdraws the events the scenario made (tfl.set_mode).
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlmodel import Session

from relay import events as events_service
from relay import timeutil
from relay.context import Context
from relay.models import PENDING
from relay.sources.tfl import FeedState

log = logging.getLogger(__name__)
SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "scenarios"


class Step(BaseModel):
    at_s: float
    action: Literal["create", "update", "resolve"]
    event_key: str
    payload: dict[str, Any] | None = None


class Scenario(BaseModel):
    name: str
    description: str
    site_id: str
    events: list[Step]


class RunStatus(BaseModel):
    run_id: str
    scenario: str
    state: Literal["running", "done", "failed", "stopped"] = "running"  # stopped: source went live
    steps_done: int = 0
    steps_total: int = 0
    error: str | None = None


def list_scenarios(directory: Path = SCENARIOS_DIR) -> list[Scenario]:
    return [
        Scenario.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(directory.glob("*.json"))
    ]


def get_scenario(name: str, directory: Path = SCENARIOS_DIR) -> Scenario | None:
    return next((s for s in list_scenarios(directory) if s.name == name), None)


AUTO_APPROVER = "scenario runner"


def apply_step(ctx: Context, scenario: Scenario, step: Step, auto_approve: bool = False) -> None:
    """Blocking: runs in a worker thread."""
    with Session(ctx.engine) as session:
        active = events_service.active_event(session, step.event_key)
        if step.action == "resolve":
            if active:
                events_service.resolve(session, ctx, step.event_key, step.payload)
            return
        if active:  # create-while-active and update both make a new version
            event = events_service.update(
                session, ctx, step.event_key, step.payload or {}, source="replay"
            )
        else:
            event = events_service.create(
                session, ctx, site_id=scenario.site_id, payload=step.payload or {},
                event_key=step.event_key, source="replay",
            )  # fmt: skip
        if auto_approve and event.approval == PENDING and event.id is not None:
            events_service.approve(session, event.id, AUTO_APPROVER)


def apply_step_in_demo(
    ctx: Context, feed: FeedState, scenario: Scenario, step: Step, auto_approve: bool
) -> bool:
    """Blocking. Applies the step only while the source is demo. Returns False if it is not."""
    with feed.lock:
        if feed.mode != "demo":
            return False
        apply_step(ctx, scenario, step, auto_approve)
        return True


POLL_MODE_S = 0.5  # real seconds: how soon a waiting run notices a switch to live


async def _wait_in_demo(feed: FeedState, seconds: float) -> bool:
    """Sleep `seconds` of scenario time, waking often enough to notice a switch to live.
    Returns False as soon as the source is not demo."""
    end = time.monotonic() + timeutil.scaled(seconds)
    while (left := end - time.monotonic()) > 0:
        if feed.mode != "demo":
            return False
        await timeutil.asleep_real(min(left, POLL_MODE_S))
    return feed.mode == "demo"


async def run(
    ctx: Context,
    scenario: Scenario,
    status: RunStatus,
    feed: FeedState,
    auto_approve: bool = False,
) -> None:
    status.steps_total = len(scenario.events)
    elapsed = 0.0
    try:
        for step in scenario.events:
            in_demo = await _wait_in_demo(feed, max(step.at_s - elapsed, 0.0))
            elapsed = step.at_s
            applied = in_demo and await asyncio.to_thread(
                apply_step_in_demo, ctx, feed, scenario, step, auto_approve
            )
            if not applied:
                status.state = "stopped"
                log.info("scenario %s stopped: the source is live", scenario.name)
                return
            status.steps_done += 1
        status.state = "done"
    except Exception as exc:  # a scenario must never take the app down
        log.exception("scenario %s failed", scenario.name)
        status.state, status.error = "failed", f"{type(exc).__name__}: {exc}"
