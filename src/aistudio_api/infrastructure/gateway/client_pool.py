"""AI Studio browser worker pool."""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from aistudio_api.config import settings
from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@dataclass(slots=True)
class AIStudioWorker:
    id: int
    client: AIStudioClient
    snapshot_cache: SnapshotCache
    in_use: bool = False
    last_used: float = field(default_factory=time.monotonic)


class AIStudioClientPool:
    """A small pool of independent browser-backed clients.

    Each worker owns a separate BrowserSession and hook page. Requests acquire one
    worker for their entire lifecycle, including streaming responses.
    """

    def __init__(
        self,
        *,
        size: int,
        port: int,
        use_pure_http: bool = False,
    ) -> None:
        self.size = max(1, size)
        self.port = port
        self.use_pure_http = use_pure_http
        self.workers: list[AIStudioWorker] = [
            self._build_worker(i + 1)
            for i in range(self.size)
        ]
        self._available: deque[AIStudioWorker] = deque(self.workers)
        self._condition = asyncio.Condition()
        self._lifecycle_lock = asyncio.Lock()
        self._account_switch_pending = False
        self._maintenance_task: asyncio.Task | None = None

    @property
    def default_client(self) -> AIStudioClient:
        return self.workers[0].client

    @property
    def snapshot_cache(self) -> SnapshotCache:
        return self.workers[0].snapshot_cache

    def auth_state_matches(self, auth_file: str | None, profile_dir: str | None = None) -> bool:
        return all(
            worker.client.auth_state_matches(auth_file, profile_dir=profile_dir)
            for worker in self.workers
        )

    def _build_worker(self, worker_id: int) -> AIStudioWorker:
        per_worker_cache_size = max(1, math.ceil(settings.snapshot_cache_max / self.size))
        snapshot_cache = SnapshotCache(max_size=per_worker_cache_size)
        client = AIStudioClient(
            port=self.port,
            use_pure_http=self.use_pure_http,
            snapshot_cache=snapshot_cache,
            worker_name=f"worker-{worker_id}",
        )
        return AIStudioWorker(id=worker_id, client=client, snapshot_cache=snapshot_cache)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[AIStudioWorker]:
        async with self._condition:
            while not self._available:
                await self._condition.wait()
            worker = self._available.popleft()
            worker.in_use = True
        try:
            yield worker
        finally:
            worker.in_use = False
            worker.last_used = time.monotonic()
            async with self._condition:
                self._available.append(worker)
                self._condition.notify()

    async def warmup(self, workers: int | None = None) -> None:
        limit = self.size if workers is None else max(0, min(self.size, workers))
        if limit <= 0:
            return
        async with self._lifecycle_lock:
            if self._account_switch_pending:
                return
            selected: list[AIStudioWorker] = []
            async with self._condition:
                for worker in list(self._available):
                    if len(selected) >= limit:
                        break
                    self._available.remove(worker)
                    worker.in_use = True
                    selected.append(worker)

            try:
                await asyncio.gather(
                    *(worker.client.warmup() for worker in selected),
                )
            finally:
                now = time.monotonic()
                for worker in selected:
                    worker.in_use = False
                    worker.last_used = now
                    async with self._condition:
                        self._available.append(worker)
                        self._condition.notify()

    def _storage_sync_worker_id(self) -> int | None:
        open_workers = [
            worker
            for worker in self.workers
            if worker.client.browser_open
        ]
        if not open_workers:
            return None
        return max(open_workers, key=lambda worker: worker.last_used).id

    async def switch_auth(self, auth_file: str | None, profile_dir: str | None = None) -> None:
        self._account_switch_pending = True
        try:
            async with self._lifecycle_lock:
                sync_worker_id = self._storage_sync_worker_id()
                await asyncio.gather(
                    *(
                        worker.client.switch_auth(
                            auth_file,
                            profile_dir=profile_dir,
                            sync_storage=(worker.id == sync_worker_id),
                        )
                        for worker in self.workers
                    ),
                )
                self.clear_snapshot_cache()
        finally:
            self._account_switch_pending = False

    async def reset_auth_state(self) -> None:
        async with self._lifecycle_lock:
            sync_worker_id = self._storage_sync_worker_id()
            await asyncio.gather(
                *(
                    worker.client.reset_auth_state(sync_storage=(worker.id == sync_worker_id))
                    for worker in self.workers
                ),
            )

    async def sync_storage_state(self) -> None:
        worker = max(
            (worker for worker in self.workers if worker.client.browser_open),
            key=lambda worker: worker.last_used,
            default=self.workers[0],
        )
        await worker.client.sync_storage_state()

    def clear_snapshot_cache(self) -> None:
        for worker in self.workers:
            worker.client.clear_snapshot_cache()

    def start_maintenance(self) -> None:
        if self._maintenance_task is None or self._maintenance_task.done():
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def close(self) -> None:
        if self._maintenance_task and not self._maintenance_task.done():
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        async with self._lifecycle_lock:
            await asyncio.gather(
                *(
                    worker.client.close(release_memory=True, sync_storage=worker.id == 1)
                    for worker in self.workers
                ),
                return_exceptions=True,
            )

    def stats(self) -> dict:
        available = sum(1 for worker in self.workers if not worker.in_use)
        open_workers = sum(1 for worker in self.workers if worker.client.browser_open)
        return {
            "size": self.size,
            "active": self.size - available,
            "available": available,
            "browser_open": open_workers,
            "idle_timeout_seconds": settings.browser_idle_timeout_seconds,
        }

    async def _maintenance_loop(self) -> None:
        interval = settings.browser_idle_check_interval_seconds
        while True:
            await asyncio.sleep(interval)
            await self.close_idle_workers(settings.browser_idle_timeout_seconds)

    async def close_idle_workers(self, idle_seconds: int) -> None:
        if idle_seconds <= 0:
            return
        now = time.monotonic()
        candidates: list[AIStudioWorker] = []
        async with self._lifecycle_lock:
            if self._account_switch_pending:
                return
            async with self._condition:
                for worker in list(self._available):
                    if now - worker.last_used < idle_seconds:
                        continue
                    self._available.remove(worker)
                    worker.in_use = True
                    candidates.append(worker)

            for worker in candidates:
                try:
                    await worker.client.close(release_memory=True, sync_storage=worker.id == 1)
                finally:
                    worker.in_use = False
                    worker.last_used = time.monotonic()
                    async with self._condition:
                        self._available.append(worker)
                        self._condition.notify()
