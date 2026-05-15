"""账号管理应用服务，协调 account_store 和 login_service。"""

from __future__ import annotations

import json
import logging
from typing import Any

from aistudio_api.infrastructure.account.account_store import AccountStore, AccountMeta
from aistudio_api.infrastructure.account.login_service import LoginService, LoginSession

logger = logging.getLogger("aistudio.account")

AUTH_COOKIE_NAMES = {
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
}


class AccountService:
    """账号管理服务。"""

    def __init__(
        self,
        account_store: AccountStore,
        login_service: LoginService,
    ) -> None:
        self._store = account_store
        self._login = login_service

    def list_accounts(self) -> list[AccountMeta]:
        """列出所有账号。"""
        return self._store.list_accounts()

    def get_account(self, account_id: str) -> AccountMeta | None:
        """获取单个账号。"""
        return self._store.get_account(account_id)

    def get_active_account(self) -> AccountMeta | None:
        """获取当前活跃账号。"""
        return self._store.get_active_account()

    def get_active_auth_path(self) -> str | None:
        """获取当前活跃账号的 auth.json 路径。"""
        path = self._store.get_active_auth_path()
        return str(path) if path is not None else None

    def get_active_profile_path(self) -> str | None:
        """获取当前活跃账号的持久化浏览器 Profile 路径。"""
        path = self._store.get_active_profile_path()
        return str(path) if path is not None else None

    async def ensure_active_loaded(
        self,
        browser_session: Any,
        snapshot_cache: Any = None,
        *,
        keep_snapshot_cache: bool = True,
    ) -> AccountMeta | None:
        """确保当前活跃账号已经加载到 BrowserSession。"""
        account = self._store.get_active_account()
        if account is None:
            return None

        auth_path = self._store.get_auth_path(account.id)
        if auth_path is None:
            logger.error("活跃账号 %s 的 auth.json 不存在", account.id)
            return None

        profile_path = self._store.get_profile_path(account.id)
        target_auth = str(auth_path.resolve())
        target_profile = str(profile_path.resolve()) if profile_path is not None else None
        current_auth = getattr(browser_session, "auth_file", None)
        current_profile = getattr(browser_session, "profile_dir", None)
        if current_auth == target_auth and current_profile == target_profile:
            return account

        self._log_cookie_health(auth_path, account.id)
        await browser_session.switch_auth(target_auth, profile_dir=target_profile)
        if not keep_snapshot_cache and snapshot_cache is not None:
            snapshot_cache.clear()
            logger.info("已清除 snapshot 缓存")
        logger.info("已加载活跃账号: %s (%s), profile=%s", account.id, account.name, target_profile or "none")
        return account

    async def ensure_active_loaded_for_pool(
        self,
        client_pool: Any,
        *,
        keep_snapshot_cache: bool = True,
    ) -> AccountMeta | None:
        """确保当前活跃账号已经加载到所有 worker。"""
        account = self._store.get_active_account()
        if account is None:
            return None
        if getattr(client_pool.default_client, "_session", None) is None:
            return account

        auth_path = self._store.get_auth_path(account.id)
        if auth_path is None:
            logger.error("活跃账号 %s 的 auth.json 不存在", account.id)
            return None

        profile_path = self._store.get_profile_path(account.id)
        target_auth = str(auth_path.resolve())
        target_profile = str(profile_path.resolve()) if profile_path is not None else None
        if client_pool.auth_state_matches(target_auth, profile_dir=target_profile):
            return account

        self._log_cookie_health(auth_path, account.id)
        await client_pool.switch_auth(target_auth, profile_dir=target_profile)
        if not keep_snapshot_cache:
            client_pool.clear_snapshot_cache()
            logger.info("已清除 snapshot 缓存")
        logger.info(
            "已加载活跃账号到 worker 池: %s (%s), workers=%s, profile=%s",
            account.id,
            account.name,
            client_pool.size,
            target_profile or "none",
        )
        return account

    def _log_cookie_health(self, auth_path: Any, account_id: str) -> None:
        """记录账号包是否包含常见 Google 登录 cookie，不输出敏感值。"""
        try:
            storage_state = json.loads(auth_path.read_text(encoding="utf-8"))
            cookies = storage_state.get("cookies") if isinstance(storage_state, dict) else []
            if not isinstance(cookies, list):
                cookies = []
            names = {cookie.get("name") for cookie in cookies if isinstance(cookie, dict)}
            domains = {
                cookie.get("domain")
                for cookie in cookies
                if isinstance(cookie, dict) and isinstance(cookie.get("domain"), str)
            }
            present = sorted(name for name in AUTH_COOKIE_NAMES if name in names)
            logger.info(
                "账号 cookie 检查: account=%s, cookies=%d, google_domains=%d, auth_cookies=%s",
                account_id,
                len(cookies),
                len([domain for domain in domains if "google.com" in domain]),
                present,
            )
            if not present:
                logger.warning("账号 %s 未包含常见 Google 登录 cookie，可能会出现 CREDENTIALS_MISSING", account_id)
        except Exception as exc:
            logger.warning("账号 %s cookie 检查失败: %s", account_id, exc)

    async def start_login(self, name: str | None = None, browser_url: str | None = None) -> str:
        """启动登录流程，返回 session_id。"""
        return await self._login.start_login(self._store, name, browser_url=browser_url)

    def get_login_status(self, session_id: str) -> LoginSession | None:
        """获取登录状态。"""
        return self._login.get_status(session_id)

    async def paste_login_text(self, session_id: str, text: str, *, press_enter: bool = False) -> None:
        """向正在登录的远程浏览器焦点输入框输入文本。"""
        await self._login.paste_text(session_id, text, press_enter=press_enter)

    async def activate_account(
        self,
        account_id: str,
        browser_session: Any,
        snapshot_cache: Any,
        busy_lock: Any = None,  # None = skip lock (caller already holds it)
        keep_snapshot_cache: bool = False,
    ) -> AccountMeta | None:
        """切换到指定账号。

        Args:
            account_id: 目标账号 ID
            browser_session: BrowserSession 实例
            snapshot_cache: SnapshotCache 实例
            busy_lock: asyncio.Lock，确保切换时无请求在飞行中。None 则跳过锁
            keep_snapshot_cache: 是否保留 snapshot 缓存（默认 False，避免切号后复用旧 snapshot）

        Returns:
            切换后的账号元数据，或 None（如果账号不存在）
        """
        # 验证账号存在
        account = self._store.get_account(account_id)
        if account is None:
            return None

        async def _do_switch():
            auth_path = self._store.get_auth_path(account_id)
            if auth_path is None:
                logger.error("账号 %s 的 auth.json 不存在", account_id)
                return None
            profile_path = self._store.get_profile_path(account_id)

            await browser_session.switch_auth(
                str(auth_path),
                profile_dir=str(profile_path) if profile_path is not None else None,
            )

            # 切号后默认清理 snapshot，避免旧页面态和新账号 cookies 混用。
            if not keep_snapshot_cache and snapshot_cache is not None:
                snapshot_cache.clear()
                logger.info("已清除 snapshot 缓存")

            # 更新注册表
            self._store.set_active_account(account_id)

            logger.info("已切换到账号: %s (%s)", account_id, account.name)
            return account

        # 获取 busy_lock 确保无请求在飞行中
        if busy_lock is not None:
            async with busy_lock:
                return await _do_switch()
        else:
            return await _do_switch()

    async def activate_account_for_pool(
        self,
        account_id: str,
        client_pool: Any,
        keep_snapshot_cache: bool = False,
    ) -> AccountMeta | None:
        """切换所有 worker 到指定账号。"""
        account = self._store.get_account(account_id)
        if account is None:
            return None

        auth_path = self._store.get_auth_path(account_id)
        if auth_path is None:
            logger.error("账号 %s 的 auth.json 不存在", account_id)
            return None
        profile_path = self._store.get_profile_path(account_id)

        await client_pool.switch_auth(
            str(auth_path),
            profile_dir=str(profile_path) if profile_path is not None else None,
        )
        if not keep_snapshot_cache:
            client_pool.clear_snapshot_cache()
            logger.info("已清除 snapshot 缓存")

        self._store.set_active_account(account_id)
        logger.info("已切换 worker 池到账号: %s (%s), workers=%s", account_id, account.name, client_pool.size)
        return account

    def delete_account(self, account_id: str) -> bool:
        """删除账号。"""
        return self._store.delete_account(account_id)

    def update_account(self, account_id: str, name: str | None = None, email: str | None = None) -> AccountMeta | None:
        """更新账号资料。"""
        return self._store.update_account(account_id, name=name, email=email)

    def export_account(self, account_id: str) -> dict[str, Any] | None:
        """导出单个账号及其 storage state。"""
        account = self._store.get_account(account_id)
        auth_path = self._store.get_auth_path(account_id)
        profile_path = self._store.get_profile_path(account_id)
        if account is None or auth_path is None:
            return None

        storage_state = json.loads(auth_path.read_text(encoding="utf-8"))
        return {
            "version": 1,
            "type": "aistudio-api-account",
            "account": account.to_dict(),
            "storage_state": storage_state,
            "profile": {
                "persistent": profile_path is not None and profile_path.exists(),
            },
        }

    def import_account_package(
        self,
        payload: dict[str, Any],
        *,
        preserve_id: bool = False,
        name: str | None = None,
        email: str | None = None,
    ) -> AccountMeta:
        """导入由 export_account 生成的单账号包。"""
        if not isinstance(payload, dict):
            raise ValueError("账号包格式不正确")

        if isinstance(payload.get("storage_state"), dict):
            storage_state = payload["storage_state"]
        elif isinstance(payload.get("cookies"), list):
            storage_state = {
                "cookies": payload["cookies"],
                "origins": payload.get("origins") if isinstance(payload.get("origins"), list) else [],
            }
        else:
            raise ValueError("账号包缺少 storage_state 或 cookies")

        account_data = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        if not isinstance(storage_state.get("cookies"), list):
            raise ValueError("账号包 storage_state.cookies 格式不正确")

        storage_state = self._normalize_storage_state(storage_state)
        account_id = account_data.get("id") if preserve_id else None
        account_name = name or account_data.get("name") or "导入的账号"
        account_email = email if email is not None else account_data.get("email")
        return self._store.save_account(
            name=account_name,
            email=account_email,
            storage_state=storage_state,
            account_id=account_id,
        )

    def _normalize_storage_state(self, storage_state: dict[str, Any]) -> dict[str, Any]:
        """规范化浏览器扩展导出的 storage_state。"""
        cookies: list[dict[str, Any]] = []
        same_site_map = {
            "none": "None",
            "no_restriction": "None",
            "unspecified": "None",
            "lax": "Lax",
            "strict": "Strict",
        }

        for cookie in storage_state.get("cookies", []):
            if not isinstance(cookie, dict):
                continue
            if not all(cookie.get(key) for key in ("name", "value", "domain")):
                continue

            normalized = dict(cookie)
            same_site = str(normalized.get("sameSite") or "None").lower()
            normalized["sameSite"] = same_site_map.get(same_site, "None")
            normalized["path"] = normalized.get("path") or "/"
            normalized["secure"] = bool(normalized.get("secure", True))
            normalized["httpOnly"] = bool(normalized.get("httpOnly", False))
            normalized["expires"] = normalized.get("expires", -1)
            cookies.append(normalized)

        if not cookies:
            raise ValueError("账号包未包含有效 cookies")

        origins = storage_state.get("origins") if isinstance(storage_state.get("origins"), list) else []
        return {"cookies": cookies, "origins": origins}
