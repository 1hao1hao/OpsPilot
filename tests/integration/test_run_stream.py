"""The WS endpoint serializes the same persisted events as the event resource."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.models import RunStatus, RuntimeEventView


def test_stream_returns_ordered_events_and_closes_for_terminal_run():
    events = [
        RuntimeEventView(sequence=i, event_type=kind, status=None, step_name=None, created_at=datetime.now(UTC))
        for i, kind in enumerate(("run.created", "investigation.gate", "run.succeeded"), 1)
    ]

    class Manager:
        async def get_events(self, run_id, *, after=0):
            return [event for event in events if event.sequence > after] if run_id == "run-1" else None

        async def get_run(self, run_id):
            return SimpleNamespace(status=RunStatus.SUCCEEDED)

    with TestClient(create_app(task_manager=Manager())) as client:
        response = client.get("/api/v1/runs/run-1/events").json()
        with client.websocket_connect("/api/v1/runs/run-1/stream") as socket:
            assert [socket.receive_json() for _ in events] == response
            assert socket.receive()["type"] == "websocket.close"
        with client.websocket_connect("/api/v1/runs/missing/stream") as socket:
            assert socket.receive_json() == {"event": "error", "detail": "run not found"}
            assert socket.receive()["type"] == "websocket.close"
