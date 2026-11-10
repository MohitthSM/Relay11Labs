"""Workflow tables. Run state lives here so a paused run survives a restart."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from relay import timeutil

# Run statuses
RUNNING, PAUSED, SUCCEEDED, DEAD_LETTER, REJECTED = (
    "running",
    "paused",
    "succeeded",
    "dead_letter",
    "rejected",
)


class WorkflowRun(SQLModel, table=True):
    __tablename__ = "workflow_run"
    __table_args__ = (UniqueConstraint("workflow", "idempotency_key"),)

    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    workflow: str = Field(index=True)
    idempotency_key: str
    trigger_kind: str
    status: str = Field(default=RUNNING, index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # {"steps": {step_name: output_dict}}. Always reassigned, never mutated in place.
    state: dict[str, Any] = Field(default_factory=lambda: {"steps": {}}, sa_column=Column(JSON))
    current_step: int = 0
    failed_step: str | None = None
    error: str | None = None  # visible dead-letter reason
    created_at: datetime = Field(default_factory=timeutil.now)
    updated_at: datetime = Field(default_factory=timeutil.now)


class StepRun(SQLModel, table=True):
    """One execution of one step: the trace."""

    __tablename__ = "step_run"

    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(foreign_key="workflow_run.id", index=True)
    step_index: int
    step_name: str
    kind: str
    status: str  # succeeded | failed | awaiting_approval | rejected
    input: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    prompt_name: str | None = None
    prompt_version: int | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 1  # connector tries (retries + 1)
    replayed: bool = False  # action skipped because its idempotency key was already done
    calls: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    error: str | None = None
    started_at: datetime = Field(default_factory=timeutil.now)
    finished_at: datetime | None = None


class ActionRecord(SQLModel, table=True):
    """Done-marker for an action's side effect. The key is the primary key, so no duplicates."""

    __tablename__ = "action_record"

    key: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    step_name: str
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=timeutil.now)
