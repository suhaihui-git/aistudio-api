"""AI Studio browser worker pool."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache
from aistudio_api.infrastructure.gateway.client import AIStudioClient


@dataclass(slots=True)
class AIStudioWorker:
    id: int
    client: AIStudioClient
    snapshot_cache: SnapshotCache


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
        self._available: asyncio.Queue[AIStudioWorker] = asyncio.Queue(maxsize=self.size)
        for worker in self.workers:
            self._available.put_nowait(worker)

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
        snapshot_cache = SnapshotCache()
        client = AIStudioClient(
            port=self.port,
            use_pure_http=self.use_pure_http,
            snapshot_cache=snapshot_cache,
            worker_name=f"worker-{worker_id}",
        )
        return AIStudioWorker(id=worker_id, client=client, snapshot_cache=snapshot_cache)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[AIStudioWorker]:
        worker = await self._available.get()
        try:
            yield worker
        finally:
            self._available.put_nowait(worker)

    async def warmup(self) -> None:
        await asyncio.gather(
            *(worker.client.warmup() for worker in self.workers),
        )

    async def switch_auth(self, auth_file: str | None, profile_dir: str | None = None) -> None:
        await asyncio.gather(
            *(worker.client.switch_auth(auth_file, profile_dir=profile_dir) for worker in self.workers),
        )
        self.clear_snapshot_cache()

    async def reset_auth_state(self) -> None:
        await asyncio.gather(
            *(worker.client.reset_auth_state() for worker in self.workers),
        )

    async def sync_storage_state(self) -> None:
        await self.default_client.sync_storage_state()

    def clear_snapshot_cache(self) -> None:
        for worker in self.workers:
            worker.client.clear_snapshot_cache()
