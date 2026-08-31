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
