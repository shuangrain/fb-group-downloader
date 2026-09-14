import base64
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import Page

from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class FacebookVideoExtractor:
    """
    專門用來解析 Facebook 私密社團與貼文中的直鏈高畫質影片 (Direct MP4 CDN Streams)
    """

    @staticmethod
    def strip_byte_range_params(url: str) -> str:
        """移除 Facebook CDN 網址中的 bytestart 與 byteend 參數以取得完整 progressive 影片串流"""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            q_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            filtered = [(k, v) for k, v in q_pairs if k not in ("bytestart", "byteend")]
            return urlunparse(parsed._replace(query=urlencode(filtered)))
        except Exception:
            cleaned = re.sub(r"[?&]bytestart=\d+", "", url)
            cleaned = re.sub(r"[?&]byteend=\d+", "", cleaned)
            if "?" not in cleaned and "&" in cleaned:
                cleaned = cleaned.replace("&", "?", 1)
            return cleaned

    @staticmethod
    def extract_video_id_from_url(url: str) -> str | None:
        """從影片網址擷取 Video ID，包含從 efg base64 參數解析"""
        if not url:
            return None
        patterns = [
            r"/videos/[^/]+/(\d+)",
            r"/videos/(\d+)",
            r"[?&]v=(\d+)",
            r"story_fbid=(\d+)",
            r"video_id=(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)

        try:
            parsed = urlparse(url)
            qs = parse_qsl(parsed.query)
            for k, v in qs:
                if k == "efg":
                    padded = v + "=" * (-len(v) % 4)
                    dec = base64.b64decode(padded).decode("utf-8", errors="ignore")
                    data = json.loads(dec)
                    if "video_id" in data:
                        return str(data["video_id"])
        except Exception:
            pass

        return None

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

    @staticmethod
    def is_audio_stream(url: str) -> bool:
        """判斷是否為純音訊串流（檢查網址及 efg base64 參數中的 vencode_tag）"""
        if not url:
            return False
        if "audio" in url.lower() or "heaac" in url.lower():
            return True
        try:
            parsed = urlparse(url)
            qs = parse_qsl(parsed.query)
            for k, v in qs:
                if k == "efg":
                    padded = v + "=" * (-len(v) % 4)
                    data = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
                    vtag = data.get("vencode_tag", "").lower()
                    if "audio" in vtag or "heaac" in vtag or "opus" in vtag:
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def get_video_stream_quality(url: str) -> int:
        """從 efg 參數擷取視訊解析度高度（例如 1080, 720, 360），用以優先選取高畫質視訊軌"""
        if not url:
            return 0
        try:
            parsed = urlparse(url)
            qs = parse_qsl(parsed.query)
            for k, v in qs:
                if k == "efg":
                    padded = v + "=" * (-len(v) % 4)
                    data = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
                    vtag = data.get("vencode_tag", "")
                    m = re.search(r"(\d+)p", vtag)
                    if m:
                        return int(m.group(1))
                    if "bitrate" in data:
                        return int(data["bitrate"])
        except Exception:
            pass
        return 0

    @staticmethod
    def is_vp9_stream(url: str) -> bool:
        """檢查是否為 VP9 或 AV1 等需要轉碼為通用相容格式 (H.264) 的串流"""
        if not url:
            return False
        if "vp9" in url.lower() or "av1" in url.lower():
            return True
        try:
            parsed = urlparse(url)
            qs = parse_qsl(parsed.query)
            for k, v in qs:
                if k == "efg":
                    padded = v + "=" * (-len(v) % 4)
                    data = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
                    vtag = data.get("vencode_tag", "").lower()
                    if "vp9" in vtag or "av1" in vtag:
                        return True
        except Exception:
            pass
        return False

    @classmethod
    def extract_from_html(cls, html_content: str) -> str | None:
        """從 Facebook 頁面原始碼中搜尋各類 video stream 屬性與 JSON 標籤（排除純音訊軌）"""
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
                if clean:
                    clean = cls.strip_byte_range_params(clean)
                    if not cls.is_audio_stream(clean):
                        if "fbcdn.net" in clean and ("mp4" in clean or "video" in clean or "bytestart" in clean):
                            return clean
                        if clean.startswith("http") and not clean.startswith("blob:"):
                            return clean

        return None

    @classmethod
    async def resolve_video_streams(
        cls, page: Page, video_page_url: str, timeout_ms: int = 5000
    ) -> tuple[str | None, str | None]:
        """
        在 Playwright 已登入的 session 瀏覽器中開啟影片貼文網址，
        精準分離並提取真實的 .mp4 高畫質視訊軌與音訊軌 CDN 直鏈（已自動移除 bytestart/byteend 分段標頭）
        回傳 (video_stream_url, audio_stream_url)
        """
        if ".mp4" in video_page_url and "fbcdn.net" in video_page_url:
            clean_url = cls.strip_byte_range_params(video_page_url)
            if cls.is_audio_stream(clean_url):
                return None, clean_url
            return clean_url, None

        captured_videos: list[tuple[int, str]] = []  # (quality_score, url)
        captured_audios: list[str] = []

        def on_response(response):
            r_url = response.url
            if ".mp4" in r_url and "fbcdn.net" in r_url:
                clean_url = cls.strip_byte_range_params(r_url)
                if cls.is_audio_stream(r_url):
                    if clean_url not in captured_audios:
                        captured_audios.append(clean_url)
                else:
                    q = cls.get_video_stream_quality(r_url)
                    if not any(u == clean_url for _, u in captured_videos):
                        captured_videos.append((q, clean_url))

        page.on("response", on_response)
        logger.debug(f"正在嘗試透過瀏覽器解析影片直鏈：{video_page_url[:80]}...")

        try:
            v_page = await page.context.new_page()
            v_page.on("response", on_response)

            try:
                await v_page.goto(video_page_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await v_page.wait_for_timeout(2000)

                # 嘗試從 DOM 點擊播放以觸發影片串流
                try:
                    video_elem = v_page.locator("video, div[data-video-id], div[aria-label*='播放']").first
                    if await video_elem.is_visible(timeout=500):
                        await video_elem.hover()
                except Exception:
                    pass

                # 1. 優先檢查網路攔截到的串流（選取最高畫質視訊軌）
                if captured_videos:
                    captured_videos.sort(key=lambda x: x[0], reverse=True)
                    video_url = captured_videos[0][1]
                    audio_url = captured_audios[0] if captured_audios else None
                    q_label = f" ({captured_videos[0][0]}p)" if captured_videos[0][0] > 0 else ""
                    logger.info(f"✓ 成功攔截到影片視訊軌{q_label}與音訊軌：{video_url[:80]}...")
                    return video_url, audio_url

                # 2. 檢查頁面 HTML 中的 JSON 標籤
                html = await v_page.content()
                extracted = cls.extract_from_html(html)
                if extracted:
                    logger.info(f"✓ 成功從頁面原始碼解析影片直鏈：{extracted[:80]}...")
                    return extracted, None

            finally:
                await v_page.close()

        except Exception as e:
            logger.debug(f"瀏覽器解析影片失敗：{e}")

        return None, None

    @classmethod
    async def resolve_video_url(cls, page: Page, video_page_url: str, timeout_ms: int = 5000) -> str | None:
        """向後相容的 resolve_video_url"""
        video_url, _ = await cls.resolve_video_streams(page, video_page_url, timeout_ms)
        return video_url
