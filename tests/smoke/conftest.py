"""HTTP smoke clients: explicit URLs use deployed services; otherwise run local test apps.

The local fixture uses SQLite and the in-memory queue. PostgreSQL, Redis and
independent processes are covered by the opt-in integration tests / Compose.
"""

import asyncio
import os
from contextlib import asynccontextmanager, suppress

import httpx
import pytest
from fastapi.testclient import TestClient

from opspilot.api.app import create_app
from opspilot.config import RuntimeSettings
from opspilot.persistence import Database
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.execution import RecoverableExecution
from opspilot.runtime.queue import InMemoryRunQueue
from opspilot.runtime.task_manager import TaskManager
from opspilot.runtime.worker import RuntimeWorker
from opspilot.tools import build_default_registry


@pytest.fixture
def agent_client(tmp_path):
    if os.getenv("AGENT_URL"):
        with httpx.Client(base_url=os.environ["AGENT_URL"], timeout=30) as client:
            yield client
        return
    settings = RuntimeSettings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'smoke.db'}", llm_enabled=False)
    database = Database(settings.database_url)
    repository = RuntimeRepository(database.sessions)
    queue = InMemoryRunQueue()
    manager = TaskManager(repository, queue, settings)
    worker = RuntimeWorker(repository, queue, RecoverableExecution(repository, build_default_registry(), settings), settings)
    app = create_app(task_manager=manager)

    @asynccontextmanager
    async def lifespan(app):
        await database.create_schema()
        app.state.task_manager = manager
        task = asyncio.create_task(worker.run_forever())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await database.dispose()

    app.router.lifespan_context = lifespan
    with TestClient(app) as client:
        yield client


@pytest.fixture
def mock_client():
    if os.getenv("MOCK_URL"):
        with httpx.Client(base_url=os.environ["MOCK_URL"].rstrip("/") + "/api/v1/mock", timeout=10) as client:
            yield client
        return
    from deeprca.mock_env import create_mock_router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_mock_router())
    with TestClient(app, base_url="http://testserver/api/v1/mock/") as client:
        yield client


@pytest.fixture
def reset_mock(mock_client):
    mock_client.post("/reset")
    yield
