"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from aistudio_api.config import settings
from aistudio_api.infrastructure.security import admin_sessions, api_key_store

from aistudio_api.infrastructure.gateway.client import AIStudioClient

from .state import runtime_state


def get_client() -> AIStudioClient:
    if runtime_state.client is None:
        raise HTTPException(503, detail={"message": "Client not initialized", "type": "service_unavailable"})
    return runtime_state.client


def get_busy_lock():
    if runtime_state.busy_lock is None:
        raise HTTPException(503, detail={"message": "Server not ready", "type": "service_unavailable"})
    return runtime_state.busy_lock


def get_account_service():
    if runtime_state.account_service is None:
        raise HTTPException(503, detail={"message": "Account service not initialized", "type": "service_unavailable"})
    return runtime_state.account_service


def get_runtime_state():
    return runtime_state


def require_admin(request: Request):
    if not settings.admin_password:
        raise HTTPException(503, detail={"message": "AISTUDIO_ADMIN_PASSWORD 未配置", "type": "admin_not_configured"})
    token = request.cookies.get("aistudio_admin")
    if not admin_sessions.verify(token):
        raise HTTPException(401, detail={"message": "需要管理员登录", "type": "unauthorized"})
    return True


def require_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_goog_api_key: str | None = Header(default=None),
):
    raw_key = x_api_key or x_goog_api_key or request.query_params.get("key")
    if not raw_key and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            raw_key = token.strip()
    if not api_key_store.verify_key(raw_key):
        raise HTTPException(401, detail={"message": "无效或缺失 API Key", "type": "unauthorized"})
    return True

