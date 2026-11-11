"""Mock scenario feed: plays scenarios/*.json (A, B, C) as timed events through the events service.

`at_s` is scenario time. Waits go through timeutil, so RELAY_TIME_SCALE and the runtime speed
control apply: at 60x, scenario A's 12 minutes take 12 seconds.
A replayed `create` on an event that is still active becomes an update, so a scenario can be
run twice in a row without clearing anything (and an identical one is a duplicate, D21).
With auto_approve, an event that waits for approval (D20) is approved as "scenario runner", so
an unattended run plays everything; the audit log says who approved it.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlmodel import Session

from relay import events as events_service
from relay import timeutil
from relay.context import Context
from relay.models import PENDING

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
    state: Literal["running", "done", "failed"] = "running"
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


async def run(
    ctx: Context, scenario: Scenario, status: RunStatus, auto_approve: bool = False
) -> None:
    status.steps_total = len(scenario.events)
    elapsed = 0.0
    try:
        for step in scenario.events:
            await timeutil.asleep(max(step.at_s - elapsed, 0.0))
            elapsed = step.at_s
            await asyncio.to_thread(apply_step, ctx, scenario, step, auto_approve)
            status.steps_done += 1
        status.state = "done"
    except Exception as exc:  # a scenario must never take the app down
        log.exception("scenario %s failed", scenario.name)
        status.state, status.error = "failed", f"{type(exc).__name__}: {exc}"
