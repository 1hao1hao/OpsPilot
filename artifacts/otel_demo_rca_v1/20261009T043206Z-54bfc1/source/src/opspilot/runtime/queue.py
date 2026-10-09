"""Queue adapters. Messages contain exactly one opaque run_id."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Protocol

import redis.asyncio as redis


class RunQueue(Protocol):
    async def enqueue(self, run_id: str) -> None: ...

    async def dequeue(self, timeout_seconds: float = 1.0) -> str | None: ...

    async def close(self) -> None: ...


class RedisRunQueue:
    def __init__(self, client: redis.Redis, name: str) -> None:
        self.client = client
        self.name = name

    @classmethod
    def from_url(cls, url: str, name: str) -> RedisRunQueue:
        return cls(redis.from_url(url, decode_responses=True), name)

    async def enqueue(self, run_id: str) -> None:
        await self.client.rpush(self.name, run_id)

    async def dequeue(self, timeout_seconds: float = 1.0) -> str | None:
        timeout = max(1, int(timeout_seconds))
        message = await self.client.blpop(self.name, timeout=timeout)
        return message[1] if message else None

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

    async def dequeue(self, timeout_seconds: float = 1.0) -> str | None:
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
        return value

    async def close(self) -> None:
        return None
