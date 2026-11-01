import json
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from fb_group_downloader.utils.logger import console, get_logger

logger = get_logger()

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class SessionManager:
    def __init__(
        self,
        session_file: Path | str = "./session.json",
        user_data_dir: Path | str | None = "./browser_data",
        user_agent: str | None = None,
    ):
        self.session_file = Path(session_file)
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.user_agent = user_agent or DEFAULT_USER_AGENT

    @staticmethod
    def has_valid_auth_cookies(cookies: list[dict[str, Any]]) -> bool:
        """檢查 Cookie 列表中是否包含未過期的 Facebook 核心登入憑證 (c_user 與 xs)"""
        has_c_user = False
        has_xs = False
        now = time.time()

        for c in cookies:
            exp = c.get("expires", 0)
            is_unexpired = exp == -1 or exp > now
            if c.get("name") == "c_user" and c.get("value") and is_unexpired:
                has_c_user = True
            elif c.get("name") == "xs" and c.get("value") and is_unexpired:
                has_xs = True

        return has_c_user and has_xs

    @staticmethod
    def is_login_or_checkpoint_url(url: str) -> bool:
        """判斷目前網址是否處於登入、二步驟驗證 (2FA) 或安全檢查頁面"""
        lower_url = url.lower()
        blocked_paths = (
            "/login",
            "login.php",
            "/checkpoint",
            "/two_step_verification",
            "/recover",
            "/account_recovery",
        )
        return any(p in lower_url for p in blocked_paths)

    @staticmethod
    def cleanup_stale_locks(user_data_dir: Path) -> None:
        """清理 Chromium 持久化設定檔目錄中殘留的 Lock 檔案（避免容器重啟或異常中斷後無法啟動）"""
        if not user_data_dir.exists():
            return
        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = user_data_dir / lock_name
            try:
                if lock_path.is_symlink() or lock_path.exists():
                    lock_path.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"清理殘留瀏覽器鎖定檔 {lock_path} 時忽略錯誤：{e}")

    def is_logged_in(self) -> bool:
        """檢查目前儲存的 Session 檔案是否存在且包含有效的 Facebook 登入 Cookie"""
        if not self.session_file.exists():
            return False
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])
            return self.has_valid_auth_cookies(cookies)
        except Exception as e:
            logger.error(f"讀取 Session 檔案時出錯：{e}")
            return False

    def save_storage_state_safely(self, storage_state: dict[str, Any]) -> bool:
        """
        安全地將 Playwright storage_state 寫回 session.json：
        - 僅在包含有效 c_user 與 xs 時才覆寫，防止因網路異常或登出狀態覆蓋掉原本有效的憑證
        - 直接覆寫原檔案內容（相容 Docker 單檔 bind mount）
        """
        cookies = storage_state.get("cookies", [])
        if not self.has_valid_auth_cookies(cookies):
            logger.warning("目前瀏覽器狀態不包含有效的登入 Cookie (c_user / xs)，略過回存以避免覆蓋有效 Session。")
            return False

        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(storage_state, f, indent=2, ensure_ascii=False)
            logger.debug(f"✓ 已自動刷新並回存 Session 檔案：{self.session_file} (共 {len(cookies)} 個 Cookies)")
            return True
        except Exception as e:
            logger.warning(f"回存 Session 檔案失敗 ({self.session_file})：{e}")
            return False

    def load_cookies_list(self) -> list[dict[str, Any]]:
        """讀取 session.json 中的原始 Cookie 列表（自動過濾已過期的 Cookie）"""
        if not self.session_file.exists():
            return []
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])
            now = time.time()
            valid_cookies = []
            for c in cookies:
                exp = c.get("expires", -1)
                if exp == -1 or exp > now:
                    valid_cookies.append(c)
            return valid_cookies
        except Exception as e:
            logger.error(f"讀取 Session Cookie 列表失敗：{e}")
            return []

    @classmethod
    def get_cookies_to_merge(
        cls, context_cookies: list[dict[str, Any]], file_cookies: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        比對持久化瀏覽器 Profile 與 session.json 中的 Cookies：
        - 若瀏覽器 Profile 尚未登入，或 session.json 中的 Cookie 較新/缺少（如剛執行 import-cookies），
          回傳需要注入至瀏覽器 Context 的 Cookie 列表。
        """
        if not file_cookies:
            return []

        if not cls.has_valid_auth_cookies(context_cookies):
            return file_cookies

        ctx_map: dict[tuple[str, str], dict[str, Any]] = {
            (c.get("name", ""), c.get("domain", "")): c for c in context_cookies
        }
        to_inject: list[dict[str, Any]] = []
        for fc in file_cookies:
            key = (fc.get("name", ""), fc.get("domain", ""))
            existing = ctx_map.get(key)
            if not existing:
                to_inject.append(fc)
            else:
                fc_exp = fc.get("expires", -1)
                ex_exp = existing.get("expires", -1)
                if fc_exp > ex_exp and fc.get("value") != existing.get("value"):
                    to_inject.append(fc)
        return to_inject

    def interactive_login(self, timeout_sec: int = 300) -> bool:
        """
        開啟可視化瀏覽器視窗，引導使用者登入 Facebook，登入成功後自動擷取並保存 Session / Cookies
        同時寫入持久化瀏覽器設定檔目錄 (user_data_dir) 以保留 IndexedDB 與受信任裝置狀態。
        """
        console.print("\n[bold cyan]=== Facebook 互動式登入 ===[/bold cyan]")
        console.print(
            "即將開啟瀏覽器視窗，請在視窗中登入您的 Facebook 帳號（包含完成二步驟驗證 2FA 與點選「信任這台裝置／儲存瀏覽器」）。"
        )
        console.print(f"程式將在偵測到進入 Facebook 首頁後自動保存 Session 至：[green]{self.session_file}[/green]")
        if self.user_data_dir:
            console.print(f"持久化瀏覽器設定檔將同步保存至：[green]{self.user_data_dir}[/green]\n")

        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        existing_file_cookies = self.load_cookies_list()

        with sync_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--start-maximized",
            ]
            browser = None

            if self.user_data_dir:
                self.user_data_dir.mkdir(parents=True, exist_ok=True)
                self.cleanup_stale_locks(self.user_data_dir)
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.user_data_dir),
                    headless=False,
                    args=launch_args,
                    no_viewport=True,
                    user_agent=self.user_agent,
                    locale="zh-TW",
                    timezone_id="Asia/Taipei",
                )
            else:
                browser = p.chromium.launch(headless=False, args=launch_args)
                context = browser.new_context(
                    no_viewport=True,
                    user_agent=self.user_agent,
                    locale="zh-TW",
                    timezone_id="Asia/Taipei",
                )

            # 預先載入既有的裝置識別 Cookies (如 datr, sb 等)，避免每次都被當成全新陌生裝置
            if existing_file_cookies:
                try:
                    to_merge = self.get_cookies_to_merge(context.cookies(), existing_file_cookies)
                    if to_merge:
                        context.add_cookies(to_merge)  # type: ignore[arg-type]
                        logger.debug(f"已預先載入 {len(to_merge)} 個既有 Cookie 至登入視窗")
                except Exception as e:
                    logger.debug(f"預先載入既有 Cookie 時略過：{e}")

            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

            start_time = time.time()
            logged_in = False

            while time.time() - start_time < timeout_sec:
                time.sleep(2)
                try:
                    cookies = context.cookies()
                    current_url = page.url
                except Exception:
                    break

                # 確認已取得有效 c_user 與 xs，且已離開 /login、/checkpoint、/two_step_verification 等驗證頁面
                if self.has_valid_auth_cookies(cookies) and not self.is_login_or_checkpoint_url(current_url):
                    # 稍候 4 秒讓首頁完成重新導向、IndexedDB 寫入與最終 Set-Cookie
                    time.sleep(4)
                    try:
                        cookies = context.cookies()
                        current_url = page.url
                    except Exception:
                        break

                    if self.has_valid_auth_cookies(cookies) and not self.is_login_or_checkpoint_url(current_url):
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                        logger.info(f"偵測到登入成功！用戶 ID: {cookie_dict.get('c_user')}")
                        state = context.storage_state()
                        self.save_storage_state_safely(state)
                        logged_in = True
                        break

            try:
                context.close()
                if browser:
                    browser.close()
            except Exception:
                pass

            if logged_in:
                console.print(f"[bold green]✓ 登入成功！Session 已妥善保存至 {self.session_file}[/bold green]\n")
                return True
            else:
                console.print("[bold red]✗ 登入逾時或未完成登入。請重試。[/bold red]\n")
                return False

    def load_cookies_dict(self) -> dict[str, str]:
        """將 storage_state.json 轉換為 requests/httpx 用的 Cookie 字典"""
        if not self.session_file.exists():
            return {}
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])
            return {c["name"]: c["value"] for c in cookies}
        except Exception as e:
            logger.error(f"解析 Session Cookie 字典失敗：{e}")
            return {}

    def get_cookies_dict(self) -> dict[str, str]:
        """相容方法：取得 Cookie 字典"""
        return self.load_cookies_dict()

    def import_from_cookie_editor_json(self, json_file: Path | str) -> bool:
        """
        支援從瀏覽器套件（如 Cookie-Editor）導出的 JSON 檔案匯入為 Playwright storage_state 格式
        """
        src = Path(json_file)
        if not src.exists():
            raise FileNotFoundError(f"找不到檔案：{src}")

        with open(src, encoding="utf-8") as f:
            raw_cookies = json.load(f)

        formatted_cookies = []
        for c in raw_cookies:
            # 轉換為 Playwright cookie 格式
            cookie_entry = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", ".facebook.com"),
                "path": c.get("path", "/"),
                "expires": c.get("expirationDate", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", True),
                "sameSite": "Lax"
                if c.get("sameSite") in [None, "unspecified", "lax"]
                else "None"
                if c.get("sameSite") == "no_restriction"
                else "Strict",
            }
            formatted_cookies.append(cookie_entry)

        storage_data = {
            "cookies": formatted_cookies,
            "origins": [],
        }

        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_file, "w", encoding="utf-8") as f:
            json.dump(storage_data, f, indent=2)

        logger.info(f"成功從 {src} 匯入 {len(formatted_cookies)} 個 Cookies 至 {self.session_file}")
        return True
