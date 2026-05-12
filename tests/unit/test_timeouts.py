from aistudio_api.infrastructure.gateway.timeouts import completion_timeout_seconds


def test_completion_timeout_keeps_base_without_max_tokens():
    assert completion_timeout_seconds(max_tokens=None, base_seconds=120) == 120


def test_completion_timeout_scales_for_large_max_tokens():
    assert completion_timeout_seconds(max_tokens=5000, base_seconds=120) == 227
    assert completion_timeout_seconds(max_tokens=32768, base_seconds=120) == 1153


def test_completion_timeout_ignores_invalid_values():
    assert completion_timeout_seconds(max_tokens=0, base_seconds=120) == 120
    assert completion_timeout_seconds(max_tokens=True, base_seconds=120) == 120
