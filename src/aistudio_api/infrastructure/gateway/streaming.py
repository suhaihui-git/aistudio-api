"""Streaming replay workflow for chat completions."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from aistudio_api.config import settings
from aistudio_api.domain.errors import RequestError, classify_error, is_auth_error_body
from aistudio_api.domain.models import parse_chunk_usage
from aistudio_api.infrastructure.gateway.capture import CapturedRequest
from aistudio_api.infrastructure.gateway.request_rewriter import modify_body
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.stream_parser import IncrementalJSONStreamParser, classify_chunk
from aistudio_api.infrastructure.gateway.timeouts import completion_timeout_seconds
from aistudio_api.infrastructure.gateway.wire_types import AistudioContent

logger = logging.getLogger("aistudio")


def _dump_stream_exchange(
    *,
    model: str,
    url: str,
    modified_body: str,
    status_code: int,
    raw_response: str,
) -> None:
    if not settings.dump_raw_response:
        return

    out_dir = Path(settings.dump_raw_response_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_model = model.replace("/", "_")
    timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
    payload = {
        "kind": "stream_generate_content",
        "model": model,
        "url": url,
        "status_code": status_code,
        "modified_body": json.loads(modified_body),
        "raw_response": raw_response,
    }
    path = out_dir / f"aistudio_stream_generate_content_{safe_model}_{timestamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("已落盘流式原始请求/响应: %s", path)


def _summarize_error_body(raw_response: str, limit: int = 500) -> str:
    text = raw_response.strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except json.JSONDecodeError:
        compact = " ".join(text.split())

    if len(compact) > limit:
        return compact[:limit] + "..."
    return compact


def _strip_browser_only_headers(headers: dict[str, str]) -> dict[str, str]:
    skipped = {
        "host",
        "content-length",
        "connection",
        "accept-encoding",
        "cookie",
    }
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in skipped and not key.lower().startswith("sec-")
    }


def _is_browser_network_error(exc: Exception) -> bool:
    return "streaming request failed: network error" in str(exc)


def _extract_cookie_header(cookie: dict) -> str | None:
    name = cookie.get("name")
    value = cookie.get("value")
    if not name or value is None:
        return None
    return f"{name}={value}"


class StreamingGateway:
    def __init__(self, session: BrowserSession | None = None):
        self._session = session

    async def _stream_via_http(
        self,
        *,
        captured: CapturedRequest,
        modified_body: str,
        timeout_ms: int,
    ) -> AsyncGenerator[tuple[str, object | None], None]:
        import aiohttp

        headers = _strip_browser_only_headers(captured.headers)
        if self._session is not None:
            cookies = await self._session.get_cookies()
            cookie_header = "; ".join(
                item for item in (_extract_cookie_header(cookie) for cookie in cookies) if item
            )
            if cookie_header:
                headers["Cookie"] = cookie_header
        headers.setdefault("Origin", "https://aistudio.google.com")
        headers.setdefault("Referer", "https://aistudio.google.com/")

        safe_header_names = sorted(key for key in headers if key.lower() != "cookie")
        logger.info(
            "HTTP 流式回退请求: url=%s, headers=%s, cookies=%s",
            captured.url,
            safe_header_names,
            "yes" if "Cookie" in headers else "no",
        )
        timeout = aiohttp.ClientTimeout(total=timeout_ms / 1000)
        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            async with session.post(captured.url, data=modified_body, headers=headers) as resp:
                logger.info("HTTP 流式回退状态: status=%s", resp.status)
                yield ("status", resp.status)
                saw_chunk = False
                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if not saw_chunk:
                            logger.info("HTTP 流式回退开始接收响应: first_chunk=%s bytes", len(chunk))
                            saw_chunk = True
                        yield ("chunk", chunk)

    async def stream_chat(
        self,
        *,
        captured: CapturedRequest | None,
        model: str,
        system_instruction: str | None,
        contents: list[AistudioContent] | None = None,
        system_instruction_content: AistudioContent | None = None,
        tools: list[list] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        max_tokens: int | None = None,
        generation_config_overrides: dict | None = None,
        sanitize_plain_text: bool = True,
    ) -> AsyncGenerator[tuple[str, object | None], None]:
        if not captured:
            raise ValueError("captured request is required")
        if self._session is None:
            raise RuntimeError("browser session is required for streaming xhr replay")

        modified_body = modify_body(
            captured.body,
            model=model,
            contents=contents,
            system_instruction=system_instruction,
            system_instruction_content=system_instruction_content,
            tools=tools,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            max_tokens=max_tokens,
            generation_config_overrides=generation_config_overrides,
            sanitize_plain_text=sanitize_plain_text,
        )

        parser = IncrementalJSONStreamParser()
        latest_usage: dict | None = None
        raw_parts: list[str] = []
        status_code = 0
        replay_mode = "browser"
        timeout_seconds = completion_timeout_seconds(max_tokens=max_tokens, base_seconds=settings.timeout_stream)

        async def consume_events(events):
            nonlocal latest_usage, status_code
            async for event_type, payload in events:
                if event_type == "status" and payload and not status_code:
                    status_code = int(payload)
                elif event_type == "chunk" and payload:
                    text_payload = payload.decode("utf-8", errors="replace")
                    raw_parts.append(text_payload)
                    for parsed_chunk in parser.feed(text_payload):
                        usage = parse_chunk_usage(parsed_chunk)
                        if usage:
                            latest_usage = usage
                        ctype, text = classify_chunk(parsed_chunk)
                        if ctype in ("body", "thinking", "tool_calls") and text:
                            yield (ctype, text)

        try:
            async for event in consume_events(
                self._session.send_streaming_request(
                    body=modified_body,
                    timeout_ms=timeout_seconds * 1000,
                )
            ):
                yield event
        except RuntimeError as exc:
            if not _is_browser_network_error(exc):
                raise
            logger.warning("浏览器流式重放失败，尝试 HTTP 流式回退: %s", exc)
            parser = IncrementalJSONStreamParser()
            latest_usage = None
            raw_parts.clear()
            status_code = 0
            replay_mode = "http_fallback"
            async for event in consume_events(
                self._stream_via_http(
                    captured=captured,
                    modified_body=modified_body,
                    timeout_ms=timeout_seconds * 1000,
                )
            ):
                yield event

        raw_response = "".join(raw_parts)
        _dump_stream_exchange(
            model=model,
            url=captured.url,
            modified_body=modified_body,
            status_code=status_code,
            raw_response=raw_response,
        )
        if status_code != 200:
            detail = _summarize_error_body(raw_response)
            logger.warning("流式请求失败: mode=%s, status=%s, detail=%s", replay_mode, status_code, detail)
            if status_code in (401, 403, 429):
                raise classify_error(status_code, raw_response)
            if detail:
                raise RequestError(status_code, detail)
            raise RequestError(status_code, "")

        if is_auth_error_body(raw_response):
            raise classify_error(status_code, raw_response)

        if replay_mode == "http_fallback":
            logger.info("HTTP 流式回退成功: chunks=%s chars", len(raw_response))
        yield ("usage", latest_usage)
        yield ("done", None)
