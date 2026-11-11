"""Scenario feed and speed control.

POST /scenarios/{name}/run?speed=60&auto_approve=true   start a scenario in the background
                                      (A, B or C); auto_approve plays held events unattended
GET  /scenarios                       the scenarios and the current speed
GET  /scenarios/runs/{run_id}         progress of a run
GET/PUT /speed                        time scale: 60 means one minute passes in one second
"""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from relay import timeutil
from relay.context import Context
from relay.sources import replay


class SpeedBody(BaseModel):
    scale: float = Field(gt=0, le=10000)


def build_router(ctx: Context) -> APIRouter:
    router = APIRouter()
    runs: dict[str, replay.RunStatus] = {}
    tasks: set[asyncio.Task] = set()  # keep references so a run is not garbage-collected

    @router.get("/scenarios")
    def scenarios() -> dict:
        return {
            "speed": timeutil.get_scale(),
            "scenarios": [
                {"name": s.name, "description": s.description, "site_id": s.site_id,
                 "steps": len(s.events), "duration_s": max(e.at_s for e in s.events)}
                for s in replay.list_scenarios()
            ],
        }  # fmt: skip

    @router.post("/scenarios/{name}/run", status_code=202)
    async def run_scenario(
        name: str,
        speed: float | None = Query(None, gt=0, le=10000),
        auto_approve: bool = False,
    ) -> replay.RunStatus:
        scenario = replay.get_scenario(name)
        if scenario is None:
            raise HTTPException(404, "no such scenario")
        if speed is not None:
            timeutil.set_scale(speed)
        status = replay.RunStatus(run_id=uuid4().hex[:12], scenario=name)
        runs[status.run_id] = status
        task = asyncio.create_task(replay.run(ctx, scenario, status, auto_approve))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return status

    @router.get("/scenarios/runs/{run_id}")
    def run_status(run_id: str) -> replay.RunStatus:
        if run_id not in runs:
            raise HTTPException(404, "no such run")
        return runs[run_id]

    @router.get("/speed")
    def get_speed() -> dict:
        return {"scale": timeutil.get_scale()}

    @router.put("/speed")
    def set_speed(body: SpeedBody) -> dict:
        timeutil.set_scale(body.scale)
        return {"scale": timeutil.get_scale()}

    return router
