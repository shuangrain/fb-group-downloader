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
        例如路徑中的 /s526x296/, /p720x720/, /c0.0.526.296a/
        或參數中的 ctp=s590x590, stp=dst-jpg_s720x720 等低解析度標記
        """
        if not url:
            return False

        # 1. 檢查路徑中的縮圖標記
        if re.search(r"/[spc]\d+x\d+[^/]*/|/c\d+\.\d+\.\d+\.\d+a/", url):
            return True

        # 2. 檢查查詢參數中的縮圖標記 (例如 ctp=s590x590, ctp=s480x480)
        m_ctp = re.search(r"[?&]ctp=[sp](\d+)x(\d+)", url)
        if m_ctp:
            w, h = int(m_ctp.group(1)), int(m_ctp.group(2))
            if w < 1000 or h < 1000:
                return True

        # 3. 檢查 stp 參數中的縮圖標記
        m_stp = re.search(r"stp=[^&]*[sp](\d+)x(\d+)", url)
        if m_stp:
            w, h = int(m_stp.group(1)), int(m_stp.group(2))
            if w < 1000 or h < 1000:
                return True

        return False

    @staticmethod
    def is_icon_or_ui_asset(url: str) -> bool:
        """判斷圖片網址是否為 Facebook 前端 UI 圖示、Emoji 或靜態小圖"""
        if not url:
            return True
        low_url = url.lower()
        if (
            "emoji.php" in low_url
            or "rsrc.php" in low_url
            or "static.xx.fbcdn.net" in low_url
            or "static.facebook.com" in low_url
            or "/rsrc.php/" in low_url
            or "/assets/" in low_url
            or "favicon" in low_url
            or "/badges/" in low_url
        ):
            return True
        return False

    @staticmethod
    def get_image_dimensions(data: bytes) -> tuple[int, int] | None:
        """從圖片二進制資料快速讀取 (width, height)，支援 PNG, JPEG, GIF"""
        if not data or len(data) < 24:
            return None
        import struct

        # PNG: IHDR 位於第 16-24 bytes
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            try:
                w, h = struct.unpack(">II", data[16:24])
                return w, h
            except Exception:
                return None

        # GIF: 第 6-10 bytes
        if data[:6] in (b"GIF87a", b"GIF89a"):
            try:
                w, h = struct.unpack("<HH", data[6:10])
                return w, h
            except Exception:
                return None

        # JPEG: 尋找 SOF0/SOF2 標記
        if data[:2] == b"\xff\xd8":
            try:
                idx = 2
                while idx < len(data) - 9:
                    if data[idx] == 0xFF:
                        marker = data[idx + 1]
                        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                            h, w = struct.unpack(">HH", data[idx + 5 : idx + 9])
                            return w, h
                        elif marker not in (0xD8, 0xD9):
                            length = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
                            idx += 2 + length
                            continue
                    idx += 1
            except Exception:
                return None

        return None

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
                if clean and "fbcdn.net" in clean and not cls.is_thumbnail_url(clean):
                    return clean
        return None

    @classmethod
    async def resolve_high_res_photo_url(
        cls, page: Page, photo_page_url: str, fallback_url: str = "", timeout_ms: int = 8000
    ) -> str:
        """
        透過 Playwright 瀏覽器開啟相片燈箱頁面 (Photo Viewer)，提取 100% 原始高畫質大圖網址
        """
        if not photo_page_url or not photo_page_url.startswith("http"):
            return fallback_url

        try:
            v_page = await page.context.new_page()
            captured_net_urls: list[str] = []

            def on_response(resp):
                r_url = resp.url
                if "fbcdn.net" in r_url and ("jpg" in r_url or "png" in r_url or "webp" in r_url):
                    if not cls.is_thumbnail_url(r_url):
                        captured_net_urls.append(r_url)

            v_page.on("response", on_response)

            try:
                await v_page.goto(photo_page_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await v_page.wait_for_timeout(2500)

                # 1. 優先從 DOM 取得 Photo Viewer 核心大圖 (data-visualcompletion="media-vc-image")
                best_dom_url = await v_page.evaluate(
                    r"""() => {
                        // 1. 尋找 Facebook Photo Viewer 的核心大圖標籤
                        const viewerImg = document.querySelector('img[data-visualcompletion="media-vc-image"], img.spotlight');
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

                        // 2. 尋找 dialog 內尺寸最大的圖片
                        let maxArea = 0;
                        let bestSrc = "";
                        const imgs = Array.from(document.querySelectorAll('div[role="dialog"] img, div[role="main"] img, img[src*="fbcdn.net"]'));
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
                    logger.debug(f"✓ 成功從 Photo Viewer DOM 解析原尺寸相片：{best_dom_url[:80]}...")
                    return best_dom_url

                # 2. 檢查網路攔截到的無縮圖高畫質圖片
                if captured_net_urls:
                    logger.debug(f"✓ 成功從網路攔截解析高解析相片：{captured_net_urls[-1][:80]}...")
                    return captured_net_urls[-1]

                # 3. 檢查頁面 HTML 中的 JSON 標籤
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
