import asyncio
import hashlib
import json
import logging
import re
import tempfile
from pathlib import Path

import yt_dlp

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.utils.http import create_async_client
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class YtdlLogger:
    """
    yt-dlp 日誌轉發器：
    - 一般模式：靜音，防止 yt-dlp 報錯干擾終端輸出
    - Debug 模式 (--debug)：將 yt-dlp 的 debug/info/warning/error 完整輸出至 logger.debug
    """

    def debug(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG) and not msg.startswith("[debug] "):
            logger.debug(f"[yt-dlp debug] {msg}")

    def info(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[yt-dlp info] {msg}")

    def warning(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[yt-dlp warning] {msg}")

    def error(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[bold red][yt-dlp error][/bold red] {msg}")


class VideoDownloader:
    def __init__(self, default_storage_dir: Path, session_file: Path | None = None, timeout: float = 120.0):
        self.default_storage_dir = Path(default_storage_dir)
        self.session_file = Path(session_file) if session_file else None
        self.timeout = timeout

    def _sanitize_filename(self, name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def _create_temp_cookie_file(self) -> Path | None:
        """將 Playwright session.json 轉換成 yt-dlp 可接受的 Netscape cookie 檔案"""
        if not self.session_file or not self.session_file.exists():
            return None
        try:
            with open(self.session_file, encoding="utf-8") as f:
                data = json.load(f)
            cookies = data.get("cookies", [])

            temp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt", encoding="utf-8")
            temp_file.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                domain = c.get("domain", ".facebook.com")
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                path = c.get("path", "/")
                secure = "TRUE" if c.get("secure", True) else "FALSE"
                expires = str(int(c.get("expires", 0))) if c.get("expires", -1) != -1 else "2147483647"
                name = c.get("name", "")
                value = c.get("value", "")
                temp_file.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
            temp_file.close()
            return Path(temp_file.name)
        except Exception as e:
            logger.warning(f"產生臨時 Cookie 檔案失敗：{e}")
            return None

    async def _download_direct_stream(
        self,
        item: MediaItem,
        output_file: Path,
        cookies: dict | None = None,
        headers: dict | None = None,
    ) -> bool:
        """透過 direct HTTP stream 下載 mp4 影片"""
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Referer": "https://www.facebook.com/",
        }
        if headers:
            req_headers.update(headers)

        try:
            async with create_async_client(
                cookies=cookies, headers=req_headers, timeout=self.timeout, follow_redirects=True
            ) as client:
                async with client.stream("GET", item.source_url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(f"串流下載影片失敗 HTTP {resp.status_code}")
                        return False
                    with open(output_file, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
            return True
        except Exception as e:
            logger.warning(f"直接串流下載失敗：{e}")
            return False

    def _download_via_ytdlp(self, url: str, output_template: str, cookie_file: Path | None = None) -> str | None:
        """使用 yt-dlp 下載 Facebook 影片"""
        is_debug = logger.isEnabledFor(logging.DEBUG)

        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": output_template,
            "quiet": not is_debug,
            "no_warnings": not is_debug,
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "logger": YtdlLogger(),
        }
        if cookie_file and cookie_file.exists():
            ydl_opts["cookiefile"] = str(cookie_file)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    filename = ydl.prepare_filename(info)
                    if not Path(filename).exists():
                        parent = Path(filename).parent
                        stem = Path(filename).stem
                        matches = list(parent.glob(f"{stem}.*"))
                        if matches:
                            return str(matches[0])
                    return filename
        except Exception as e:
            if is_debug:
                logger.debug(f"yt-dlp 執行例外：{e}")
        return None

    async def download(
        self,
        item: MediaItem,
        target_dir: Path | None = None,
        index: int = 1,
        cookies: dict | None = None,
        headers: dict | None = None,
    ) -> DownloadRecord | None:
        """下載影片檔案至指定資料搞"""
        output_dir = target_dir or (self.default_storage_dir / item.group_id / "videos")
        output_dir.mkdir(parents=True, exist_ok=True)

        media_tag = self._sanitize_filename(item.media_id) if item.media_id else f"{index:02d}"
        filename = f"video_{index:02d}_{media_tag}.mp4"
        file_path = output_dir / filename

        success = False

        # 優先策略 1: 如果是攔截到的 direct CDN / MP4 串流網址，直接透過 HTTP 串流下載
        if ".mp4" in item.source_url or "fbcdn.net" in item.source_url:
            success = await self._download_direct_stream(item, file_path, cookies, headers)

        # 備用策略 2: 若直接下載失敗或網址為 FB 貼文/影片頁面，則嘗試 yt-dlp
        if not success or not file_path.exists() or file_path.stat().st_size == 0:
            target_url = item.post_url if item.post_url else item.source_url
            cookie_path = self._create_temp_cookie_file()
            output_template = str(output_dir / f"video_{index:02d}_{media_tag}.%(ext)s")

            loop = asyncio.get_event_loop()
            result_path_str = await loop.run_in_executor(
                None, self._download_via_ytdlp, target_url, output_template, cookie_path
            )

            if cookie_path and cookie_path.exists():
                try:
                    cookie_path.unlink()
                except Exception:
                    pass

            if result_path_str and Path(result_path_str).exists():
                file_path = Path(result_path_str)
                success = True

        if not success or not file_path.exists() or file_path.stat().st_size == 0:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            logger.warning(f"無法下載影片：{item.source_url[:80]}...（若為私密社團加密影片，已自動略過並記錄）")
            return None

        # 計算檔案大小與雜湊
        file_size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            sha256_hash = hashlib.sha256(f.read()).hexdigest()

        logger.info(f"✓ 影片下載完成：{file_path.name} ({file_size / (1024 * 1024):.2f} MB)")

        return DownloadRecord(
            group_id=item.group_id,
            post_id=item.post_id,
            album_id=item.album_id,
            album_name=item.album_name,
            media_id=item.media_id,
            media_type=MediaType.VIDEO,
            original_url=item.source_url,
            local_filepath=str(file_path.resolve()),
            folder_path=str(output_dir.resolve()),
            file_size=file_size,
            sha256=sha256_hash,
            post_author=item.post_author,
            post_text=item.post_text,
            post_url=item.post_url,
            post_time=item.post_time,
        )
