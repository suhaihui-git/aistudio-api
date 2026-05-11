"""账号管理路由。"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aistudio_api.api.dependencies import get_account_service, get_runtime_state, require_admin
from aistudio_api.infrastructure.account.cookie_parser import parse_cookie_string

router = APIRouter(prefix="/accounts", dependencies=[Depends(require_admin)])


class LoginStartRequest(BaseModel):
    name: str | None = None


class LoginStartResponse(BaseModel):
    session_id: str


class AccountResponse(BaseModel):
    id: str
    name: str
    email: str | None
    created_at: str
    last_used: str | None


class LoginStatusResponse(BaseModel):
    session_id: str
    status: str
    account_id: str | None = None
    email: str | None = None
    error: str | None = None


class UpdateAccountRequest(BaseModel):
    name: str | None = None
    email: str | None = None


class ImportCookiesRequest(BaseModel):
    cookies: str  # "key=value; key=value; ..." 格式
    name: str | None = None  # 可选的账号名称
    email: str | None = None  # 可选的邮箱
    account_id: str | None = None  # 可选的账号 ID（覆盖已有账号）


class ImportCookiesResponse(BaseModel):
    account_id: str
    name: str
    cookie_count: int
    domain_summary: dict[str, int]  # domain -> cookie 数量


class ImportAccountRequest(BaseModel):
    package: dict
    preserve_id: bool = False
    name: str | None = None
    email: str | None = None


class ImportAccountResponse(BaseModel):
    account_id: str
    name: str
    email: str | None = None
    cookie_count: int


def _safe_filename_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "account"


@router.post("/login/start", response_model=LoginStartResponse)
async def login_start(
    req: LoginStartRequest,
    account_service=Depends(get_account_service),
):
    """启动 Google 登录流程。"""
    session_id = await account_service.start_login(req.name)
    return LoginStartResponse(session_id=session_id)


@router.get("/login/status/{session_id}", response_model=LoginStatusResponse)
async def login_status(
    session_id: str,
    account_service=Depends(get_account_service),
):
    """查询登录状态。"""
    session = account_service.get_login_status(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="登录会话不存在")
    return LoginStatusResponse(
        session_id=session.session_id,
        status=session.status.value,
        account_id=session.account_id,
        email=session.email,
        error=session.error,
    )


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    account_service=Depends(get_account_service),
):
    """列出所有账号。"""
    accounts = account_service.list_accounts()
    return [
        AccountResponse(
            id=a.id,
            name=a.name,
            email=a.email,
            created_at=a.created_at,
            last_used=a.last_used,
        )
        for a in accounts
    ]


@router.get("/active", response_model=AccountResponse)
async def get_active_account(
    account_service=Depends(get_account_service),
):
    """获取当前活跃账号。"""
    account = account_service.get_active_account()
    if account is None:
        raise HTTPException(status_code=404, detail="没有活跃账号")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
    )


@router.post("/{account_id}/activate", response_model=AccountResponse)
async def activate_account(
    account_id: str,
    account_service=Depends(get_account_service),
    runtime_state=Depends(get_runtime_state),
):
    """切换到指定账号。"""
    # 从 runtime_state 获取 browser_session, snapshot_cache, busy_lock
    browser_session = runtime_state.client._session if runtime_state.client else None
    snapshot_cache = runtime_state.snapshot_cache
    busy_lock = runtime_state.busy_lock

    if browser_session is None:
        raise HTTPException(status_code=503, detail="服务未就绪")

    account = await account_service.activate_account(
        account_id, browser_session, snapshot_cache, busy_lock
    )
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在或切换失败")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
    )


@router.get("/{account_id}/export")
async def export_account(
    account_id: str,
    account_service=Depends(get_account_service),
):
    """导出单个账号。"""
    payload = account_service.export_account(account_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="账号不存在或 auth.json 缺失")

    account = payload["account"]
    filename_base = _safe_filename_part(account.get("email") or account.get("name") or account_id)
    headers = {"Content-Disposition": f'attachment; filename="aistudio-account-{filename_base}.json"'}
    return JSONResponse(payload, headers=headers)


@router.delete("/{account_id}")
async def delete_account(
    account_id: str,
    account_service=Depends(get_account_service),
    runtime_state=Depends(get_runtime_state),
):
    """删除账号。"""
    async def _delete():
        active = account_service.get_active_account()
        success = account_service.delete_account(account_id)
        if not success:
            raise HTTPException(status_code=404, detail="账号不存在")

        rotator = runtime_state.rotator
        if rotator:
            rotator.remove_account(account_id)

        client = runtime_state.client
        if active and active.id == account_id and client and client._session:
            await client._session.switch_auth(None)
            if runtime_state.snapshot_cache is not None:
                runtime_state.snapshot_cache.clear()
        return {"ok": True}

    busy_lock = runtime_state.busy_lock
    if busy_lock is None:
        return await _delete()
    async with busy_lock:
        return await _delete()


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: str,
    req: UpdateAccountRequest,
    account_service=Depends(get_account_service),
):
    """更新账号资料。"""
    if req.name is None and req.email is None:
        raise HTTPException(status_code=400, detail="至少需要提供 name 或 email")
    name = req.name.strip() if req.name is not None else None
    email = req.email.strip() if req.email is not None else None
    if name == "":
        raise HTTPException(status_code=400, detail="账号名称不能为空")
    account = account_service.update_account(account_id, name=name, email=email)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return AccountResponse(
        id=account.id,
        name=account.name,
        email=account.email,
        created_at=account.created_at,
        last_used=account.last_used,
    )


@router.post("/import-cookies", response_model=ImportCookiesResponse)
async def import_cookies(
    req: ImportCookiesRequest,
    account_service=Depends(get_account_service),
):
    """从 cookie 字符串导入账号。

    支持格式: `key=value; key=value; ...`（浏览器开发者工具或 Cookie 编辑扩展导出格式）
    """
    storage_state = parse_cookie_string(req.cookies)
    cookie_count = len(storage_state["cookies"])

    if cookie_count == 0:
        raise HTTPException(status_code=400, detail="未解析到有效 cookie")

    # 统计各域名的 cookie 数量
    domain_summary: dict[str, int] = {}
    for c in storage_state["cookies"]:
        d = c["domain"]
        domain_summary[d] = domain_summary.get(d, 0) + 1

    # 从 cookie 中尝试提取 email（如果有 Gmail 相关信息）
    name = req.name or "导入的账号"

    account = account_service._store.save_account(
        name=name,
        email=req.email,
        storage_state=storage_state,
        account_id=req.account_id,
    )

    return ImportCookiesResponse(
        account_id=account.id,
        name=account.name,
        cookie_count=cookie_count,
        domain_summary=domain_summary,
    )


@router.post("/import-account", response_model=ImportAccountResponse)
async def import_account(
    req: ImportAccountRequest,
    account_service=Depends(get_account_service),
    runtime_state=Depends(get_runtime_state),
):
    """导入单个账号导出包。"""
    try:
        account = account_service.import_account_package(
            req.package,
            preserve_id=req.preserve_id,
            name=req.name,
            email=req.email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    rotator = runtime_state.rotator
    if rotator:
        rotator.add_account(account.id)

    if isinstance(req.package.get("storage_state"), dict):
        storage_state = req.package["storage_state"]
    else:
        storage_state = req.package if isinstance(req.package, dict) else {}
    cookies = storage_state.get("cookies", []) if isinstance(storage_state, dict) else []
    return ImportAccountResponse(
        account_id=account.id,
        name=account.name,
        email=account.email,
        cookie_count=len(cookies) if isinstance(cookies, list) else 0,
    )
