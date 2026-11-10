import json

import pytest
from sqlmodel import Session, select

from relay import timeutil
from relay.db import make_engine
from relay.workflow import engine as wf
from relay.workflow.models import ActionRecord, StepRun, WorkflowRun
from relay.workflow.retry import backoff_delay
from relay.workflow.steps import ApprovalDecision
from relay.workflow.triggers import fire_due_schedules

PAYLOAD = {"line": "Jubilee", "status": "Severe Delays", "reason": "signal failure"}
OK = ApprovalDecision(approved=True, approver="sup1")


def test_runs_to_pause_and_resumes_after_approval(session, ctx, conns):
    run, created = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k1")
    assert created and run.status == "paused" and run.current_step == 2
    assert conns.chat.sent == [] and conns.crm.records == {}  # nothing happens before approval

    run = wf.decide(session, ctx, run.id, OK)
    assert run.status == "succeeded"
    assert len(conns.crm.records) == 1 and len(conns.chat.sent) == 1


def test_paused_run_survives_restart(db_url, make_ctx, conns):
    eng1 = make_engine(db_url)
    from relay.db import init_db

    init_db(eng1)
    with Session(eng1) as s1:
        run, _ = wf.start_run(s1, make_ctx(), "disruption_followup", PAYLOAD, idempotency_key="k")
        run_id = run.id
    eng1.dispose()  # "process exits"

    eng2 = make_engine(db_url)  # "process restarts": new engine, new context, same file
    with Session(eng2) as s2:
        assert wf.recover(s2, make_ctx()) == []  # paused runs are not auto-advanced
        assert s2.get(WorkflowRun, run_id).status == "paused"
        run = wf.decide(s2, make_ctx(), run_id, OK)
        assert run.status == "succeeded"
    assert len(conns.chat.sent) == 1


def test_recover_continues_run_stopped_mid_flight(session, ctx, conns):
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    wf.decide(session, ctx, run.id, OK)
    # Simulate a crash after Slack was posted but before the step was saved as done.
    session.delete(session.get(ActionRecord, f"{run.id}:notify_slack"))
    run.status, run.current_step = "running", 4
    session.add(run)
    session.commit()
    wf.recover(session, ctx)
    assert session.get(WorkflowRun, run.id).status == "succeeded"
    # The step re-ran, but the connector deduped on the idempotency key: still one post.
    assert len(conns.crm.records) == 1 and len(conns.chat.sent) == 1


def test_rejection_ends_run_without_side_effects(session, ctx, conns):
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    run = wf.decide(session, ctx, run.id, ApprovalDecision(approved=False, approver="s"))
    assert run.status == "rejected"
    assert conns.crm.records == {} and conns.chat.sent == []


def test_trigger_idempotency_returns_same_run(session, ctx):
    a, created_a = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="x")
    b, created_b = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="x")
    assert created_a and not created_b and a.id == b.id
    assert len(session.exec(select(WorkflowRun)).all()) == 1


def test_double_approval_does_not_double_post(session, ctx, conns):
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    wf.decide(session, ctx, run.id, OK)
    wf.decide(session, ctx, run.id, OK)
    assert len(conns.crm.records) == 1 and len(conns.chat.sent) == 1


def test_action_idempotency_after_requeue(session, ctx, conns):
    """Slack fails for good once: case is created once, and requeue does not recreate it."""
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    conns.chat.failures.fail_next(3, retryable=True)  # == max attempts, so the step dead-letters
    run = wf.decide(session, ctx, run.id, OK)
    assert run.status == "dead_letter" and run.failed_step == "notify_slack"
    assert len(conns.crm.records) == 1

    run = wf.requeue(session, ctx, run.id)
    assert run.status == "succeeded"
    assert len(conns.crm.records) == 1 and len(conns.chat.sent) == 1
    assert session.exec(select(ActionRecord).where(ActionRecord.step_name == "create_case")).one()
    steps = session.exec(select(StepRun).where(StepRun.step_name == "create_case")).all()
    assert len(steps) == 1  # not even re-executed: the run resumed at the failed step


def test_connector_crash_after_side_effect_does_not_double_post(session, ctx, conns):
    """Side effect done but no ActionRecord saved: the connector's own key dedupes."""
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    run = wf.decide(session, ctx, run.id, OK)
    key = f"{run.id}:notify_slack"
    again = conns.chat.post_message("#station-ops", "same", idempotency_key=key)
    assert again.deduped and len(conns.chat.sent) == 1


