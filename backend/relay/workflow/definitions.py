"""Workflows shipped with Relay. Each is data: a trigger plus ordered typed steps."""

from pydantic import BaseModel

from relay.workflow.context import Context
from relay.workflow.steps import (
    Trigger,
    Workflow,
    action,
    approval,
    deterministic,
    llm_step,
)


class DisruptionIn(BaseModel):
    line: str
    status: str
    reason: str = "not stated"


class Normalized(BaseModel):
    line: str
    status: str
    reason: str
    severity: int


class Summary(BaseModel):
    headline: str
    action: str


class ApprovalIn(BaseModel):
    line: str
    headline: str
    action: str


class CaseIn(BaseModel):
    line: str
    headline: str
    action: str


class CaseOut(BaseModel):
    case_id: str


class SlackIn(BaseModel):
    line: str
    headline: str
    case_id: str


class SlackOut(BaseModel):
    ts: str


SEVERITY = {"good service": 0, "minor delays": 1, "severe delays": 2, "suspended": 3}


def _normalize(inp: DisruptionIn) -> dict:
    status = inp.status.strip().lower()
    return {"line": inp.line.strip().lower(), "status": status, "reason": inp.reason,
            "severity": SEVERITY.get(status, 1)}  # fmt: skip


def _create_case(inp: CaseIn, ctx: Context, key: str) -> dict:
    rec = ctx.conns.crm.create_record(
        "Case", {"Subject": f"{inp.line}: {inp.headline}", "Description": inp.action},
        idempotency_key=key,
    )  # fmt: skip
    return {"case_id": rec.id}


def _post_slack(inp: SlackIn, ctx: Context, key: str) -> dict:
    text = f"[{inp.line}] {inp.headline} (case {inp.case_id})"
    res = ctx.conns.chat.post_message("#station-ops", text, idempotency_key=key)
    return {"ts": res.ts}


disruption_followup = Workflow(
    name="disruption_followup",
    trigger=Trigger(kind="webhook", payload_model=DisruptionIn),
    steps=[
        deterministic("normalize", DisruptionIn, Normalized, _normalize),
        llm_step("summarise", Normalized, Summary, prompt="disruption_summary"),
        approval(
            "supervisor_approval",
            ApprovalIn,
            inputs=lambda s: {"line": s["steps"]["normalize"]["line"], **s["steps"]["summarise"]},
        ),  # fmt: skip
        action(
            "create_case",
            CaseIn,
            CaseOut,
            _create_case,
            inputs=lambda s: {"line": s["steps"]["normalize"]["line"], **s["steps"]["summarise"]},
        ),  # fmt: skip
        action(
            "notify_slack",
            SlackIn,
            SlackOut,
            _post_slack,
            inputs=lambda s: {
                "line": s["steps"]["normalize"]["line"],
                "headline": s["steps"]["summarise"]["headline"],
                "case_id": s["steps"]["create_case"]["case_id"],
            },
        ),  # fmt: skip
    ],
)

WORKFLOWS: dict[str, Workflow] = {disruption_followup.name: disruption_followup}
