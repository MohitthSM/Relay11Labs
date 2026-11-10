"""Trigger helpers. Webhook and Slack come in through api/workflows.py; schedules fire here."""

from datetime import datetime

from sqlmodel import Session

from relay import timeutil
from relay.workflow.context import Context
from relay.workflow.engine import start_run


def parse_slash_text(text: str) -> dict[str, str]:
    """'line=jubilee status=severe' -> {'line': 'jubilee', 'status': 'severe'}."""
    return dict(part.split("=", 1) for part in text.split() if "=" in part)


def fire_due_schedules(session: Session, ctx: Context, now: datetime) -> list[str]:
    """Start one run per schedule workflow per time slot. Safe to call every tick.

    The slot number is part of the idempotency key, so calling twice in one slot (or from two
    processes) creates one run. Returns ids of runs created by this call.
    """
    created = []
    for wf in ctx.workflows.values():
        if wf.trigger.kind != "schedule" or not wf.trigger.interval_s:
            continue
        slot = int(now.timestamp() // timeutil.scaled(wf.trigger.interval_s))
        run, was_created = start_run(
            session, ctx, wf.name, wf.trigger.static_payload, idempotency_key=f"schedule:{slot}"
        )
        if was_created:
            created.append(run.id)
    return created
