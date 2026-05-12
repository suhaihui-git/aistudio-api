"""Camoufox browser lifecycle management."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import importlib.util
import selectors
from pathlib import Path
from typing import Any, Optional

from aistudio_api.config import settings

logger = logging.getLogger("aistudio.camoufox")
LAUNCHER_PATH = Path(__file__).with_name("camoufox_launcher.py")
PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"


def _is_headed_linux_without_display(headless: bool) -> bool:
    return (
        not headless
        and sys.platform.startswith("linux")
        and not os.getenv("DISPLAY")
        and not os.getenv("WAYLAND_DISPLAY")
    )


class CamoufoxManager:
    def __init__(
        self,
        port: int = 9222,
        auth_profile: Optional[str] = None,
        headless: bool = True,
    ):
        self.port = port
        self.auth_profile = auth_profile
        self.headless = headless
        self._process: Optional[subprocess.Popen] = None
        self._ws_endpoint: Optional[str] = None
        self._browser = None
        self._page = None
        self._playwright = None
        self.python_executable = settings.camoufox_python or sys.executable

    async def start(self) -> str:
        if self._ws_endpoint:
            return self._ws_endpoint

        if _is_headed_linux_without_display(self.headless):
            if os.getenv("AISTUDIO_ENABLE_LOGIN_DESKTOP") == "1":
                raise RuntimeError(
                    "无法启动有头登录浏览器：已启用 AISTUDIO_ENABLE_LOGIN_DESKTOP=1，"
                    "但当前进程没有 DISPLAY。请确认通过 Docker entrypoint 启动，"
                    "或手动启动 Xvfb/VNC 后再运行服务。"
                )
            raise RuntimeError(
                "无法启动有头登录浏览器：当前 Linux 环境没有 DISPLAY/WAYLAND_DISPLAY。"
                "服务器或 Docker 部署请启用内置 noVNC 登录桌面，或配置 VNC/Xvfb 后再使用浏览器登录。"
            )

        try:
            import urllib.request

            resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2)
            data = json.loads(resp.read())
            if "wsEndpointPath" in data:
                self._ws_endpoint = f"ws://127.0.0.1:{self.port}{data['wsEndpointPath']}"
                logger.info("Found existing Camoufox at %s", self._ws_endpoint)
                return self._ws_endpoint
        except Exception:
            pass

        logger.info("Starting Camoufox on port %s...", self.port)
        cmd = [
            self.python_executable,
            str(LAUNCHER_PATH),
            "--port",
            str(self.port),
        ]
        if self.headless:
            cmd.append("--headless")

        env = os.environ.copy()
        python_path_parts = [str(SRC_ROOT)]
        if env.get("PYTHONPATH"):
            python_path_parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        output_parts: list[str] = []
        selector = selectors.DefaultSelector() if os.name != "nt" else None
        if selector is not None and self._process.stdout:
            selector.register(self._process.stdout, selectors.EVENT_READ)

        try:
            for _ in range(30):
                output_parts.extend(self._drain_output(selector))
                time.sleep(1)
                if self._process and self._process.poll() is not None:
                    output_parts.extend(self._drain_output(selector))
                    output = "".join(output_parts)
                    hint = self._build_failure_hint(output)
                    raise RuntimeError(
                        "Camoufox exited before startup. "
                        f"Command: {' '.join(cmd)}. "
                        f"Output: {output.strip() or '<no output>'}. "
                        f"{hint}"
                    )
                try:
                    import urllib.request

                    resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2)
                    data = json.loads(resp.read())
                    if "wsEndpointPath" in data:
                        self._ws_endpoint = f"ws://127.0.0.1:{self.port}{data['wsEndpointPath']}"
                        logger.info("Camoufox started: %s", self._ws_endpoint)
                        return self._ws_endpoint
                except Exception:
                    continue
        finally:
            if selector is not None:
                selector.close()

        output = "".join(output_parts)
        self._terminate_process()
        hint = self._build_failure_hint(output)
        raise RuntimeError(
            "Camoufox failed to start within 30s. "
            f"Command: {' '.join(cmd)}. "
            f"Output: {output.strip() or '<no output>'}. "
            f"{hint}"
        )

    def _drain_output(self, selector) -> list[str]:
        if selector is None:
            return []
        chunks: list[str] = []
        for key, _ in selector.select(timeout=0):
            try:
                line = key.fileobj.readline()
            except Exception:
                continue
            if line:
                chunks.append(line)
        return chunks

    def _terminate_process(self) -> None:
        if not self._process or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)

    def _build_failure_hint(self, output: str) -> str:
        if settings.camoufox_python:
            return (
                f"Check whether AISTUDIO_CAMOUFOX_PYTHON={settings.camoufox_python} "
                "has camoufox installed and can run `-m camoufox.server`."
            )

        current_has_camoufox = importlib.util.find_spec("camoufox.server") is not None
        if not current_has_camoufox:
            return (
                "Current server interpreter does not appear to have camoufox installed. "
                "Run the server with the environment that has camoufox, or set "
                "AISTUDIO_CAMOUFOX_PYTHON to that Python executable."
            )

        if not output.strip():
            return (
                "Camoufox produced no output. Try launching it manually with the same command "
                "to inspect runtime dependencies."
            )

        return "Inspect the command output above for startup failures."

    async def get_page(self):
        if self._page and not self._page.is_closed():
            return self._page

        from playwright.async_api import async_playwright

        if not self._ws_endpoint:
            await self.start()

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.firefox.connect(self._ws_endpoint)
        ctx = self._browser.contexts[0]
        self._page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        return self._page

    async def evaluate(self, js_code: str, timeout: int = 30000) -> Any:
        page = await self.get_page()
        return await page.evaluate(js_code)

    async def navigate(self, url: str):
        page = await self.get_page()
        await page.goto(url, wait_until="networkidle")

    async def fetch_in_browser(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[dict[str, str]] = None,
        body: Optional[str] = None,
    ) -> dict[str, Any]:
        headers_js = json.dumps(headers or {})
        body_js = json.dumps(body) if body else "undefined"

        js_code = f"""(async () => {{
            try {{
                const resp = await fetch({json.dumps(url)}, {{
                    method: {json.dumps(method)},
                    headers: {headers_js},
                    body: {body_js},
                    credentials: 'include',
                }});
                const text = await resp.text();
                return {{status: resp.status, text: text}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        }})()"""

        return await self.evaluate(js_code, timeout=60000)

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        if self._process:
            self._terminate_process()
        self._ws_endpoint = None
        self._browser = None
        self._page = None
