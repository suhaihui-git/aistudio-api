"""Pure HTTP request capture — no UI operations.

Uses chromium_botguard.py to generate BotGuard snapshots,
then builds the request body manually.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from aistudio_api.config import DEFAULT_TEXT_MODEL
from aistudio_api.infrastructure.cache.snapshot_cache import SnapshotCache

logger = logging.getLogger("aistudio")


@dataclass
class CapturedRequest:
    url: str
    headers: dict[str, str]
    body: str
    model: str = ""
    snapshot: str = ""

    def __post_init__(self):
        parsed = json.loads(self.body)
        self.model = parsed[0] if parsed else ""
        self.snapshot = parsed[4] if len(parsed) > 4 and isinstance(parsed[4], str) else ""


# Default API endpoint
TARGET_HOST = "alkalimakersuite-pa.clients6.google.com"
DEFAULT_URL = f"https://{TARGET_HOST}/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent"


def compute_content_hash(prompt: str) -> str:
    """Compute SHA-256 hash of prompt content."""
    return hashlib.sha256(prompt.encode()).hexdigest()


class PureHttpCaptureService:
    """Capture service that doesn't need UI operations."""

    def __init__(self, snapshot_cache: SnapshotCache):
        self._snapshot_cache = snapshot_cache

    async def capture(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        images: list[str] | None = None,
        contents=None,
        force_refresh: bool = False,
    ) -> CapturedRequest | None:
        # Check cache first
        if not images and not force_refresh:
            cached = self._snapshot_cache.get(prompt, model=model)
            if cached:
                _snapshot, url, headers, body = cached
                return CapturedRequest(url=url, headers=headers, body=body)

        # Generate snapshot via chromium_botguard.py (no UI)
        snapshot = await self._generate_snapshot(prompt)
        if not snapshot:
            logger.error("Failed to generate snapshot")
            return None

        # Build request body manually
        body = self._build_request_body(model, prompt, snapshot, images)
        
        # Build headers (will be filled by the caller with fresh SAPISIDHASH)
        headers = self._build_headers()

        # Cache the result
        self._snapshot_cache.put(prompt, snapshot, DEFAULT_URL, headers, body, model=model)

        return CapturedRequest(
            url=DEFAULT_URL,
            headers=headers,
            body=body,
            model=model,
            snapshot=snapshot,
        )

    async def _generate_snapshot(self, prompt: str) -> Optional[str]:
        """Generate BotGuard snapshot using chromium_botguard.py."""
        try:
            # Import from project root
            import sys
            from pathlib import Path
            project_root = Path(__file__).resolve().parents[4]
            sys.path.insert(0, str(project_root))
            from chromium_botguard import generate_snapshot
            
            content_hash = compute_content_hash(prompt)
            logger.info("Generating snapshot for hash: %s", content_hash[:16])
            
            result = await generate_snapshot(content_hash)
            snapshot = result.get("snapshot")
            
            if snapshot:
                logger.info("Snapshot generated: %d chars", len(snapshot))
                return snapshot
            else:
                logger.error("No snapshot in result: %s", result)
                return None
        except Exception as e:
            logger.error("Snapshot generation failed: %s", e, exc_info=True)
            return None

    def _build_request_body(
        self,
        model: str,
        prompt: str,
        snapshot: str,
        images: Optional[list[str]] = None,
    ) -> str:
        """Build AI Studio request body manually.

        Format: [model, [[[[null, "text"]], "user"]], null, null, snapshot]
        """
        # Build part: [null, "text"]
        part = [None, prompt]
        
        # Build parts array: [[null, "text"]]
        parts = [part]
        
        # Build content: [[parts], "user"]
        content = [parts, "user"]
        
        # Build contents: [content]
        contents = [content]
        
        # Build the full request body
        body = [
            model,       # 0: model name
            contents,    # 1: contents
            None,        # 2: system instruction
            None,        # 3: generation config
            snapshot,    # 4: BotGuard snapshot
        ]

        return json.dumps(body, ensure_ascii=False)

    def _build_headers(self) -> dict[str, str]:
        """Build default headers (SAPISIDHASH will be added by caller)."""
        return {
            "Content-Type": "application/json+protobuf",
            "X-User-Agent": "grpc-web-javascript/0.1",
            "X-Goog-AuthUser": "0",
            "X-Goog-Ext-519733851-bin": "CAASAUIwATgEQABQBFgDYgJVUw==", # TODO, 这个有含义
            "Origin": "https://aistudio.google.com",
            "Referer": "https://aistudio.google.com/",
        }
