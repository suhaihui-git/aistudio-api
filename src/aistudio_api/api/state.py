"""Shared API runtime state."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aistudio_api.config import settings
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.client_pool import AIStudioClientPool


class ExclusiveSlotTimeout(RuntimeError):
    """Raised when an exclusive account operation cannot drain active requests."""


@dataclass
class RuntimeState:
    client: AIStudioClient | None = None
    client_pool: AIStudioClientPool | None = None
    busy_lock: asyncio.Semaphore | None = None
    state_lock: asyncio.Lock | None = None
    max_concurrency: int = 1
    single_account_max_concurrency: int = 1
    camoufox_port: int = 9222
    snapshot_cache: object | None = None  # SnapshotCache 实例
    account_service: object | None = None  # AccountService 实例
    rotator: object | None = None  # AccountRotator 实例
    model_stats: dict[str, dict] = field(
        default_factory=lambda: defaultdict(
            lambda: {
                "requests": 0,
                "success": 0,
                "rate_limited": 0,
                "errors": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "last_used": None,
            }
        )
    )

    def record(self, model: str, event: str, usage: dict | None = None):
        stats = self.model_stats[model]
        stats["requests"] += 1
        stats[event] += 1
        stats["last_used"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        if usage and event == "success":
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            tt = usage.get("total_tokens", 0)
            stats["prompt_tokens"] += pt if isinstance(pt, int) else 0
            stats["completion_tokens"] += ct if isinstance(ct, int) else 0
            stats["total_tokens"] += tt if isinstance(tt, int) else 0

    @asynccontextmanager
    async def request_slot(self) -> AsyncIterator[None]:
        """Acquire one request slot while respecting pending exclusive account operations."""
        if self.busy_lock is None:
            raise RuntimeError("Server not ready")

        if self.state_lock is None:
            await self.busy_lock.acquire()
        else:
            async with self.state_lock:
                await self.busy_lock.acquire()
        try:
            yield
        finally:
            self.busy_lock.release()

    @asynccontextmanager
    async def client_slot(self) -> AsyncIterator[AIStudioClient]:
        if self.client_pool is None:
            if self.client is None:
                raise RuntimeError("Client not initialized")
            yield self.client
            return

        async with self.client_pool.acquire() as worker:
            yield worker.client

    @asynccontextmanager
    async def exclusive_slot(self, timeout_seconds: float | None = None) -> AsyncIterator[None]:
        """Drain all request slots before mutating account/profile browser state."""
        if self.busy_lock is None:
            yield
            return

        permits = max(1, self.max_concurrency)
        acquired = 0
        lock = self.state_lock
        if lock is None:
            lock = asyncio.Lock()
            self.state_lock = lock
        timeout = settings.account_operation_timeout if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout if timeout and timeout > 0 else None

        lock_acquired = False
        try:
            if deadline is None:
                await lock.acquire()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ExclusiveSlotTimeout("当前有请求运行，请稍后重试")
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise ExclusiveSlotTimeout("当前有请求运行，请稍后重试") from exc
            lock_acquired = True

            try:
                for _ in range(permits):
                    if deadline is None:
                        await self.busy_lock.acquire()
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ExclusiveSlotTimeout("当前有请求运行，请稍后重试")
                        try:
                            await asyncio.wait_for(self.busy_lock.acquire(), timeout=remaining)
                        except asyncio.TimeoutError as exc:
                            raise ExclusiveSlotTimeout("当前有请求运行，请稍后重试") from exc
                    acquired += 1
                yield
            finally:
                for _ in range(acquired):
                    self.busy_lock.release()
        finally:
            if lock_acquired:
                lock.release()


runtime_state = RuntimeState()

