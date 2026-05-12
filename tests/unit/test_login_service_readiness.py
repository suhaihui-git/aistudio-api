from aistudio_api.infrastructure.account.login_service import LoginService


def test_login_ready_requires_aistudio_chat_ui():
    service = LoginService()

    assert service._is_aistudio_ready(
        {
            "url": "https://aistudio.google.com/prompts/new_chat",
            "title": "Google AI Studio",
            "hasTextarea": True,
            "hasMakerSuite": True,
            "hasRunButton": True,
            "textPreview": "",
        }
    )


def test_login_ready_rejects_google_intermediate_pages():
    service = LoginService()

    assert not service._is_aistudio_ready(
        {
            "url": "https://accounts.google.com/signin/challenge",
            "title": "Sign in",
            "hasTextarea": False,
            "hasMakerSuite": False,
            "hasRunButton": False,
            "textPreview": "Verify it is you",
        }
    )
    assert not service._should_probe_aistudio(
        {"url": "https://accounts.google.com/signin/challenge"}
    )
