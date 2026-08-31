import re

from playwright.async_api import Page

from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class FacebookVideoExtractor:
    """
    專門用來解析 Facebook 私密社團與貼文中的直鏈高畫質影片 (Direct MP4 CDN Streams)
    """

    @staticmethod
    def _clean_stream_url(raw_url: str) -> str:
        """還原與解碼 Facebook 內嵌 JSON 中的 escaped URL"""
        if not raw_url:
            return ""
        # 替換 JSON escaped 反斜線
        cleaned = raw_url.replace(r"\/", "/").replace("\\u0025", "%")
        try:
            # 處理 \u0026 等 Unicode escape 字元
            cleaned = cleaned.encode("utf-8").decode("unicode_escape")
        except Exception:
            pass
        return cleaned.strip()

    @classmethod
    def extract_from_html(cls, html_content: str) -> str | None:
        """從 Facebook 頁面原始碼中搜尋各類 video stream 屬性與 JSON 標籤"""
        patterns = [
            r'"playable_url_quality_hd":\s*"([^"]+)"',
            r'"browser_native_hd_url":\s*"([^"]+)"',
            r'"hd_src":\s*"([^"]+)"',
            r'"hd_src_no_ratelimit":\s*"([^"]+)"',
            r'"playable_url":\s*"([^"]+)"',
            r'"browser_native_sd_url":\s*"([^"]+)"',
            r'"sd_src":\s*"([^"]+)"',
            r'"sd_src_no_ratelimit":\s*"([^"]+)"',
            r'hd_src:"([^"]+)"',
            r'sd_src:"([^"]+)"',
            r'<video[^>]+src="([^"]+)"',
        ]

        for pat in patterns:
            matches = re.findall(pat, html_content)
            for m in matches:
                clean = cls._clean_stream_url(m)
                if clean and "fbcdn.net" in clean and ("mp4" in clean or "video" in clean or "bytestart" in clean):
                    return clean
                if clean and clean.startswith("http") and not clean.startswith("blob:"):
                    return clean

        return None

    @classmethod
    async def resolve_video_url(cls, page: Page, video_page_url: str, timeout_ms: int = 5000) -> str | None:
        """
        在 Playwright 已登入的 session 瀏覽器中開啟影片貼文網址，攔截並提取真實的 .mp4 串流直鏈
        """
        if ".mp4" in video_page_url and "fbcdn.net" in video_page_url:
            return video_page_url

        captured_streams: list[str] = []

        def on_response(response):
            r_url = response.url
            if ".mp4" in r_url and "fbcdn.net" in r_url:
                if r_url not in captured_streams:
                    captured_streams.append(r_url)

        # 暫時註冊監聽器
        page.on("response", on_response)
        logger.debug(f"正在嘗試透過瀏覽器解析影片直鏈：{video_page_url[:80]}...")

        try:
            # 建立新頁面或前往影片網址
            v_page = await page.context.new_page()
            v_page.on("response", on_response)

            try:
                await v_page.goto(video_page_url, wait_until="domcontentloaded", timeout=timeout_ms)
                # 稍等 1 秒讓內部 GraphQL 載入
                await v_page.wait_for_timeout(1500)

                # 嘗試從 DOM 點擊播放以觸發影片串流
                try:
                    video_elem = v_page.locator("video, div[data-video-id], div[aria-label*='播放']").first
                    if await video_elem.is_visible(timeout=500):
                        await video_elem.hover()
                except Exception:
                    pass

                # 1. 優先檢查網路攔截到的串流
                if captured_streams:
                    logger.info(f"✓ 成功攔截到影片 CDN 直鏈：{captured_streams[0][:80]}...")
                    return captured_streams[0]

                # 2. 檢查頁面 HTML 中的 JSON 標籤
                html = await v_page.content()
                extracted = cls.extract_from_html(html)
                if extracted:
                    logger.info(f"✓ 成功從頁面原始碼解析影片直鏈：{extracted[:80]}...")
                    return extracted

            finally:
                await v_page.close()

        except Exception as e:
            logger.debug(f"瀏覽器解析影片失敗：{e}")

        return None
