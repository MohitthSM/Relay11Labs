"""Playback jobs for one event version: create, hold for approval, release, cancel.

Sync and DB-only. The events service calls these inside its own transaction and commits; the
scheduler (scheduler.py) runs the jobs. One job per (event version, zone), so an update that
cancels the old version's jobs and creates new ones can never stack two announcements.

Human in the loop (D20): `approval_reason` decides, per site policy, whether an event waits for an
operator before its first play. A held job has no ends_at yet: its time starts when it is released.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, col, select

from relay import timeutil
from relay.models import APPROVED, AUTO, CANCELLED, HELD, PENDING, SCHEDULED, Event, PlaybackJob
from relay.sites import PRIORITY, RESOLUTION_PRIORITY, Approval, Site

# Payload values that make an event high severity, by event type and field. Everything else
# is routine. Pages and stand changes are routine: the gate agent or the feed already decided.
HIGH_SEVERITY: dict[str, dict[str, set[str]]] = {
    "line_status": {"status": {"severe_delays", "part_suspended", "suspended"}},
}

AUTO_APPROVER = "auto"  # approved_by when the policy let the event play without an operator


def high_severity(event_type: str, payload: dict[str, Any]) -> str | None:
    """The payload value that makes this event high severity, or None if it is routine."""
    for field, values in HIGH_SEVERITY.get(event_type, {}).items():
        if payload.get(field) in values:
            return str(payload[field])
    return None


def approval_reason(
    mode: Approval, *, source: str, event_type: str, payload: dict[str, Any]
) -> str | None:
    """Why this event must wait for an operator, or None if it may play at once."""
    if mode == "never":
        return None
    if mode == "always":
        return "site policy: always"
    if source == "staff_note":
        return "staff note"
    severe = high_severity(event_type, payload)
    return f"high severity: {severe}" if severe else None


def schedule_event(
    session: Session, event: Event, site: Site, now: datetime, *, resolution: bool = False
) -> list[PlaybackJob]:
    """One job per zone of the event's policy. Held if the event needs approval.

    resolution=True schedules the resolution message instead (SPEC F4): it plays
    `resolution_plays` times at info priority and waits only under the "always" policy.
    """
    assert event.id is not None
    policy = site.policies[event.type]
    if resolution:
        reason = "site policy: always" if policy.approval == "always" else None
        max_plays, duration_s, escalate = policy.resolution_plays, None, False
        priority = PRIORITY[RESOLUTION_PRIORITY]
    else:
        reason = approval_reason(
            policy.approval, source=event.source, event_type=event.type, payload=event.payload
        )
        max_plays, duration_s, escalate = policy.max_plays, policy.duration_s, policy.escalate
        priority = policy.rank

    if reason:
        event.approval, event.approval_reason = PENDING, reason
        event.approved_by = event.approved_at = None
    elif not resolution:
        event.approval, event.approval_reason = AUTO, None
        event.approved_by, event.approved_at = AUTO_APPROVER, now
    session.add(event)

    jobs = []
    for zone_id in policy.zones:
        job = PlaybackJob(
            event_id=event.id,
            zone_id=zone_id,
            languages=site.zone(zone_id).languages,
            is_resolution=resolution,
            next_at=now,
            interval_s=policy.interval_s,
            max_plays=max_plays,
            duration_s=duration_s,
            escalate=escalate,
            priority=priority,
            state=HELD if reason else SCHEDULED,
        )
        if not reason:
            job.ends_at = ends_at(now, duration_s)
        session.add(job)
        jobs.append(job)
    return jobs


def ends_at(start: datetime, duration_s: int | None) -> datetime | None:
    return start + timedelta(seconds=timeutil.scaled(duration_s)) if duration_s else None


def held_jobs(session: Session, event_id: int) -> list[PlaybackJob]:
    return list(
        session.exec(
            select(PlaybackJob).where(PlaybackJob.event_id == event_id, PlaybackJob.state == HELD)
        ).all()
    )


def release_held(session: Session, event: Event, operator: str, now: datetime) -> int:
    """An operator approved the event: its held jobs play from now. Returns how many."""
    assert event.id is not None
    jobs = held_jobs(session, event.id)
    for job in jobs:
        job.state, job.next_at, job.ends_at = SCHEDULED, now, ends_at(now, job.duration_s)
        session.add(job)
    event.approval, event.approved_by, event.approved_at = APPROVED, operator, now
    session.add(event)
    return len(jobs)


def cancel_jobs(session: Session, event_id: int) -> int:
    """Stop an event version's future plays (update or resolve, R9). The scheduler drops its
    queued clips on the next tick; a clip already on air finishes. Returns how many jobs."""
    jobs = session.exec(
        select(PlaybackJob).where(
            PlaybackJob.event_id == event_id, col(PlaybackJob.state).in_([HELD, SCHEDULED])
        )
    ).all()
    for job in jobs:
        job.state = CANCELLED
        session.add(job)
    return len(jobs)
