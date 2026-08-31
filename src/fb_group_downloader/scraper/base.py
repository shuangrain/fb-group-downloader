import asyncio
import logging
import random

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from fb_group_downloader.config import AppConfig
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class BaseScraper:
    def __init__(self, config: AppConfig):
        self.config = config
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None

    async def initialize(self) -> None:
        """初始化 Playwright 瀏覽器環境與 Session"""
        self.playwright = await async_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]

        self.browser = await self.playwright.chromium.launch(
            headless=self.config.headless,
            args=launch_args,
        )

        context_options = {
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
        """關閉瀏覽器與資源"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
