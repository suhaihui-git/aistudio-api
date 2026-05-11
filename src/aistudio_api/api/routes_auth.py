"""Authentication and API key settings routes."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from aistudio_api.api.dependencies import require_admin
from aistudio_api.api.routes_openai import MODELS
from aistudio_api.config import settings
from aistudio_api.infrastructure.security import admin_sessions, api_key_store

router = APIRouter()


class AdminLoginRequest(BaseModel):
    password: str


class CreateApiKeyRequest(BaseModel):
    name: str = "默认 Key"


@router.get("/auth/status")
async def auth_status(admin=Depends(require_admin)):
    return {"authenticated": True}


@router.post("/auth/login")
async def admin_login(req: AdminLoginRequest, response: Response):
    if not settings.admin_password:
        raise HTTPException(503, detail="AISTUDIO_ADMIN_PASSWORD 未配置")
    if not hmac.compare_digest(req.password, settings.admin_password):
        raise HTTPException(401, detail="密码错误")
    response.set_cookie(
        "aistudio_admin",
        admin_sessions.create(),
        httponly=True,
        samesite="lax",
        max_age=settings.admin_session_ttl_seconds,
    )
    return {"ok": True}


@router.post("/auth/logout")
async def admin_logout(response: Response):
    response.delete_cookie("aistudio_admin")
    return {"ok": True}


@router.get("/settings/api-keys")
async def list_api_keys(admin=Depends(require_admin)):
    return {"keys": api_key_store.list_keys()}


@router.post("/settings/api-keys")
async def create_api_key(req: CreateApiKeyRequest, admin=Depends(require_admin)):
    name = req.name.strip() or "默认 Key"
    return api_key_store.create_key(name)


@router.delete("/settings/api-keys/{key_id}")
async def delete_api_key(key_id: str, admin=Depends(require_admin)):
    if not api_key_store.delete_key(key_id):
        raise HTTPException(404, detail="Key 不存在")
    return {"ok": True}


@router.get("/settings/models")
async def list_admin_models(admin=Depends(require_admin)):
    return {"object": "list", "data": MODELS}
