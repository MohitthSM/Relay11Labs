"""Workflow definitions: a trigger plus ordered, typed steps. Plain dataclasses, no magic."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

DETERMINISTIC, LLM, APPROVAL, ACTION = "deterministic", "llm", "approval", "action"


class ApprovalDecision(BaseModel):
    approved: bool
    approver: str
    comment: str = ""


@dataclass(frozen=True)
class Step:
    name: str
    kind: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    # Maps {"trigger": payload, "steps": {name: output}} to this step's input dict.
    # Default: the previous step's output (or the trigger payload for the first step).
    inputs: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    fn: Callable[..., Any] | None = None  # deterministic: fn(inp); action: fn(inp, ctx, key)
    prompt: str | None = None  # llm: prompt file name
    prompt_version: int | None = None  # llm: pin a version (None = latest)
    model: str | None = None  # llm: None = SCRIPT_MODEL_DEFAULT
    key: Callable[[BaseModel], str] | None = None  # action: idempotency key suffix


@dataclass(frozen=True)
class Trigger:
    kind: str  # webhook | schedule | slack_command
    payload_model: type[BaseModel]
    interval_s: float | None = None  # schedule
    command: str | None = None  # slack_command, e.g. "/disruption"
    static_payload: dict[str, Any] = field(default_factory=dict)  # schedule


@dataclass(frozen=True)
class Workflow:
    name: str
    trigger: Trigger
    steps: list[Step]


def deterministic(name, input_model, output_model, fn, inputs=None) -> Step:
    return Step(name, DETERMINISTIC, input_model, output_model, inputs=inputs, fn=fn)


def llm_step(
    name, input_model, output_model, prompt, inputs=None, model=None, prompt_version=None
) -> Step:
    return Step(
        name, LLM, input_model, output_model, inputs=inputs,
        prompt=prompt, prompt_version=prompt_version, model=model,
    )  # fmt: skip


def approval(name, input_model, inputs=None) -> Step:
    return Step(name, APPROVAL, input_model, ApprovalDecision, inputs=inputs)


def action(name, input_model, output_model, fn, inputs=None, key=None) -> Step:
    return Step(name, ACTION, input_model, output_model, inputs=inputs, fn=fn, key=key)
