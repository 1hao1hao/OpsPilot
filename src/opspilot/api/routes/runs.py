"""Run resources and the deprecated /analyze compatibility adapter."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status

from opspilot.models import AlertEvent, CreateRunRequest, RunAccepted, RunResult, RunStatus, RuntimeEventView, RunView
from opspilot.runtime.task_manager import TaskManager


def _manager(request: Request) -> TaskManager:
    return request.app.state.task_manager


def create_runs_router() -> APIRouter:
    router = APIRouter(tags=["runs"])

    @router.post("/api/v1/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def create_run(payload: CreateRunRequest, request: Request) -> RunAccepted:
        return await _manager(request).create_run(request_id=payload.request_id, alert=payload.alert)

    @router.get("/api/v1/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, request: Request) -> RunView:
        run = await _manager(request).get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    @router.get("/api/v1/runs/{run_id}/result", response_model=RunResult)
    async def get_result(run_id: str, request: Request) -> RunResult:
        result = await _manager(request).get_result(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="run not found")
        if result.status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise HTTPException(status_code=202, detail={"run_id": run_id, "status": result.status.value})
        return result

    @router.get("/api/v1/runs/{run_id}/events", response_model=list[RuntimeEventView])
    async def get_events(run_id: str, request: Request, after: int = 0) -> list[RuntimeEventView]:
        events = await _manager(request).get_events(run_id, after=after)
        if events is None:
            raise HTTPException(status_code=404, detail="run not found")
        return events

    @router.websocket("/api/v1/runs/{run_id}/stream")
    async def stream_run(websocket: WebSocket, run_id: str) -> None:
        await websocket.accept()
        manager: TaskManager = websocket.app.state.task_manager
        sequence = 0
        try:
            while True:
                events = await manager.get_events(run_id, after=sequence)
                if events is None:
                    await websocket.send_json({"event": "error", "detail": "run not found"})
                    return
                for event in events:
                    sequence = event.sequence
                    await websocket.send_json(event.model_dump(mode="json"))
                run = await manager.get_run(run_id)
                if run and run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
                    return
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            return

    @router.post("/api/v1/analyze", status_code=status.HTTP_202_ACCEPTED)
    async def legacy_analyze(alert: AlertEvent, request: Request) -> dict:
        """Compatibility entrypoint; it creates the exact same persistent Run."""
        accepted = await _manager(request).create_run(request_id=f"legacy:{alert.alert_id}", alert=alert)
        return {
            "trace_id": accepted.run_id,
            "status": accepted.status.value.lower(),
            "websocket_url": f"/api/v1/runs/{accepted.run_id}/stream",
        }

    @router.get("/api/v1/analyze/{run_id}/status")
    async def legacy_status(run_id: str, request: Request) -> dict:
        run = await get_run(run_id, request)
        return {"trace_id": run.run_id, "status": run.status.value.lower(), "current_step": run.current_step}

    @router.get("/api/v1/analyze/{run_id}/result")
    async def legacy_result(run_id: str, request: Request) -> dict:
        result = await get_result(run_id, request)
        return {
            "trace_id": result.run_id,
            "status": result.status.value.lower(),
            "report": result.report.model_dump(mode="json") if result.report else None,
        }

    return router
