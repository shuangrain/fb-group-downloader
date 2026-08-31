import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from fb_group_downloader.utils.logger import console, get_logger

logger = get_logger()


class SessionManager:
    def __init__(self, session_file: Path | str = "./session.json"):
        self.session_file = Path(session_file)

    def is_logged_in(self) -> bool:
        """檢查目前儲存的 Session 檔案是否存在且包含有效的 Facebook 登入 Cookie"""
        if not self.session_file.exists():
            return False
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])
            has_c_user = False
            has_xs = False
            now = time.time()

            for c in cookies:
                if c.get("name") == "c_user":
                    if c.get("expires", 0) == -1 or c.get("expires", 0) > now:
                        has_c_user = True
                elif c.get("name") == "xs":
                    if c.get("expires", 0) == -1 or c.get("expires", 0) > now:
                        has_xs = True

            return has_c_user and has_xs
        except Exception as e:
            logger.error(f"讀取 Session 檔案時出錯：{e}")
            return False

    def interactive_login(self, timeout_sec: int = 300) -> bool:
        """
        開啟可視化瀏覽器視窗，引導使用者登入 Facebook，登入成功後自動擷取並保存 Session / Cookies。
        """
        console.print("\n[bold cyan]=== Facebook 互動式登入 ===[/bold cyan]")
        console.print("即將開啟瀏覽器視窗，請在視窗中登入您的 Facebook 帳號（包含完成二步驟驗證 2FA）。")
        console.print(f"程式將在偵測到登入成功後自動保存 Session 至：[green]{self.session_file}[/green]\n")

        self.session_file.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--start-maximized",
                ],
            )
            context = browser.new_context(
                viewport=None,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")

            start_time = time.time()
            logged_in = False

            while time.time() - start_time < timeout_sec:
                time.sleep(2)
                # 檢查 cookies 中是否存在 c_user (FB 用戶 ID cookie)
                cookies = context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}

                if "c_user" in cookie_dict and "xs" in cookie_dict:
                    logger.info(f"偵測到登入成功！用戶 ID: {cookie_dict['c_user']}")
                    logged_in = True
                    # 稍等 2 秒確保所有 Session Cookies 寫入完畢
                    time.sleep(2)
                    context.storage_state(path=str(self.session_file))
                    break

            browser.close()

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
