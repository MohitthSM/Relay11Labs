"""HTTP triggers (webhook, Slack slash command) and run inspection/approval."""

import asyncio
import hashlib
import hmac
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from relay.db import session_dependency
from relay.workflow import engine
from relay.workflow.context import Context
from relay.workflow.models import StepRun, WorkflowRun
from relay.workflow.steps import ApprovalDecision
from relay.workflow.triggers import parse_slash_text

SLACK_MAX_AGE_S = 300


def build_router(ctx: Context, session_engine: Engine) -> APIRouter:
    router = APIRouter()
    get_session = session_dependency(session_engine)

    def run_view(session: Session, run: WorkflowRun) -> dict:
        steps = session.exec(
            select(StepRun).where(StepRun.run_id == run.id).order_by(StepRun.id)  # type: ignore[arg-type]
        ).all()
        return {**run.model_dump(mode="json"), "steps": [s.model_dump(mode="json") for s in steps]}

    @router.post("/webhooks/{workflow}")
    def webhook(
        workflow: str,
        payload: dict,
        idempotency_key: str = Header(...),
        session: Session = Depends(get_session),
    ):
        if workflow not in ctx.workflows:
            raise HTTPException(404, "unknown workflow")
        try:
            run, _ = engine.start_run(
                session, ctx, workflow, payload, idempotency_key=idempotency_key
            )
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_input=False)) from exc
        return run_view(session, run)

    @router.post("/slack/commands")
    async def slack_command(request: Request):
        body = await request.body()
        secret = ctx.settings.slack_signing_secret
        if secret:
            ts = request.headers.get("x-slack-request-timestamp", "0")
            base = f"v0:{ts}:{body.decode()}".encode()
            expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
            sig = request.headers.get("x-slack-signature", "")
            if abs(time.time() - int(ts)) > SLACK_MAX_AGE_S or not hmac.compare_digest(
                sig, expected
            ):
                raise HTTPException(401, "bad signature")
        form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
        wf = next(
            (w for w in ctx.workflows.values() if w.trigger.command == form.get("command")), None
        )
        if wf is None:
            return {"response_type": "ephemeral", "text": "Unknown command."}
        key = form.get("trigger_id") or f"{form.get('command')}:{form.get('text')}"

        def work() -> dict:
            with Session(session_engine) as session:
                try:
                    run, _ = engine.start_run(
                        session, ctx, wf.name, parse_slash_text(form.get("text", "")),
                        idempotency_key=key,
                    )  # fmt: skip
                except ValidationError:
                    return {"response_type": "ephemeral", "text": "Missing or invalid fields."}
                return {"response_type": "ephemeral", "text": f"Run {run.id}: {run.status}"}

        return await asyncio.to_thread(work)

    @router.get("/runs")
    def list_runs(status: str | None = None, session: Session = Depends(get_session)):
        query = select(WorkflowRun).order_by(WorkflowRun.created_at.desc())  # type: ignore[attr-defined]
        if status:
            query = query.where(WorkflowRun.status == status)
        return [r.model_dump(mode="json") for r in session.exec(query).all()]

    @router.get("/runs/{run_id}")
    def get_run(run_id: str, session: Session = Depends(get_session)):
        run = session.get(WorkflowRun, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        return run_view(session, run)

    @router.post("/runs/{run_id}/approval")
    def approve(run_id: str, decision: ApprovalDecision, session: Session = Depends(get_session)):
        try:
            run = engine.decide(session, ctx, run_id, decision)
        except KeyError:
            raise HTTPException(404, "unknown run") from None
        return run_view(session, run)

    @router.post("/runs/{run_id}/requeue")
    def requeue(run_id: str, session: Session = Depends(get_session)):
        try:
            run = engine.requeue(session, ctx, run_id)
        except KeyError:
            raise HTTPException(404, "unknown run") from None
        return run_view(session, run)

    return router
