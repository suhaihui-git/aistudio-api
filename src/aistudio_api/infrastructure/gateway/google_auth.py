"""Helpers for replaying Google web auth headers without leaking secrets."""

from __future__ import annotations

import hashlib
import time
from http.cookies import SimpleCookie
from urllib.parse import urlparse


GOOGLE_AUTH_ORIGIN = "https://aistudio.google.com"


def _cookie_domain_matches(host: str, domain: str) -> bool:
    normalized = domain.lstrip(".").lower()
    return bool(normalized) and (host == normalized or host.endswith(f".{normalized}"))


def cookies_for_url(cookies: list[dict], url: str) -> list[dict]:
    """Return the subset of Playwright cookies a browser would send to url."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    is_https = parsed.scheme == "https"
    now = int(time.time())
    matched: list[dict] = []

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        domain = str(cookie.get("domain") or "")
        cookie_path = str(cookie.get("path") or "/")
        expires = cookie.get("expires", -1)
        if not name or value is None or not _cookie_domain_matches(host, domain):
            continue
        if not path.startswith(cookie_path.rstrip("/") or "/"):
            continue
        if bool(cookie.get("secure")) and not is_https:
            continue
        if isinstance(expires, (int, float)) and expires > 0 and expires <= now:
            continue
        matched.append(cookie)

    return matched


def build_cookie_header(cookies: list[dict], url: str) -> str:
    """Build a Cookie header using only cookies matching the target URL."""
    pairs: list[str] = []
    for cookie in cookies_for_url(cookies, url):
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        morsel = SimpleCookie()
        morsel[str(name)] = str(value)
        pairs.append(morsel.output(header="").strip())
    return "; ".join(pairs)


def _hash_auth_cookie(value: str, origin: str, timestamp: int) -> str:
    digest = hashlib.sha1(f"{timestamp} {value} {origin}".encode("utf-8")).hexdigest()
    return f"{timestamp}_{digest}"


def build_authorization_header(
    cookies: list[dict],
    *,
    origin: str = GOOGLE_AUTH_ORIGIN,
    timestamp: int | None = None,
) -> str | None:
    """Build a fresh Google SAPISID authorization header from current cookies."""
    values = {
        str(cookie.get("name")): str(cookie.get("value"))
        for cookie in cookies
        if isinstance(cookie, dict) and cookie.get("name") and cookie.get("value") is not None
    }
    timestamp = int(time.time()) if timestamp is None else timestamp
    parts: list[str] = []

    sapisid = values.get("SAPISID")
    if sapisid:
        parts.extend(["SAPISIDHASH", _hash_auth_cookie(sapisid, origin, timestamp)])

    one_papisid = values.get("__Secure-1PAPISID") or sapisid
    if one_papisid:
        parts.extend(["SAPISID1PHASH", _hash_auth_cookie(one_papisid, origin, timestamp)])

    three_papisid = values.get("__Secure-3PAPISID") or sapisid
    if three_papisid:
        parts.extend(["SAPISID3PHASH", _hash_auth_cookie(three_papisid, origin, timestamp)])

    return " ".join(parts) if parts else None


def authorization_header_kinds(header: str | None) -> list[str]:
    if not header:
        return []
    tokens = header.split()
    return [tokens[i] for i in range(0, len(tokens), 2)]
