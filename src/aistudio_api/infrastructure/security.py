"""Local admin session and API key management."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aistudio_api.config import settings


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class ApiKeyRecord:
    id: str
    name: str
    key_hash: str
    prefix: str
    created_at: int
    last_used_at: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "prefix": self.prefix,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


class ApiKeyStore:
    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or settings.api_keys_file)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[ApiKeyRecord]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return [ApiKeyRecord(**item) for item in data.get("keys", [])]

    def _save(self, records: list[ApiKeyRecord]) -> None:
        payload = {"keys": [record.__dict__ for record in records]}
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_keys(self) -> list[dict[str, Any]]:
        return [record.to_public_dict() for record in self._load()]

    def create_key(self, name: str) -> dict[str, Any]:
        raw_key = "sk-aistudio-" + secrets.token_urlsafe(32)
        records = self._load()
        record = ApiKeyRecord(
            id="key_" + secrets.token_hex(6),
            name=name,
            key_hash=_sha256(raw_key),
            prefix=raw_key[:18],
            created_at=int(time.time()),
        )
        records.append(record)
        self._save(records)
        data = record.to_public_dict()
        data["key"] = raw_key
        return data

    def delete_key(self, key_id: str) -> bool:
        records = self._load()
        kept = [record for record in records if record.id != key_id]
        if len(kept) == len(records):
            return False
        self._save(kept)
        return True

    def verify_key(self, raw_key: str | None) -> bool:
        if not raw_key:
            return False
        key_hash = _sha256(raw_key)
        records = self._load()
        changed = False
        now = int(time.time())
        valid = False
        for record in records:
            if hmac.compare_digest(record.key_hash, key_hash):
                record.last_used_at = now
                changed = True
                valid = True
                break
        if changed:
            self._save(records)
        return valid


api_key_store = ApiKeyStore()


class AdminSessionManager:
    def __init__(self) -> None:
        self._secret = settings.admin_session_secret or secrets.token_hex(32)

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def create(self) -> str:
        expires = int(time.time()) + settings.admin_session_ttl_seconds
        nonce = secrets.token_hex(12)
        payload = f"{expires}:{nonce}"
        return f"{payload}:{self._sign(payload)}"

    def verify(self, token: str | None) -> bool:
        if not token:
            return False
        parts = token.split(":")
        if len(parts) != 3:
            return False
        expires_text, nonce, signature = parts
        payload = f"{expires_text}:{nonce}"
        if not hmac.compare_digest(signature, self._sign(payload)):
            return False
        try:
            expires = int(expires_text)
        except ValueError:
            return False
        return expires >= int(time.time())


admin_sessions = AdminSessionManager()

