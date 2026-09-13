import re

from playwright.async_api import Page

from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class FacebookPhotoExtractor:
    """
    專門用來解析 Facebook 原始高解析度相片（2048px+ / Full HD+）
    避免抓取到動態牆上被壓縮、縮放或裁切的卡片縮圖
    """

    @staticmethod
    def is_thumbnail_url(url: str) -> bool:
        """
        判斷圖片網址是否帶有 Facebook 動態牆卡片的縮放與裁切標記
        例如 /s526x296/, /p720x720/, /p480x480/, /c0.0.526.296a/
        """
        if not url:
            return False
        return bool(re.search(r"/[spc]\d+x\d+[^/]*/|/c\d+\.\d+\.\d+\.\d+a/", url))

    @staticmethod
    def _clean_stream_url(raw_url: str) -> str:
        """還原與解碼 Facebook 內嵌 JSON 中的 escaped URL"""
        if not raw_url:
            return ""
        cleaned = raw_url.replace(r"\/", "/").replace("\\u0025", "%")
        try:
            cleaned = cleaned.encode("utf-8").decode("unicode_escape")
        except Exception:
            pass
        return cleaned.strip()

    @classmethod
    def extract_from_html(cls, html_content: str) -> str | None:
        """從 Facebook 頁面原始碼中尋找未壓縮的大尺寸原圖直鏈"""
        patterns = [
            r'"viewer_image":\s*\{\s*"uri":\s*"([^"]+)"',
            r'"large_share_image":\s*\{\s*"uri":\s*"([^"]+)"',
            r'"full_sub_photo":\s*\{\s*"uri":\s*"([^"]+)"',
            r'"image":\s*\{\s*"uri":\s*"([^"]+)",\s*"width":\s*(?:[1-9]\d{3,})',  # 寬度大於 1000px
            r'"uri":\s*"(https:[^"]+fbcdn\.net[^"]+stp=dst-jpg_s2048x2048[^"]*)"',
        ]

        for pat in patterns:
            matches = re.findall(pat, html_content)
            for m in matches:
                clean = cls._clean_stream_url(m)
                if clean and "fbcdn.net" in clean:
                    return clean
        return None

    @classmethod
    async def resolve_high_res_photo_url(
        cls, page: Page, photo_page_url: str, fallback_url: str = "", timeout_ms: int = 6000
    ) -> str:
        """
        透過 Playwright 瀏覽器開啟相片燈箱頁面 (Photo Viewer)，提取 100% 原始高畫質大圖網址
        """
        if not photo_page_url or not photo_page_url.startswith("http"):
            return fallback_url

        try:
            v_page = await page.context.new_page()
            try:
                await v_page.goto(photo_page_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await v_page.wait_for_timeout(1000)

                # 1. 嘗試從 DOM 取得 Photo Viewer 核心高畫質大圖
                best_dom_url = await v_page.evaluate(
                    r"""() => {
                        // 1. 優先尋找 Facebook Photo Viewer 的專用大圖標籤
                        const viewerImg = document.querySelector('img[data-visualcompletion="media-vc-image"], img.spotlight, div[role="dialog"] img[src*="fbcdn.net"]');
                        if (viewerImg) {
                            if (viewerImg.srcset) {
                                const parts = viewerImg.srcset.split(',');
                                let maxW = 0;
                                let best = viewerImg.src;
                                for (const p of parts) {
                                    const m = p.trim().match(/^(https:\/\/[^\s]+)\s+(\d+)w$/);
                                    if (m) {
                                        const w = parseInt(m[2], 10);
                                        if (w > maxW) {
                                            maxW = w;
                                            best = m[1];
                                        }
                                    }
                                }
                                return best;
                            }
                            return viewerImg.currentSrc || viewerImg.src || "";
                        }

                        // 2. 備用：尋找畫面上尺寸最大的圖片
                        let maxArea = 0;
                        let bestSrc = "";
                        const imgs = Array.from(document.querySelectorAll('img[src*="fbcdn.net"], img[src*="scontent"]'));
                        for (const img of imgs) {
                            const w = img.naturalWidth || img.width || 0;
                            const h = img.naturalHeight || img.height || 0;
                            const area = w * h;
                            if (area > maxArea && !img.src.includes('emoji.php') && !img.src.includes('rsrc.php')) {
                                maxArea = area;
                                bestSrc = img.currentSrc || img.src;
                            }
                        }
                        return bestSrc;
                    }"""
                )

                if best_dom_url and "fbcdn.net" in best_dom_url and not cls.is_thumbnail_url(best_dom_url):
                    logger.debug(f"✓ 成功從 Photo Viewer 解析原尺寸相片：{best_dom_url[:80]}...")
                    return best_dom_url

                # 2. 檢查頁面 HTML 中的 JSON 標籤
                html = await v_page.content()
                extracted = cls.extract_from_html(html)
                if extracted:
                    logger.debug(f"✓ 成功從頁面 JSON 解析高解析相片：{extracted[:80]}...")
                    return extracted

                if best_dom_url and "fbcdn.net" in best_dom_url:
                    return best_dom_url

            finally:
                await v_page.close()

        except Exception as e:
            logger.debug(f"解析高解析相片時略過：{e}")

        return fallback_url
