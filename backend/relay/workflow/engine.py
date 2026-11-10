"""Runs workflows. Sync; state is saved after every step so a run can stop and resume anywhere."""

import json
import logging
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from relay import timeutil
from relay.providers.pricing import llm_cost
from relay.workflow.context import Context
from relay.workflow.models import (
    DEAD_LETTER,
    PAUSED,
    REJECTED,
    RUNNING,
    SUCCEEDED,
    ActionRecord,
    StepRun,
    WorkflowRun,
)
from relay.workflow.prompts import load_prompt
from relay.workflow.retry import call_with_retry
from relay.workflow.steps import ACTION, APPROVAL, DETERMINISTIC, LLM, ApprovalDecision, Step

log = logging.getLogger(__name__)
# Prompt file location is resolved against the backend dir when relative.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class StepFailure(Exception):
    """A step failed for good; the run goes to dead letter."""


def _save(session: Session, *objs) -> None:
    for obj in objs:
        if isinstance(obj, WorkflowRun):
            obj.updated_at = timeutil.now()
        session.add(obj)
    session.commit()


# ---------------------------------------------------------------- starting a run


def start_run(
    session: Session,
    ctx: Context,
    workflow: str,
    payload: dict,
    *,
    idempotency_key: str,
) -> tuple[WorkflowRun, bool]:
    """Create and advance a run. A repeated key returns the existing run: (run, created)."""
    wf = ctx.workflows[workflow]
    existing = session.exec(
        select(WorkflowRun).where(
            WorkflowRun.workflow == workflow, WorkflowRun.idempotency_key == idempotency_key
        )
    ).first()
    if existing:
        return existing, False
    clean = wf.trigger.payload_model.model_validate(payload).model_dump(mode="json")
    run = WorkflowRun(
        workflow=workflow, idempotency_key=idempotency_key, trigger_kind=wf.trigger.kind,
        payload=clean,
    )  # fmt: skip
    try:
        _save(session, run)
    except Exception:  # lost a race on the unique key: someone else created it
        session.rollback()
        existing = session.exec(
            select(WorkflowRun).where(
                WorkflowRun.workflow == workflow, WorkflowRun.idempotency_key == idempotency_key
            )
        ).first()
        if existing:
            return existing, False
        raise
    return advance(session, ctx, run), True


# ---------------------------------------------------------------- advancing


def advance(session: Session, ctx: Context, run: WorkflowRun) -> WorkflowRun:
    """Execute steps from run.current_step until the run pauses, finishes or dead-letters."""
    wf = ctx.workflows[run.workflow]
    while run.status == RUNNING and run.current_step < len(wf.steps):
        step = wf.steps[run.current_step]
        rec = StepRun(run_id=run.id, step_index=run.current_step, step_name=step.name,
                      kind=step.kind, status="succeeded")  # fmt: skip
        try:
            raw = _raw_input(step, wf.steps, run)
            inp = step.input_model.model_validate(raw)
            rec.input = inp.model_dump(mode="json")
            if step.kind == APPROVAL:
                rec.status = "awaiting_approval"
                run.status = PAUSED
                _save(session, rec, run)
                return run
            out = _EXECUTORS[step.kind](step, inp, run, rec, ctx, session)
            rec.output = out.model_dump(mode="json")
            run.state = {"steps": {**run.state["steps"], step.name: rec.output}}
            run.current_step += 1
        except Exception as exc:  # noqa: BLE001  any failure dead-letters with a visible error
            rec.status = "failed"
            rec.error = f"{type(exc).__name__}: {exc}"
            run.status = DEAD_LETTER
            run.failed_step = step.name
            run.error = f"step {step.name!r} failed: {rec.error}"
            log.error("run %s dead-lettered at step %s", run.id, step.name)
        rec.finished_at = timeutil.now()
        _save(session, rec, run)
    if run.status == RUNNING and run.current_step >= len(wf.steps):
        run.status = SUCCEEDED
        _save(session, run)
    return run


def _raw_input(step: Step, steps: list[Step], run: WorkflowRun) -> dict:
    view = {"trigger": run.payload, "steps": run.state["steps"]}
    if step.inputs:
        return step.inputs(view)
    idx = steps.index(step)
    return run.state["steps"][steps[idx - 1].name] if idx > 0 else run.payload


# ---------------------------------------------------------------- step executors


def _run_deterministic(step, inp, run, rec, ctx, session) -> BaseModel:
    return step.output_model.model_validate(step.fn(inp))


