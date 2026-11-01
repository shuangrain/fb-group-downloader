import json
import time
from pathlib import Path

from fb_group_downloader.auth.session import SessionManager


def test_session_manager_validation(tmp_path: Path):
    session_file = tmp_path / "session.json"
    mgr = SessionManager(session_file)

    # 1. 檔案不存在
    assert not mgr.is_logged_in()

    # 2. 寫入不完整的 session
    with open(session_file, "w") as f:
        json.dump({"cookies": [{"name": "other_cookie", "value": "123"}]}, f)
    assert not mgr.is_logged_in()

    # 3. 寫入包含有效 c_user 與 xs 的 session
    future_time = time.time() + 3600
    with open(session_file, "w") as f:
        json.dump(
            {
                "cookies": [
                    {"name": "c_user", "value": "100012345678", "expires": future_time},
                    {"name": "xs", "value": "secret_xs_val", "expires": future_time},
                ]
            },
            f,
        )
    assert mgr.is_logged_in()

    cookie_dict = mgr.load_cookies_dict()
    assert cookie_dict["c_user"] == "100012345678"
    assert cookie_dict["xs"] == "secret_xs_val"
    assert mgr.get_cookies_dict() == cookie_dict


def test_import_cookie_editor_json(tmp_path: Path):
    cookie_editor_file = tmp_path / "cookie_editor.json"
    session_file = tmp_path / "session.json"

    raw_cookies = [
        {
            "name": "c_user",
            "value": "100099999",
            "domain": ".facebook.com",
            "path": "/",
            "expirationDate": time.time() + 86400,
        },
        {
            "name": "xs",
            "value": "xs_token_abc",
            "domain": ".facebook.com",
            "path": "/",
            "expirationDate": time.time() + 86400,
        },
    ]
    with open(cookie_editor_file, "w") as f:
        json.dump(raw_cookies, f)

    mgr = SessionManager(session_file)
    assert mgr.import_from_cookie_editor_json(cookie_editor_file)
    assert mgr.is_logged_in()


def test_save_storage_state_safely_prevents_overwriting_with_invalid_state(tmp_path: Path):
    session_file = tmp_path / "session.json"
    mgr = SessionManager(session_file)
    future_time = time.time() + 3600

    valid_state = {
        "cookies": [
            {"name": "c_user", "value": "1000111", "expires": future_time},
            {"name": "xs", "value": "valid_xs_1", "expires": future_time},
        ],
        "origins": [],
    }
    assert mgr.save_storage_state_safely(valid_state)
    assert mgr.is_logged_in()

    # 若傳入缺少 c_user 或 xs 的狀態，不應覆蓋原本有效的 session.json
    invalid_state = {
        "cookies": [
            {"name": "datr", "value": "some_device_token", "expires": future_time},
        ],
        "origins": [],
    }
    assert not mgr.save_storage_state_safely(invalid_state)
    assert mgr.is_logged_in()
    assert mgr.get_cookies_dict()["xs"] == "valid_xs_1"


def test_get_cookies_to_merge():
    future_1 = time.time() + 3600
    future_2 = time.time() + 7200

    file_cookies = [
        {"name": "c_user", "value": "10001", "domain": ".facebook.com", "expires": future_2},
        {"name": "xs", "value": "newer_xs", "domain": ".facebook.com", "expires": future_2},
        {"name": "datr", "value": "device_id", "domain": ".facebook.com", "expires": future_2},
    ]

    # 1. Context 尚未登入時，應全數注入
    assert SessionManager.get_cookies_to_merge([], file_cookies) == file_cookies

    # 2. Context 已登入，但檔案中有較新的 xs 與缺少的 datr
    ctx_cookies = [
        {"name": "c_user", "value": "10001", "domain": ".facebook.com", "expires": future_2},
        {"name": "xs", "value": "older_xs", "domain": ".facebook.com", "expires": future_1},
    ]
    merged = SessionManager.get_cookies_to_merge(ctx_cookies, file_cookies)
    merged_names = {c["name"] for c in merged}
    assert merged_names == {"xs", "datr"}


def test_login_url_detection_and_lock_cleanup(tmp_path: Path):
    assert SessionManager.is_login_or_checkpoint_url("https://www.facebook.com/login/")
    assert SessionManager.is_login_or_checkpoint_url("https://www.facebook.com/checkpoint/?next")
    assert SessionManager.is_login_or_checkpoint_url("https://www.facebook.com/two_step_verification/authentication")
    assert not SessionManager.is_login_or_checkpoint_url("https://www.facebook.com/")
    assert not SessionManager.is_login_or_checkpoint_url("https://www.facebook.com/groups/123456")

    user_data_dir = tmp_path / "browser_data"
    user_data_dir.mkdir()
    lock_file = user_data_dir / "SingletonLock"
    lock_file.symlink_to("dead-container-12345")
    assert lock_file.is_symlink()

    SessionManager.cleanup_stale_locks(user_data_dir)
    assert not lock_file.is_symlink()
