"""Camoufox browser lifecycle management."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import importlib.util
from pathlib import Path
from typing import Any, Optional

from aistudio_api.config import settings

logger = logging.getLogger("aistudio.camoufox")
LAUNCHER_PATH = Path(__file__).with_name("camoufox_launcher.py")
PROJECT_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = PROJECT_ROOT / "src"


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

        for _ in range(30):
            time.sleep(1)
            if self._process and self._process.poll() is not None:
                output = ""
                if self._process.stdout:
                    try:
                        output = self._process.stdout.read()
                    except Exception:
                        output = ""
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

        output = ""
        if self._process and self._process.stdout:
            try:
                output = self._process.stdout.read()
            except Exception:
                output = ""
        hint = self._build_failure_hint(output)
        raise RuntimeError(
            "Camoufox failed to start within 30s. "
            f"Command: {' '.join(cmd)}. "
            f"Output: {output.strip() or '<no output>'}. "
            f"{hint}"
        )

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
            self._process.terminate()
        self._ws_endpoint = None
        self._browser = None
        self._page = None
