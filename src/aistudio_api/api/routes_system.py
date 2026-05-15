"""System and metadata routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from aistudio_api.application.api_service import health_response, stats_response
from aistudio_api.api.dependencies import get_runtime_state, require_admin
from aistudio_api.api.state import ExclusiveSlotTimeout

router = APIRouter()


@router.get("/health")
async def health():
    return health_response()


@router.get("/stats")
async def stats(admin=Depends(require_admin)):
    return stats_response()


# ========== 轮询管理 API ==========

class RotationModeRequest(BaseModel):
    mode: str  # round_robin, lru, least_rl
    cooldown_seconds: int | None = None


@router.get("/rotation")
async def get_rotation_status(runtime_state=Depends(get_runtime_state), admin=Depends(require_admin)):
    """获取轮询状态。"""
    rotator = runtime_state.rotator
    if rotator is None:
        return {"enabled": False, "message": "轮询器未初始化"}

    return {
        "enabled": True,
        "mode": rotator.mode.value,
        "cooldown_seconds": rotator.cooldown_seconds,
        "accounts": rotator.get_all_stats(),
    }


@router.post("/rotation/mode")
async def set_rotation_mode(
    req: RotationModeRequest,
    runtime_state=Depends(get_runtime_state),
    admin=Depends(require_admin),
):
    """设置轮询模式。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="轮询器未初始化")

    try:
        from aistudio_api.application.account_rotator import RotationMode
        rotator.mode = RotationMode(req.mode)
        if req.cooldown_seconds is not None:
            rotator.cooldown_seconds = req.cooldown_seconds
        return {
            "ok": True,
            "mode": rotator.mode.value,
            "cooldown_seconds": rotator.cooldown_seconds,
        }
    except ValueError:
        raise HTTPException(400, detail=f"无效的轮询模式: {req.mode}，可选: round_robin, lru, least_rl")


@router.get("/rotation/accounts")
async def get_rotation_accounts(runtime_state=Depends(get_runtime_state), admin=Depends(require_admin)):
    """获取所有账号的轮询统计。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="轮询器未初始化")

    return rotator.get_all_stats()


@router.post("/rotation/next")
async def force_next_account(runtime_state=Depends(get_runtime_state), admin=Depends(require_admin)):
    """强制切换到下一个可用账号。"""
    rotator = runtime_state.rotator
    if rotator is None:
        raise HTTPException(503, detail="轮询器未初始化")

    # 获取下一个账号
    next_account = await rotator.get_next_account()
    if next_account is None:
        raise HTTPException(404, detail="没有可用的账号")

    # 切换账号
    account_service = runtime_state.account_service
    client_pool = runtime_state.client_pool
    client = runtime_state.client
    if not account_service or (client_pool is None and client is None):
        raise HTTPException(503, detail="服务未就绪")

    try:
        async with runtime_state.exclusive_slot():
            if client_pool is not None:
                result = await account_service.activate_account_for_pool(
                    next_account.id,
                    client_pool,
                    keep_snapshot_cache=False,
                )
            else:
                result = await account_service.activate_account(
                    next_account.id,
                    client._session,
                    runtime_state.snapshot_cache,
                    None,
                    keep_snapshot_cache=False,
                )
    except ExclusiveSlotTimeout as exc:
        raise HTTPException(409, detail="当前有请求运行，请稍后重试") from exc

    if result is None:
        raise HTTPException(500, detail="切换失败")

    return {
        "ok": True,
        "account": {
            "id": result.id,
            "name": result.name,
            "email": result.email,
        },
    }

