"""Google 账号登录服务，通过有头浏览器完成登录并保存 cookie。"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aistudio_api.config import build_camoufox_proxy, settings

logger = logging.getLogger("aistudio.login")
LOCAL_NOVNC_HOSTS = {"localhost", "127.0.0.1", "::1"}
AI_STUDIO_URL = "https://aistudio.google.com/prompts/new_chat"
AI_STUDIO_URL_FALLBACK = "https://aistudio.google.com/app/prompts/new_chat"
GOOGLE_AUTH_COOKIE_NAMES = {
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
GOOGLE_LOGIN_URL = (
    "https://accounts.google.com/ServiceLogin"
    "?continue=https%3A%2F%2Faistudio.google.com%2Fprompts%2Fnew_chat"
)


class LoginStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LoginSession:
    """登录会话状态。"""
    session_id: str
    status: LoginStatus = LoginStatus.PENDING
    account_id: str | None = None
    email: str | None = None
    error: str | None = None
    browser_url: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    paste_error: str | None = None


class LoginService:
    """Google 账号登录服务。"""

    def __init__(self, port: int = 9223) -> None:
        self._port = port
        self._sessions: dict[str, LoginSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def _generate_session_id(self) -> str:
        return f"login_{secrets.token_hex(8)}"

    async def start_login(
        self,
        account_store: Any,  # AccountStore
        name: str | None = None,
        browser_url: str | None = None,
    ) -> str:
        """启动登录流程，返回 session_id。"""
        session_id = self._generate_session_id()
        session = LoginSession(session_id=session_id)
        session.browser_url = browser_url or settings.login_novnc_url or None
        self._sessions[session_id] = session
        # 启动后台任务
        task = asyncio.create_task(
            self._login_worker(session_id, account_store, name)
        )
        self._tasks[session_id] = task
        return session_id

    def get_status(self, session_id: str) -> LoginSession | None:
        """获取登录状态。"""
        return self._sessions.get(session_id)

    async def paste_text(self, session_id: str, text: str, *, press_enter: bool = False) -> None:
        """Paste text into the active element of a running login browser."""
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError("登录会话不存在")
        if session.status is not LoginStatus.PENDING:
            raise RuntimeError("登录会话已结束")
        page = getattr(session, "_page", None)
        if page is None:
            raise RuntimeError("登录浏览器尚未就绪")
        try:
            focused = await page.evaluate(
                """
                (text) => {
                    const active = document.activeElement;
                    const editable = active && (
                        active.tagName === 'INPUT'
                        || active.tagName === 'TEXTAREA'
                        || active.isContentEditable
                    );
                    if (!editable) return false;
                    active.focus();
                    return true;
                }
                """,
                text,
            )
            if not focused:
                raise RuntimeError("远程浏览器当前没有聚焦的输入框")
            await page.keyboard.insert_text(text)
            if press_enter:
                await page.keyboard.press("Enter")
            session.paste_error = None
        except Exception as exc:
            session.paste_error = str(exc)
            raise

    @staticmethod
    def is_local_novnc_url(url: str | None) -> bool:
        if not url:
            return False
        try:
            host = urlparse(url).hostname
        except Exception:
            return False
        return host in LOCAL_NOVNC_HOSTS

    async def _login_worker(
        self,
        session_id: str,
        account_store: Any,
        name: str | None,
    ) -> None:
        """登录工作协程。"""
        session = self._sessions[session_id]
        login_profile_dir = Path(settings.tmp_dir) / f"aistudio-login-profile-{session_id}"
        camoufox = None
        camoufox_entered = False
        storage_state: dict[str, Any] | None = None
        detected_email: str | None = None
        account_name = name or "Google 账号"
        try:
            # 启动浏览器
            logger.info("启动登录浏览器，使用临时 profile: %s", login_profile_dir)
            from camoufox.async_api import AsyncCamoufox

            login_profile_dir.mkdir(parents=True, exist_ok=True)
            camoufox = AsyncCamoufox(
                headless=False,  # 有头模式，用户需要看到浏览器
                main_world_eval=True,
                proxy=build_camoufox_proxy(settings.proxy_url),
                persistent_context=True,
                user_data_dir=str(login_profile_dir),
                viewport={
                    "width": settings.login_browser_width,
                    "height": settings.login_browser_height,
                },
            )
            context = await camoufox.__aenter__()
            camoufox_entered = True
            page = context.pages[0] if context.pages else await context.new_page()
            setattr(session, "_page", page)
            await page.set_viewport_size(
                {
                    "width": settings.login_browser_width,
                    "height": settings.login_browser_height,
                }
            )

            # 导航到 Google 登录页面
            logger.info("打开 Google 登录页面")
            await page.goto(
                GOOGLE_LOGIN_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )

            # 等待用户完成登录（最多 5 分钟）
            logger.info("等待用户登录...")
            try:
                ready_state = await self._wait_for_aistudio_ready(page, timeout=300)
            except asyncio.TimeoutError as exc:
                logger.warning("登录超时")
                raise RuntimeError("登录超时（5 分钟）") from exc

            detected_email = await self._extract_email_from_page(page)
            logger.info(
                "AI Studio 页面已就绪，准备保存 cookie: url=%s, title=%s",
                ready_state.get("url"),
                ready_state.get("title"),
            )

            # 尝试从 Google 账号页面获取邮箱
            if detected_email is None:
                try:
                    # 导航到 Google 账号页面
                    logger.info("尝试从 Google 账号页面获取邮箱")
                    await page.goto("https://myaccount.google.com", wait_until="networkidle")
                    await asyncio.sleep(2)  # 等待页面加载

                    # 从页面提取邮箱（优先匹配 *@gmail.com）
                    detected_email = await page.evaluate("""
                        () => {
                            const text = document.body.innerText;
                            // 直接匹配 *@gmail.com 邮箱
                            const gmailRegex = /[a-zA-Z0-9._%+-]+@gmail\\.com/g;
                            const matches = text.match(gmailRegex);
                            return matches ? matches[0] : null;
                        }
                    """)
                except Exception as e:
                    logger.warning("从 Google 账号页面获取邮箱失败: %s", e)

            logger.info("登录完成，保存 cookie")
            storage_state = await context.storage_state()
            self._log_storage_state_health(storage_state, "登录浏览器")

            # 如果还是没提取到邮箱，尝试从 storage state 的 origins 中提取
            if detected_email is None:
                try:
                    # 检查 localStorage 中是否有用户信息
                    for origin in storage_state.get("origins", []):
                        for item in origin.get("localStorage", []):
                            if "email" in item.get("name", "").lower():
                                detected_email = item.get("value")
                                break
                        if detected_email:
                            break
                except Exception:
                    pass

            # 保存账号
            account_name = name or detected_email or "Google 账号"
            if detected_email and not name:
                account_name = detected_email

        except Exception as e:
            session.status = LoginStatus.FAILED
            session.error = str(e)
            logger.exception("登录失败")
        finally:
            # 先关闭持久化 context，确保 cookies / indexedDB / session 文件完整落盘。
            try:
                if camoufox_entered and camoufox:
                    await camoufox.__aexit__(None, None, None)
            except Exception:
                pass

        if session.status is LoginStatus.FAILED:
            try:
                shutil.rmtree(login_profile_dir, ignore_errors=True)
            except Exception:
                pass
            if getattr(session, "_page", None) is not None:
                setattr(session, "_page", None)
            self._tasks.pop(session_id, None)
            return

        try:
            if storage_state is None:
                raise RuntimeError("登录完成但未获取到 storage state")
            meta = account_store.save_account(
                name=account_name,
                email=detected_email,
                storage_state=storage_state,
                profile_source=login_profile_dir,
            )

            session.status = LoginStatus.COMPLETED
            session.account_id = meta.id
            session.email = detected_email
            logger.info("账号已保存: %s (%s)", meta.id, detected_email)

        except Exception as e:
            session.status = LoginStatus.FAILED
            session.error = str(e)
            logger.exception("保存登录账号失败")
        finally:
            try:
                shutil.rmtree(login_profile_dir, ignore_errors=True)
            except Exception:
                pass
            # 清理任务引用
            if getattr(session, "_page", None) is not None:
                setattr(session, "_page", None)
            self._tasks.pop(session_id, None)

    async def _wait_for_aistudio_ready(self, page, timeout: int) -> dict[str, Any]:
        """Wait until login reaches a usable AI Studio chat page."""
        deadline = time.monotonic() + timeout
        next_probe_at = time.monotonic() + 8
        last_state: dict[str, Any] = {}

        while time.monotonic() < deadline:
            last_state = await self._read_page_state(page)
            logger.debug("登录页面状态: %s", last_state)
            if self._is_aistudio_ready(last_state):
                return last_state

            url = str(last_state.get("url") or "")
            now = time.monotonic()
            if self._should_probe_aistudio(last_state) and now >= next_probe_at:
                last_state = await self._probe_aistudio_page(page)
                if self._is_aistudio_ready(last_state):
                    return last_state
                next_probe_at = now + 10

            await page.wait_for_timeout(1000)

        raise asyncio.TimeoutError(f"AI Studio 登录未完成，最后页面状态: {last_state}")

    async def _probe_aistudio_page(self, page) -> dict[str, Any]:
        last_state: dict[str, Any] = {}
        for url in (AI_STUDIO_URL, AI_STUDIO_URL_FALLBACK):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            except Exception as exc:
                logger.debug("探测 AI Studio 页面失败 %s: %s", url, exc)
            last_state = await self._read_page_state(page)
            if self._is_aistudio_ready(last_state) or "accounts.google.com" in str(last_state.get("url") or ""):
                return last_state
        return last_state

    async def _read_page_state(self, page) -> dict[str, Any]:
        try:
            return await page.evaluate("""
                () => {
                    const text = document.body?.innerText || '';
                    const title = document.title || '';
                    return {
                        url: location.href,
                        title,
                        hasTextarea: !!document.querySelector('textarea'),
                        hasMakerSuite: !!window.default_MakerSuite,
                        hasRunButton: Array.from(document.querySelectorAll('button'))
                            .some((button) => (button.textContent || '').trim().toLowerCase() === 'run'),
                        textPreview: text.slice(0, 300),
                    };
                }
            """)
        except Exception as exc:
            logger.debug("读取登录页面状态失败: %s", exc)
            return {"url": getattr(page, "url", ""), "title": "", "error": str(exc)}

    def _is_aistudio_ready(self, state: dict[str, Any]) -> bool:
        url = str(state.get("url") or "")
        title = str(state.get("title") or "")
        text = str(state.get("textPreview") or "")
        if "aistudio.google.com" not in url or "accounts.google.com" in url:
            return False
        login_text = f"{title}\n{text}".lower()
        if "sign in" in login_text and not (state.get("hasTextarea") or state.get("hasMakerSuite")):
            return False
        return bool(state.get("hasTextarea") or state.get("hasMakerSuite") or state.get("hasRunButton"))

    def _should_probe_aistudio(self, state: dict[str, Any]) -> bool:
        url = str(state.get("url") or "")
        if "accounts.google.com" in url:
            return False
        if "myaccount.google.com" in url or "aistudio.google.com" in url:
            return True
        if "google.com" in url:
            return False
        return True

    async def _extract_email_from_page(self, page) -> str | None:
        try:
            return await page.evaluate("""
                () => {
                    const selectors = [
                        '[data-email]',
                        '[aria-label*="@"]',
                        'a[href^="mailto:"]',
                    ];
                    for (const selector of selectors) {
                        const el = document.querySelector(selector);
                        if (!el) continue;
                        const value = el.getAttribute('data-email')
                            || el.getAttribute('aria-label')
                            || el.getAttribute('href')
                            || el.textContent
                            || '';
                        const match = value.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/);
                        if (match) return match[0].replace(/^mailto:/, '');
                    }
                    const text = document.body?.innerText || '';
                    const match = text.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/);
                    return match ? match[0] : null;
                }
            """)
        except Exception:
            return None

    def _log_storage_state_health(self, storage_state: dict[str, Any], label: str) -> None:
        cookies = storage_state.get("cookies") if isinstance(storage_state, dict) else []
        if not isinstance(cookies, list):
            cookies = []
        google_cookies = [
            cookie
            for cookie in cookies
            if isinstance(cookie, dict) and "google.com" in str(cookie.get("domain") or "")
        ]
        names = {str(cookie.get("name")) for cookie in google_cookies if cookie.get("name")}
        auth_names = sorted(name for name in GOOGLE_AUTH_COOKIE_NAMES if name in names)
        logger.info(
            "%s storage_state 检查: cookies=%d, google_cookies=%d, auth_cookies=%s",
            label,
            len(cookies),
            len(google_cookies),
            auth_names,
        )
        if not auth_names:
            logger.warning("%s storage_state 未检测到常见 Google 登录 cookie", label)
