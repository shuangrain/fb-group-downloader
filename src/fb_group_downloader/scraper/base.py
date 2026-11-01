import asyncio
import logging
import random

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from fb_group_downloader.auth.session import SessionManager
from fb_group_downloader.config import AppConfig
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class BaseScraper:
    def __init__(self, config: AppConfig):
        self.config = config
        self.session_mgr = SessionManager(
            session_file=config.session_file,
            user_data_dir=config.user_data_dir,
            user_agent=config.user_agent,
        )
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def initialize(self) -> None:
        """初始化 Playwright 瀏覽器環境與持久化 Session"""
        self.playwright = await async_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]

        if self.config.user_data_dir:
            self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
            self.session_mgr.cleanup_stale_locks(self.config.user_data_dir)
            logger.debug(f"正在載入持久化瀏覽器設定檔目錄：{self.config.user_data_dir}")

            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.config.user_data_dir),
                headless=self.config.headless,
                args=launch_args,
                viewport={"width": 1280, "height": 800},
                user_agent=self.config.user_agent,
                locale="zh-TW",
                timezone_id="Asia/Taipei",
            )

            # 若 session.json 存在且包含較新或缺少的 Cookie（例如剛匯入或首次建立 browser_data），自動合併注入
            if self.config.session_file.exists():
                file_cookies = self.session_mgr.load_cookies_list()
                ctx_cookies = await self.context.cookies()
                to_merge = self.session_mgr.get_cookies_to_merge(ctx_cookies, file_cookies)  # type: ignore[arg-type]
                if to_merge:
                    await self.context.add_cookies(to_merge)  # type: ignore[arg-type]
                    logger.debug(f"已從 {self.config.session_file} 同步 {len(to_merge)} 個 Cookie 至持久化設定檔")
            elif not self.session_mgr.has_valid_auth_cookies(await self.context.cookies()):  # type: ignore[arg-type]
                logger.warning(f"找不到 Session 檔案：{self.config.session_file}，且持久化目錄尚未登入！")
        else:
            self.browser = await self.playwright.chromium.launch(
                headless=self.config.headless,
                args=launch_args,
            )

            context_options: dict = {
                "viewport": {"width": 1280, "height": 800},
                "user_agent": self.config.user_agent,
                "locale": "zh-TW",
                "timezone_id": "Asia/Taipei",
            }

            # 載入儲存的 Session / Cookies
            if self.config.session_file.exists():
                context_options["storage_state"] = str(self.config.session_file)
                logger.debug(f"已載入 Session 檔案：{self.config.session_file}")
            else:
                logger.warning(f"找不到 Session 檔案：{self.config.session_file}，可能會以未登入狀態瀏覽！")

            self.context = await self.browser.new_context(**context_options)

        self.context.set_default_timeout(self.config.browser_timeout_sec * 1000)

    async def save_session(self) -> bool:
        """將瀏覽器最新輪替/展延後的 Cookie 與 Storage 狀態回存至 session.json"""
        if not self.context:
            return False
        try:
            state = await self.context.storage_state()
            return self.session_mgr.save_storage_state_safely(state)  # type: ignore[arg-type]
        except Exception as e:
            logger.debug(f"保存瀏覽器 Session 時發生錯誤：{e}")
            return False

    async def get_cookies_dict(self) -> dict[str, str]:
        """從目前瀏覽器 Context 取得最新的 Cookie 字典（供 httpx / yt-dlp 使用）"""
        if not self.context:
            return self.session_mgr.get_cookies_dict()
        try:
            cookies = await self.context.cookies()
            return {c["name"]: c["value"] for c in cookies if "name" in c and "value" in c}
        except Exception:
            return self.session_mgr.get_cookies_dict()

    async def check_session_valid(self, page: Page) -> bool:
        """檢查目前頁面是否仍維持有效登入狀態（未被重導向至 /login 或 /checkpoint）"""
        if not self.context:
            return False
        current_url = page.url
        if self.session_mgr.is_login_or_checkpoint_url(current_url):
            return False
        cookies = await self.context.cookies()
        return self.session_mgr.has_valid_auth_cookies(cookies)  # type: ignore[arg-type]

    async def refresh_session(self) -> bool:
        """
        以無頭模式造訪 Facebook 首頁，觸發伺服器端 Cookie 展延與輪替，
        並將最新憑證同步寫入持久化設定檔目錄 (user_data_dir) 與 session.json。
        """
        try:
            await self.initialize()
            page = await self.new_page()
            try:
                await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
                await asyncio.sleep(3)
                await self.close_dialogs(page)
                if not await self.check_session_valid(page):
                    logger.error(f"Session 驗證失敗或已過期（目前頁面：{page.url}），請重新執行 login。")
                    return False
                saved = await self.save_session()
                return saved
            finally:
                await page.close()
        finally:
            await self.close()

    async def new_page(self) -> Page:
        if not self.context:
            await self.initialize()
        assert self.context is not None
        page = await self.context.new_page()

        # 阻擋不需要的追蹤與分析請求以加速載入
        await page.route(
            "**/*",
            lambda route: (
                route.abort()
                if any(domain in route.request.url for domain in ["google-analytics.com", "doubleclick.net"])
                else route.continue_()
            ),
        )

        # 若啟用 Debug 模式，記錄 Playwright 瀏覽器的網路請求與回應
        if logger.isEnabledFor(logging.DEBUG):
            page.on(
                "request",
                lambda req: logger.debug(f"[BROWSER REQ] {req.method} {req.url[:120]}..."),
            )
            page.on(
                "response",
                lambda resp: logger.debug(f"[BROWSER RESP] {resp.status} {resp.url[:120]}..."),
            )
            page.on(
                "requestfailed",
                lambda req: logger.debug(f"[BROWSER REQ FAILED] {req.method} {req.url[:120]}: {req.failure}"),
            )

        return page

    async def human_scroll(self, page: Page, delay_range: tuple[float, float] = (1.5, 3.0)) -> None:
        """模擬人類滾動頁面行為"""
        scroll_distance = random.randint(400, 800)
        await page.evaluate(f"window.scrollBy(0, {scroll_distance})")
        delay = random.uniform(*delay_range)
        await asyncio.sleep(delay)

    async def close_dialogs(self, page: Page) -> None:
        """關閉 Facebook 常見的彈出視窗（如登入提示、Cookie 接受等）"""
        dialog_selectors = [
            'div[aria-label="關閉"]',
            'div[aria-label="Close"]',
            'button:has-text("稍後再說")',
            'button:has-text("Not Now")',
            'button:has-text("只允許必要的 Cookie")',
            'button:has-text("Decline optional cookies")',
        ]
        for sel in dialog_selectors:
            try:
                elem = page.locator(sel).first
                if await elem.is_visible(timeout=500):
                    await elem.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

    async def close(self) -> None:
        """關閉瀏覽器與資源（關閉前自動將最新輪替的 Session Cookies 寫回 session.json）"""
        if self.context:
            await self.save_session()
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
