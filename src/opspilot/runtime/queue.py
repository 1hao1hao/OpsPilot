"""Queue adapters. Messages contain exactly one opaque run_id."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class RunDelivery:
    run_id: str
    message_id: str


class RunQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...

    async def dequeue(self, timeout_seconds: float = 1.0) -> RunDelivery | None: ...

    async def ack(self, delivery: RunDelivery) -> None: ...

    async def close(self) -> None: ...


class RedisRunQueue:
    def __init__(self, client: redis.Redis, name: str, *, group: str = "workers",
                 consumer: str | None = None, pending_seconds: float = 30.0) -> None:
        self.client = client
        self.name = name
        self.group = group
        self.consumer = consumer or f"worker-{uuid.uuid4().hex}"
        self.pending_ms = max(1, int(pending_seconds * 1000))
        self._group_ready = False

    @classmethod
    def from_url(cls, url: str, name: str, *, pending_seconds: float = 30.0) -> RedisRunQueue:
        return cls(redis.from_url(url, decode_responses=True), name, pending_seconds=pending_seconds)

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            await self.client.xgroup_create(self.name, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "WRONGTYPE" in str(exc):
                raise RuntimeError(
                    "Redis queue key is a legacy List; use a fresh OPSPILOT_QUEUE_NAME for Streams"
                ) from exc
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def enqueue(self, run_id: str) -> None:
        await self._ensure_group()
        await self.client.xadd(self.name, {"run_id": run_id})

    async def dequeue(self, timeout_seconds: float = 1.0) -> RunDelivery | None:
        await self._ensure_group()
        claimed = await self.client.xautoclaim(
            self.name, self.group, self.consumer, min_idle_time=self.pending_ms,
            start_id="0-0", count=1,
        )
        if claimed[1]:
            message_id, fields = claimed[1][0]
            return RunDelivery(fields["run_id"], message_id)
        batches = await self.client.xreadgroup(
            self.group, self.consumer, {self.name: ">"}, count=1,
            block=max(1, int(timeout_seconds * 1000)),
        )
        if not batches:
            return None
        message_id, fields = batches[0][1][0]
        return RunDelivery(fields["run_id"], message_id)

    async def ack(self, delivery: RunDelivery) -> None:
        await self.client.xack(self.name, self.group, delivery.message_id)
        await self.client.xdel(self.name, delivery.message_id)

    async def close(self) -> None:
        await self.client.aclose()


class InMemoryRunQueue:
    """Deterministic queue for unit/contract tests; it is never a state store."""

    def __init__(self) -> None:
        self.messages: deque[str] = deque()
        self._available = asyncio.Event()

    async def enqueue(self, run_id: str) -> None:
        self.messages.append(run_id)
        self._available.set()

    async def dequeue(self, timeout_seconds: float = 1.0) -> RunDelivery | None:
        if not self.messages:
            try:
                await asyncio.wait_for(self._available.wait(), timeout_seconds)
            except TimeoutError:
                return None
        if not self.messages:
            return None
        value = self.messages.popleft()
        if not self.messages:
            self._available.clear()
        return RunDelivery(value, value)

    async def ack(self, delivery: RunDelivery) -> None:
        return None

    async def close(self) -> None:
        return None