def test_retry_with_backoff_then_success(session, ctx, conns, monkeypatch):
    sleeps = []
    monkeypatch.setattr(timeutil, "sleep", sleeps.append)
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    conns.crm.failures.fail_next(2, retryable=True)
    run = wf.decide(session, ctx, run.id, OK)
    assert run.status == "succeeded"
    assert sleeps == [0.5, 1.0]  # exponential: base, 2*base
    rec = session.exec(select(StepRun).where(StepRun.step_name == "create_case")).one()
    assert rec.attempts == 3


def test_retry_exhausted_goes_to_dead_letter_with_error(session, ctx, conns, monkeypatch):
    monkeypatch.setattr(timeutil, "sleep", lambda s: None)
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    conns.crm.failures.fail_next(5, retryable=True, message="salesforce 503")
    run = wf.decide(session, ctx, run.id, OK)
    assert run.status == "dead_letter"
    assert run.failed_step == "create_case"
    assert "salesforce 503" in run.error
    assert conns.chat.sent == []  # later steps never ran
    failed = session.exec(select(StepRun).where(StepRun.status == "failed")).one()
    assert "salesforce 503" in failed.error  # error is recorded on the trace row


def test_non_retryable_error_fails_immediately(session, ctx, conns, monkeypatch):
    sleeps = []
    monkeypatch.setattr(timeutil, "sleep", sleeps.append)
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    conns.crm.failures.fail_next(1, retryable=False, message="bad field")
    run = wf.decide(session, ctx, run.id, OK)
    assert run.status == "dead_letter" and sleeps == []


def test_backoff_delay_is_capped():
    assert [backoff_delay(n, 1.0) for n in (1, 2, 3)] == [1.0, 2.0, 4.0]
    assert backoff_delay(20, 1.0) == 30.0


def test_llm_retries_once_with_validation_error(session, ctx, conns):
    conns.llm.responses = ['{"headline": "only one field"}', json.dumps(
        {"headline": "Jubilee severe delays", "action": "Hold trains at Westminster"})]  # fmt: skip
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    assert run.status == "paused"
    assert len(conns.llm.calls) == 2
    assert "previous response was invalid" in conns.llm.calls[1]["prompt"]
    assert "action" in conns.llm.calls[1]["prompt"]  # names the missing field
    rec = session.exec(select(StepRun).where(StepRun.step_name == "summarise")).one()
    assert [c["outcome"] for c in rec.calls] == ["invalid", "accepted"]


def test_llm_invalid_twice_dead_letters(session, ctx, conns):
    conns.llm.responses = ["not json", "still not json"]
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    assert run.status == "dead_letter" and run.failed_step == "summarise"
    assert "schema validation" in run.error


def test_trace_records_prompt_model_tokens_cost_latency(session, ctx):
    run, _ = wf.start_run(session, ctx, "disruption_followup", PAYLOAD, idempotency_key="k")
    wf.decide(session, ctx, run.id, OK)
    rows = session.exec(select(StepRun).where(StepRun.run_id == run.id).order_by(StepRun.id)).all()
    assert [r.step_name for r in rows] == [
        "normalize", "summarise", "supervisor_approval", "create_case", "notify_slack",
    ]  # fmt: skip
    assert all(r.input is not None and r.output is not None for r in rows)
    llm = rows[1]
    assert llm.prompt_name == "disruption_summary" and llm.prompt_version == 1
    assert llm.model == "claude-haiku-5-5"
    assert llm.input_tokens > 0 and llm.cost_usd > 0 and llm.latency_ms >= 0


def test_invalid_trigger_payload_rejected_before_run(session, ctx):
    with pytest.raises(ValueError):
        wf.start_run(session, ctx, "disruption_followup", {"nope": 1}, idempotency_key="k")
    assert session.exec(select(WorkflowRun)).all() == []


def test_schedule_trigger_is_idempotent_per_slot(session, ctx):
    from dataclasses import replace

    from relay.workflow.steps import Trigger

    base = ctx.workflows["disruption_followup"]
    sched = replace(
        base, name="nightly",
        trigger=Trigger("schedule", base.trigger.payload_model, interval_s=3600,
                        static_payload=PAYLOAD),
    )  # fmt: skip
    ctx.workflows = {"nightly": sched}
    now = timeutil.now()
    first = fire_due_schedules(session, ctx, now)
    second = fire_due_schedules(session, ctx, now)
    assert len(first) == 1 and second == []
