"""Short-lived HTTP gateway; workflow execution only exists in the worker."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from opspilot.api.routes.runs import create_runs_router
from opspilot.config import RuntimeSettings
from opspilot.persistence import Database
from opspilot.persistence.repositories import RuntimeRepository
from opspilot.runtime.queue import RedisRunQueue
from opspilot.runtime.task_manager import TaskManager


def create_app(*, task_manager: TaskManager | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if task_manager is not None:
            app.state.task_manager = task_manager
            yield
            return
        settings = RuntimeSettings()
        database = Database(settings.database_url)
        queue = RedisRunQueue.from_url(settings.redis_url, settings.queue_name)
        app.state.task_manager = TaskManager(RuntimeRepository(database.sessions), queue, settings)
        app.state.database = database
        try:
            yield
        finally:
            await queue.close()
            await database.dispose()

    app = FastAPI(title="OpsPilot Runtime", version="0.4.0", lifespan=lifespan)
    app.include_router(create_runs_router())

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy", "component": "api"}

    return app


app = create_app()
