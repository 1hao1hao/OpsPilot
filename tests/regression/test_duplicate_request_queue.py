"""Regression: concurrent request retries must not amplify queue messages."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from opspilot.config import RuntimeSettings
from opspilot.models import AlertEvent
from opspilot.persistence import Database
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.queue import InMemoryRunQueue
from opspilot.runtime.task_manager import TaskManager


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_enqueues_exactly_once(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'duplicate.db'}")
    await database.create_schema()
    queue = InMemoryRunQueue()
    manager = TaskManager(RuntimeRepository(database.sessions), queue, RuntimeSettings())
    alert = AlertEvent(
        alert_id="duplicate-regression",
        service_name="checkout-service",
        alert_type="timeout",
        severity="P1",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        signals={"db": {"replication_lag_seconds": 10}},
    )
    try:
        runs = await asyncio.gather(
            *(manager.create_run(request_id="same-retry", alert=alert) for _ in range(12))
        )
        assert len({item.run_id for item in runs}) == 1
        assert list(queue.messages) == [runs[0].run_id]
    finally:
        await database.dispose()