def _run_llm(step, inp, run, rec, ctx, session) -> BaseModel:
    s = ctx.settings
    prompts_dir = Path(s.prompts_dir)
    if not prompts_dir.is_absolute():
        prompts_dir = BACKEND_DIR / prompts_dir
    prompt = load_prompt(prompts_dir, step.prompt, step.prompt_version)
    rec.prompt_name, rec.prompt_version = prompt.name, prompt.version
    rec.model = step.model or s.script_model_default
    schema = step.output_model.model_json_schema()
    base = (
        prompt.render(**inp.model_dump(mode="json"))
        + "\n\nRespond with JSON only, matching this JSON schema:\n"
        + json.dumps(schema)
    )
    text, calls = base, []
    for attempt in (1, 2):  # one retry, carrying the validation error
        started = time.perf_counter()
        result, tries = call_with_retry(
            lambda text=text: ctx.conns.llm.complete_json(text, schema, rec.model),
            max_attempts=s.connector_max_attempts,
            base_delay_s=s.connector_base_delay_s,
        )
        latency = int((time.perf_counter() - started) * 1000)
        cost = llm_cost(result.model, result.input_tokens, result.output_tokens)
        call = {"attempt": attempt, "model": result.model, "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens, "cost_usd": cost, "latency_ms": latency,
                "connector_tries": tries, "outcome": "accepted"}  # fmt: skip
        calls.append(call)
        rec.input_tokens += result.input_tokens
        rec.output_tokens += result.output_tokens
        rec.cost_usd += cost
        rec.latency_ms += latency
        rec.attempts = max(rec.attempts, tries)
        rec.calls = calls
        try:
            return step.output_model.model_validate_json(result.text)
        except ValidationError as exc:
            call["outcome"] = "invalid"
            call["error"] = _short_errors(exc)
            if attempt == 2:
                raise StepFailure(
                    f"output failed schema validation twice: {call['error']}"
                ) from exc
            text = (
                f"{base}\n\nYour previous response was invalid: {call['error']}\n"
                f"Previous response:\n{result.text}\nReturn corrected JSON only."
            )
    raise AssertionError("unreachable")


def _short_errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'root'}: {e['msg']}" for e in exc.errors())


def _run_action(step, inp, run, rec, ctx, session) -> BaseModel:
    suffix = step.key(inp) if step.key else step.name
    key = f"{run.id}:{suffix}"
    done = session.get(ActionRecord, key)
    if done:  # side effect already happened (retry, requeue or restart): do not repeat it
        rec.replayed = True
        return step.output_model.model_validate(done.result)
    started = time.perf_counter()
    result, tries = call_with_retry(
        lambda: step.fn(inp, ctx, key),
        max_attempts=ctx.settings.connector_max_attempts,
        base_delay_s=ctx.settings.connector_base_delay_s,
    )
    rec.latency_ms = int((time.perf_counter() - started) * 1000)
    rec.attempts = tries
    out = step.output_model.model_validate(result)
    session.add(ActionRecord(key=key, run_id=run.id, step_name=step.name,
                             result=out.model_dump(mode="json")))  # fmt: skip
    return out


_EXECUTORS = {
    DETERMINISTIC: _run_deterministic,
    LLM: _run_llm,
    ACTION: _run_action,
}


# ---------------------------------------------------------------- approval, restart, requeue


def decide(session: Session, ctx: Context, run_id: str, decision: ApprovalDecision) -> WorkflowRun:
    """Record the approval decision for a paused run and continue. First decision wins."""
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status != PAUSED:
        return run  # already decided (or not waiting): a repeat is a no-op
    step = ctx.workflows[run.workflow].steps[run.current_step]
    rec = session.exec(
        select(StepRun)
        .where(StepRun.run_id == run.id, StepRun.step_index == run.current_step)
        .order_by(StepRun.id.desc())  # type: ignore[union-attr]
    ).first()
    rec.output = decision.model_dump(mode="json")
    rec.finished_at = timeutil.now()
    if not decision.approved:
        rec.status = "rejected"
        run.status = REJECTED
        run.state = {"steps": {**run.state["steps"], step.name: rec.output}}
        _save(session, rec, run)
        return run
    rec.status = "succeeded"
    run.state = {"steps": {**run.state["steps"], step.name: rec.output}}
    run.current_step += 1
    run.status = RUNNING
    _save(session, rec, run)
    return advance(session, ctx, run)


def recover(session: Session, ctx: Context) -> list[WorkflowRun]:
    """After a restart: continue runs that were mid-flight. Paused runs keep waiting."""
    runs = session.exec(select(WorkflowRun).where(WorkflowRun.status == RUNNING)).all()
    return [advance(session, ctx, run) for run in runs]


def requeue(session: Session, ctx: Context, run_id: str) -> WorkflowRun:
    """Retry a dead-lettered run from its failed step. Completed actions are not repeated."""
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise KeyError(run_id)
    if run.status != DEAD_LETTER:
        return run
    run.status, run.error, run.failed_step = RUNNING, None, None
    _save(session, run)
    return advance(session, ctx, run)
