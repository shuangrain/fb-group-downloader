import hashlib
import mimetypes
import re
from pathlib import Path

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.utils.http import create_async_client
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class ImageDownloader:
    def __init__(self, default_storage_dir: Path, timeout: float = 30.0):
        self.default_storage_dir = Path(default_storage_dir)
        self.timeout = timeout

    def _sanitize_filename(self, name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    async def download(
        self,
        item: MediaItem,
        target_dir: Path | None = None,
        index: int = 1,
        cookies: dict | None = None,
        headers: dict | None = None,
    ) -> DownloadRecord | None:
        """下載單張圖片至指定資料夾，計算雜湊值與檔案大小"""
        output_dir = target_dir or (self.default_storage_dir / item.group_id / "images")
        output_dir.mkdir(parents=True, exist_ok=True)

        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://www.facebook.com/",
        }
        if headers:
            req_headers.update(headers)

        try:
            async with create_async_client(
                cookies=cookies, headers=req_headers, timeout=self.timeout, follow_redirects=True
            ) as client:
                resp = await client.get(item.source_url)
                if resp.status_code != 200:
                    logger.warning(f"下載圖片失敗 HTTP {resp.status_code}: {item.source_url[:80]}...")
                    return None

                content = resp.content
                if not content:
                    logger.warning(f"圖片內容為空：{item.source_url[:80]}...")
                    return None

                # 計算 SHA256 雜湊
                sha256_hash = hashlib.sha256(content).hexdigest()

                # 決定副檔名
                content_type = resp.headers.get("Content-Type", "")
                ext = mimetypes.guess_extension(content_type.split(";")[0])
                if not ext or ext in [".jpe", ".jpeg"]:
                    ext = ".jpg"
                elif ext == ".png":
                    ext = ".png"
                elif ext == ".webp":
                    ext = ".webp"

                # 檔名命名規則：photo_{index:02d}_{media_id}{ext}
                media_tag = self._sanitize_filename(item.media_id) if item.media_id else sha256_hash[:8]
                filename = f"photo_{index:02d}_{media_tag}{ext}"
                file_path = output_dir / filename

                # 寫入檔案
                with open(file_path, "wb") as f:
                    f.write(content)

                file_size = len(content)
                logger.info(f"✓ 圖片下載完成：{filename} ({file_size / 1024:.1f} KB)")

                return DownloadRecord(
                    group_id=item.group_id,
                    post_id=item.post_id,
                    album_id=item.album_id,
                    album_name=item.album_name,
                    media_id=item.media_id,
                    media_type=MediaType.IMAGE,
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
            logger.error(f"下載圖片時發生例外錯誤：{e}")
            return None
