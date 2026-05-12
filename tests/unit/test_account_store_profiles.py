from aistudio_api.infrastructure.account.account_store import AccountStore
from aistudio_api.application.account_service import AccountService
import pytest


def _storage_state():
    return {
        "cookies": [
            {
                "name": "SID",
                "value": "test",
                "domain": ".google.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "None",
                "expires": -1,
            }
        ],
        "origins": [],
    }


def test_save_account_creates_persistent_profile_dir(tmp_path):
    store = AccountStore(accounts_dir=tmp_path)

    account = store.save_account("test", None, _storage_state())
    profile_path = store.get_profile_path(account.id)

    assert profile_path is not None
    assert profile_path.is_dir()
    assert store.get_auth_path(account.id).is_file()


def test_overwriting_account_resets_profile_dir(tmp_path):
    store = AccountStore(accounts_dir=tmp_path)
    account = store.save_account("test", None, _storage_state(), account_id="acc_test")
    profile_path = store.get_profile_path(account.id)
    stale_file = profile_path / "stale.txt"
    stale_file.write_text("old", encoding="utf-8")

    store.save_account("test", None, _storage_state(), account_id="acc_test")

    assert store.get_profile_path(account.id).is_dir()
    assert not stale_file.exists()


def test_save_account_copies_logged_in_profile(tmp_path):
    store = AccountStore(accounts_dir=tmp_path / "accounts")
    source_profile = tmp_path / "source-profile"
    source_profile.mkdir()
    (source_profile / "cookies.sqlite").write_text("session", encoding="utf-8")
    (source_profile / "cache2").mkdir()
    (source_profile / "cache2" / "ignored").write_text("cache", encoding="utf-8")

    account = store.save_account(
        "test",
        None,
        _storage_state(),
        profile_source=source_profile,
    )
    profile_path = store.get_profile_path(account.id)

    assert (profile_path / "cookies.sqlite").read_text(encoding="utf-8") == "session"
    assert not (profile_path / "cache2").exists()


def test_save_account_rejects_unsafe_account_id(tmp_path):
    store = AccountStore(accounts_dir=tmp_path)

    with pytest.raises(ValueError, match="账号 ID 格式不正确"):
        store.save_account("test", None, _storage_state(), account_id="../escape")


def test_account_service_switches_browser_to_persistent_profile(tmp_path):
    class DummyLoginService:
        pass

    class DummyBrowserSession:
        auth_file = None
        profile_dir = None

        async def switch_auth(self, auth_file, profile_dir=None):
            self.auth_file = auth_file
            self.profile_dir = profile_dir

    store = AccountStore(accounts_dir=tmp_path)
    account = store.save_account("test", None, _storage_state())
    service = AccountService(store, DummyLoginService())
    browser_session = DummyBrowserSession()

    import asyncio

    loaded = asyncio.run(service.ensure_active_loaded(browser_session))

    assert loaded.id == account.id
    assert browser_session.auth_file == str(store.get_auth_path(account.id).resolve())
    assert browser_session.profile_dir == str(store.get_profile_path(account.id).resolve())
