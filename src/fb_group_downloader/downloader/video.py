import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
from pathlib import Path

import yt_dlp

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.scraper.video_extractor import FacebookVideoExtractor
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
    # 最小有效影片大小限制 (50 KB)，低於此大小代表為分段標頭 (DASH chunk) 或錯誤頁面
    MIN_VALID_VIDEO_BYTES = 50 * 1024

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

    async def _download_stream_to_file(
        self, url: str, output_file: Path, cookies: dict | None = None, headers: dict | None = None
    ) -> bool:
        """串流下載單個 URL 到指定檔案，自動剝離 bytestart/byteend 以取得完整檔案"""
        clean_url = FacebookVideoExtractor.strip_byte_range_params(url)
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.facebook.com/",
        }
        if headers:
            req_headers.update(headers)

        try:
            async with create_async_client(
                cookies=cookies, headers=req_headers, timeout=self.timeout, follow_redirects=True
            ) as client:
                async with client.stream("GET", clean_url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(f"串流下載失敗 HTTP {resp.status_code}：{clean_url[:80]}...")
                        return False
                    with open(output_file, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
            return True
        except Exception as e:
            logger.warning(f"串流下載例外：{e}")
            return False

    async def _download_direct_stream(
        self,
        item: MediaItem,
        output_file: Path,
        cookies: dict | None = None,
        headers: dict | None = None,
    ) -> bool:
        """直接透過 HTTP 下載 CDN 串流，若具備獨立音訊軌且環境有 ffmpeg 則自動合併音畫"""
        ffmpeg_bin = shutil.which("ffmpeg")
        clean_source_url = FacebookVideoExtractor.strip_byte_range_params(item.source_url)
        clean_audio_url = FacebookVideoExtractor.strip_byte_range_params(item.audio_url) if item.audio_url else None

        # 容錯處理：若 source_url 誤為純音訊軌，而 audio_url 為視訊軌，自動校正調換
        if FacebookVideoExtractor.is_audio_stream(clean_source_url):
            if clean_audio_url and not FacebookVideoExtractor.is_audio_stream(clean_audio_url):
                clean_source_url, clean_audio_url = clean_audio_url, clean_source_url
            else:
                logger.warning(f"偵測到來源為純音訊串流，缺少視訊畫面軌，跳過下載：{clean_source_url[:80]}...")
                return False

        # 若來源為 DASH 分離串流卻缺少音訊軌，回傳 False 以便觸發備用下載策略 (yt-dlp) 抓取完整影音
        if FacebookVideoExtractor.is_dash_stream(clean_source_url) and not clean_audio_url:
            logger.info(f"偵測到 DASH 視訊串流缺少對應音訊軌，轉交備用下載策略：{clean_source_url[:80]}...")
            return False

        # 情況 A：若存在獨立音訊軌且系統支援 ffmpeg，分別下載視訊與音訊後進行轉碼/封裝合併
        if clean_audio_url and ffmpeg_bin:
            temp_v = output_file.with_suffix(".temp_v.mp4")
            temp_a = output_file.with_suffix(".temp_a.mp4")
            try:
                v_ok = await self._download_stream_to_file(clean_source_url, temp_v, cookies, headers)
                a_ok = await self._download_stream_to_file(clean_audio_url, temp_a, cookies, headers)

                if v_ok and a_ok and temp_v.exists() and temp_v.stat().st_size >= self.MIN_VALID_VIDEO_BYTES:
                    # 檢查視訊是否為 VP9，若為 VP9 轉碼為標準 H.264 以確保 Windows / Mac / 手機皆能順暢播放畫面
                    is_vp9 = FacebookVideoExtractor.is_vp9_stream(clean_source_url)
                    v_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"] if is_vp9 else ["-c:v", "copy"]

                    cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-i",
                        str(temp_v),
                        "-i",
                        str(temp_a),
                        *v_codec,
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        str(output_file),
                    ]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                    )
                    _, stderr = await proc.communicate()

                    if (
                        proc.returncode == 0
                        and output_file.exists()
                        and output_file.stat().st_size >= self.MIN_VALID_VIDEO_BYTES
                    ):
                        logger.debug(f"✓ 透過 ffmpeg 成功合併視訊與音訊軌：{output_file.name}")
                        return True
                    else:
                        err_text = stderr.decode("utf-8", errors="ignore") if stderr else ""
                        logger.warning(f"ffmpeg 影音合成失敗 (code {proc.returncode})：{err_text[:200]}")
            finally:
                temp_v.unlink(missing_ok=True)
                temp_a.unlink(missing_ok=True)

        # 情況 B：直接下載完整視訊軌
        ok = await self._download_stream_to_file(clean_source_url, output_file, cookies, headers)
        if not ok or not output_file.exists() or output_file.stat().st_size < self.MIN_VALID_VIDEO_BYTES:
            size = output_file.stat().st_size if output_file.exists() else 0
            logger.warning(f"串流檔案過小 ({size} bytes)，判定為非完整影片或無效分片。")
            output_file.unlink(missing_ok=True)
            return False

        # 若單一檔案為 VP9，亦轉碼為 H.264 以保證通用相容性
        if ffmpeg_bin and FacebookVideoExtractor.is_vp9_stream(clean_source_url):
            temp_conv = output_file.with_suffix(".temp_conv.mp4")
            try:
                cmd = [
                    ffmpeg_bin,
                    "-y",
                    "-i",
                    str(output_file),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(temp_conv),
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                if (
                    proc.returncode == 0
                    and temp_conv.exists()
                    and temp_conv.stat().st_size >= self.MIN_VALID_VIDEO_BYTES
                ):
                    temp_conv.replace(output_file)
            finally:
                temp_conv.unlink(missing_ok=True)

        return True

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
        """下載影片檔案至指定資料夾，並校驗檔案完整性"""
        output_dir = target_dir or (self.default_storage_dir / item.group_id / "videos")
        output_dir.mkdir(parents=True, exist_ok=True)

        media_tag = self._sanitize_filename(item.media_id) if item.media_id else f"{index:02d}"
        filename = f"video_{index:02d}_{media_tag}.mp4"
        file_path = output_dir / filename
        old_size = file_path.stat().st_size if file_path.exists() else 0

        # 確保網址已剝離 bytestart 與 byteend 參數
        item.source_url = FacebookVideoExtractor.strip_byte_range_params(item.source_url)
        if item.audio_url:
            item.audio_url = FacebookVideoExtractor.strip_byte_range_params(item.audio_url)

        success = False

        # 優先策略 1: 如果是攔截到的 direct CDN / MP4 串流網址，直接透過 HTTP 串流下載
        if ".mp4" in item.source_url or "fbcdn.net" in item.source_url:
            success = await self._download_direct_stream(item, file_path, cookies, headers)

        # 備用策略 2: 若直接下載失敗或網址為 FB 貼文/影片頁面，則嘗試 yt-dlp
        if not success or not file_path.exists() or file_path.stat().st_size < self.MIN_VALID_VIDEO_BYTES:
            target_urls = []
            if item.media_id and item.media_id.isdigit():
                target_urls.append(f"https://www.facebook.com/watch/?v={item.media_id}")
                target_urls.append(f"https://www.facebook.com/video.php?v={item.media_id}")
            if item.post_url:
                target_urls.append(item.post_url)
            if item.source_url and item.source_url not in target_urls:
                target_urls.append(item.source_url)

            cookie_path = self._create_temp_cookie_file()
            output_template = str(output_dir / f"video_{index:02d}_{media_tag}.%(ext)s")

            loop = asyncio.get_event_loop()
            for t_url in target_urls:
                result_path_str = await loop.run_in_executor(
                    None, self._download_via_ytdlp, t_url, output_template, cookie_path
                )
                if (
                    result_path_str
                    and Path(result_path_str).exists()
                    and Path(result_path_str).stat().st_size >= self.MIN_VALID_VIDEO_BYTES
                ):
                    file_path = Path(result_path_str)
                    success = True
                    break

            if cookie_path and cookie_path.exists():
                try:
                    cookie_path.unlink()
                except Exception:
                    pass

        # 最終檔案大小與有效性檢查
        if not success or not file_path.exists() or file_path.stat().st_size < self.MIN_VALID_VIDEO_BYTES:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
            logger.warning(f"無法下載完整影片：{item.source_url[:80]}...（若為私密社團加密影片，已自動略過並記錄）")
            return None

        # 計算 SHA256 雜湊
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        sha256_hash = hasher.hexdigest()

        file_size = file_path.stat().st_size
        if old_size > 0 and old_size < file_size:
            logger.warning(
                f"[畫質升級/修復] 以完整高畫質影片取代舊檔案：{file_path.name} "
                f"({old_size / 1024:.1f} KB -> {file_size / (1024 * 1024):.2f} MB)"
            )
        logger.info(f"✓ 影片下載完成：{file_path.name} ({file_size / (1024 * 1024):.2f} MB)")

        return DownloadRecord(
            group_id=item.group_id,
            media_type=MediaType.VIDEO,
            original_url=item.source_url,
            local_filepath=str(file_path),
            file_size=file_size,
            sha256=sha256_hash,
            media_id=item.media_id,
            post_id=item.post_id,
            album_id=item.album_id,
            status="completed",
        )
