import hashlib

from aistudio_api.infrastructure.gateway.google_auth import (
    build_authorization_header,
    build_cookie_header,
    cookies_for_url,
)
from aistudio_api.infrastructure.gateway.streaming import _strip_browser_only_headers


def test_build_authorization_header_uses_current_google_cookies():
    cookies = [
        {"name": "SAPISID", "value": "sapisid-value", "domain": ".google.com", "path": "/", "secure": True},
        {"name": "__Secure-1PAPISID", "value": "one-value", "domain": ".google.com", "path": "/", "secure": True},
        {"name": "__Secure-3PAPISID", "value": "three-value", "domain": ".google.com", "path": "/", "secure": True},
    ]
    header = build_authorization_header(cookies, timestamp=123, origin="https://aistudio.google.com")

    sap = hashlib.sha1(b"123 sapisid-value https://aistudio.google.com").hexdigest()
    one = hashlib.sha1(b"123 one-value https://aistudio.google.com").hexdigest()
    three = hashlib.sha1(b"123 three-value https://aistudio.google.com").hexdigest()
    assert header == (
        f"SAPISIDHASH 123_{sap} "
        f"SAPISID1PHASH 123_{one} "
        f"SAPISID3PHASH 123_{three}"
    )


def test_cookie_header_only_includes_target_domain_cookies():
    cookies = [
        {"name": "GOOG", "value": "1", "domain": ".google.com", "path": "/", "secure": True},
        {"name": "TARGET", "value": "2", "domain": "alkalimakersuite-pa.clients6.google.com", "path": "/", "secure": True},
        {"name": "OTHER", "value": "3", "domain": "example.com", "path": "/", "secure": True},
    ]
    url = "https://alkalimakersuite-pa.clients6.google.com/$rpc/test"

    assert [cookie["name"] for cookie in cookies_for_url(cookies, url)] == ["TARGET"]
    assert build_cookie_header(cookies, url) == "TARGET=2"


def test_strip_browser_only_headers_deduplicates_and_removes_replay_unsafe_headers():
    headers = {
        "Origin": "https://old.example",
        "origin": "https://aistudio.google.com",
        "Referer": "https://old.example",
        "Authorization": "old",
        "Cookie": "secret",
        "Sec-Fetch-Site": "same-site",
        "X-Goog-Api-Key": "key",
    }

    assert _strip_browser_only_headers(headers) == {
        "authorization": "old",
        "x-goog-api-key": "key",
    }
