import pytest

from aistudio_api.infrastructure.gateway.capture import CapturedRequest, is_aistudio_wire_body


def test_captured_request_rejects_non_json_body():
    with pytest.raises(ValueError):
        CapturedRequest(url="https://example.com/GenerateContent", headers={}, body="not-json")


def test_captured_request_rejects_non_wire_json_body():
    with pytest.raises(ValueError):
        CapturedRequest(url="https://example.com/GenerateContent", headers={}, body='{"ok": true}')


def test_captured_request_accepts_aistudio_wire_body():
    body = '["models/gemini-3.1-pro-preview",[],null,[null,null,null,1024],"!snapshot"]'

    captured = CapturedRequest(url="https://example.com/GenerateContent", headers={}, body=body)

    assert captured.model == "models/gemini-3.1-pro-preview"
    assert captured.snapshot == "!snapshot"


def test_wire_body_shape_validation():
    assert is_aistudio_wire_body(["models/x", [], None, [], "!snapshot"])
    assert not is_aistudio_wire_body({"model": "models/x"})
