import hashlib
import mimetypes
import re
import urllib.parse
from datetime import datetime
from pathlib import Path

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.utils.http import create_async_client
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class FileDownloader:
    def __init__(self, default_storage_dir: Path, timeout: float = 60.0):
        self.default_storage_dir = Path(default_storage_dir)
        self.timeout = timeout

    def _sanitize_filename(self, name: str) -> str:
        name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
        return name or "unnamed_file"

    def _extract_filename_from_headers(self, headers: dict, fallback: str | None = None) -> str:
        cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
        if "filename*=" in cd:
            match = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
            if match:
                return urllib.parse.unquote(match.group(1))

        if "filename=" in cd:
            match = re.search(r'filename="?([^";]+)"?', cd, re.IGNORECASE)
            if match:
                return urllib.parse.unquote(match.group(1))

        if fallback:
            return fallback

        content_type = headers.get("content-type", "").split(";")[0]
        ext = mimetypes.guess_extension(content_type) or ".bin"
        return f"file_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{ext}"

    def _extract_filename_from_url(self, url: str) -> str | None:
        """嘗試從 URL 本身解析檔案名稱"""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "download":
            raw_name = parts[-1]
            try:
                unquoted = urllib.parse.unquote(raw_name)
                if "." in unquoted:
                    return unquoted
            except Exception:
                pass
        return None

    async def download(
        self,
        item: MediaItem,
        target_dir: Path | None = None,
        cookies: dict | None = None,
        headers: dict | None = None,
    ) -> DownloadRecord | None:
        """下載檔案/附件至指定資料夾"""
        output_dir = target_dir or (self.default_storage_dir / item.group_id / "files")
        output_dir.mkdir(parents=True, exist_ok=True)

        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Referer": f"https://www.facebook.com/groups/{item.group_id}/",
        }
        if headers:
            req_headers.update(headers)

        target_url = item.source_url

        try:
            async with create_async_client(
                cookies=cookies,
                headers=req_headers,
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                resp = await client.get(target_url)

                # 若 HTTP 400 且 URL 中含有編碼路徑，嘗試使用純淨 download ID 網址重試
                if resp.status_code == 400 and "/download/" in target_url:
                    m = re.search(r"/download/(\d+)", target_url)
                    if m:
                        clean_download_url = f"https://www.facebook.com/download/{m.group(1)}/"
                        logger.debug(f"重試乾淨下載網址：{clean_download_url}")
                        resp = await client.get(clean_download_url)

                if resp.status_code != 200:
                    logger.warning(f"下載檔案失敗 HTTP {resp.status_code}: {target_url[:80]}...")
                    return None

                content = resp.content
                if not content:
                    logger.warning(f"檔案內容為空：{target_url[:80]}...")
                    return None

                sha256_hash = hashlib.sha256(content).hexdigest()

                # 解析檔名：優先從 Header > URL 解析 > item.filename
                url_filename = self._extract_filename_from_url(target_url)
                fallback_name = url_filename or item.filename
                raw_filename = self._extract_filename_from_headers(dict(resp.headers), fallback=fallback_name)
                clean_filename = self._sanitize_filename(raw_filename)

                # 避免檔名覆蓋：若已有同名檔案且 hash 不同，則加上日期後綴
                file_path = output_dir / clean_filename
                if file_path.exists():
                    with open(file_path, "rb") as existing_f:
                        existing_hash = hashlib.sha256(existing_f.read()).hexdigest()
                    if existing_hash != sha256_hash:
                        stem = file_path.stem
                        suffix = file_path.suffix
                        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                        clean_filename = f"{stem}_{ts}{suffix}"
                        file_path = output_dir / clean_filename

                with open(file_path, "wb") as f:
                    f.write(content)

                file_size = len(content)
                logger.info(f"✓ 檔案下載完成：{clean_filename} ({file_size / 1024:.1f} KB)")

                return DownloadRecord(
                    group_id=item.group_id,
                    post_id=item.post_id,
                    album_id=item.album_id,
                    album_name=item.album_name,
                    media_id=item.media_id or clean_filename,
                    media_type=MediaType.FILE,
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

        except Exception as e:
            logger.error(f"下載檔案時發生例外錯誤：{e}")
            return None
