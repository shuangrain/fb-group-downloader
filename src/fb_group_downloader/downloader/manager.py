import json
from pathlib import Path

from fb_group_downloader.config import AppConfig
from fb_group_downloader.db import Database
from fb_group_downloader.downloader.file import FileDownloader
from fb_group_downloader.downloader.image import ImageDownloader
from fb_group_downloader.downloader.models import (
    AlbumBundle,
    DownloadRecord,
    MediaItem,
    MediaType,
    PostBundle,
)
from fb_group_downloader.downloader.video import VideoDownloader
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class DownloadManager:
    def __init__(self, config: AppConfig, db: Database, cookies: dict | None = None):
        self.config = config
        self.db = db
        self.cookies = cookies or {}
        self.storage_dir = config.storage_dir

        self.image_downloader = ImageDownloader(self.storage_dir)
        self.video_downloader = VideoDownloader(self.storage_dir, session_file=config.session_file)
        self.file_downloader = FileDownloader(self.storage_dir)

    def _save_post_metadata(self, post_bundle: PostBundle, target_dir: Path) -> None:
        """在貼文資料夾中寫入貼文詳細文字資訊 (post_info.txt 與 post_info.json)"""
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            # 1. 寫入 post_info.txt (人類易讀格式)
            txt_path = target_dir / "post_info.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"社團名稱: {post_bundle.group_name or post_bundle.group_id}\n")
                f.write(f"發文作者: {post_bundle.post_author or '未知'}\n")
                f.write(f"發布時間: {post_bundle.post_time or '未知'}\n")
                f.write(f"貼文網址: {post_bundle.post_url or '未知'}\n")
                f.write(f"貼文 ID: {post_bundle.post_id}\n")
                f.write(f"媒體數量: {len(post_bundle.media_items)}\n")
                f.write("-" * 40 + "\n")
                f.write("貼文內容:\n")
                f.write(post_bundle.post_text or "(無內文)")
                f.write("\n")

            # 2. 寫入 post_info.json (結構化資料)
            json_path = target_dir / "post_info.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(post_bundle.model_dump(mode="json"), f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.warning(f"儲存貼文資訊檔失敗：{e}")

    def _save_album_metadata(self, album_bundle: AlbumBundle, target_dir: Path) -> None:
        """在相簿資料夾中寫入相簿資訊 (album_info.json)"""
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            json_path = target_dir / "album_info.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(album_bundle.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"儲存相簿資訊檔失敗：{e}")

    async def process_post(self, post_bundle: PostBundle) -> list[DownloadRecord]:
        """
        以「貼文」為獨立單位進行下載，儲存至 yyyyMMdd <name> 獨立資料夾
        """
        if not post_bundle.media_items:
            return []

        # 檢查是否有尚未下載的項目
        items_to_download = [
            item
            for item in post_bundle.media_items
            if not self.db.is_downloaded(
                group_id=item.group_id,
                media_id=item.media_id,
                original_url=item.source_url,
            )
        ]

        if not items_to_download:
            logger.debug(f"貼文 {post_bundle.post_id} 所有媒體皆已下載過，跳過。")
            return []

        # 建立貼文專屬資料夾：downloads/<group_name_or_id>/posts/yyyyMMdd <name>/
        group_folder = post_bundle.get_group_folder_name()
        folder_name = post_bundle.get_folder_name()
        post_dir = self.storage_dir / group_folder / "posts" / folder_name
        post_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"📂 建立/使用貼文資料夾：{post_dir.parent.name}/{post_dir.name}")
        self._save_post_metadata(post_bundle, post_dir)

        results: list[DownloadRecord] = []
        img_idx = 1
        vid_idx = 1

        for item in items_to_download:
            rec: DownloadRecord | None = None
            if item.media_type == MediaType.IMAGE:
                rec = await self.image_downloader.download(
                    item, target_dir=post_dir, index=img_idx, cookies=self.cookies
                )
                img_idx += 1
            elif item.media_type == MediaType.VIDEO:
                rec = await self.video_downloader.download(
                    item, target_dir=post_dir, index=vid_idx, cookies=self.cookies
                )
                vid_idx += 1
            elif item.media_type == MediaType.FILE:
                rec = await self.file_downloader.download(item, target_dir=post_dir, cookies=self.cookies)

            if rec:
                self.db.add_record(rec)
                results.append(rec)
            else:
                # 下載失敗時，寫入失敗重試資料表
                self.db.record_failure(item, error_msg="Download failed", target_dir=str(post_dir.resolve()))

        return results

    async def process_album(self, album_bundle: AlbumBundle) -> list[DownloadRecord]:
        """
        以「相簿」為獨立單位進行下載，儲存至 yyyyMMdd <name> 獨立資料夾
        """
        if not album_bundle.media_items:
            return []

        items_to_download = [
            item
            for item in album_bundle.media_items
            if not self.db.is_downloaded(
                group_id=item.group_id,
                media_id=item.media_id,
                original_url=item.source_url,
            )
        ]

        if not items_to_download:
            logger.debug(f"相簿 {album_bundle.album_name} 所有相片皆已下載過，跳過。")
            return []

        # 建立相簿專屬資料夾：downloads/<group_name_or_id>/albums/yyyyMMdd <name>/
        group_folder = album_bundle.get_group_folder_name()
        folder_name = album_bundle.get_folder_name()
        album_dir = self.storage_dir / group_folder / "albums" / folder_name
        album_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"📁 建立/使用相簿資料夾：{album_dir.parent.name}/{album_dir.name}")
        self._save_album_metadata(album_bundle, album_dir)

        results: list[DownloadRecord] = []
        for idx, item in enumerate(items_to_download, start=1):
            rec = await self.image_downloader.download(item, target_dir=album_dir, index=idx, cookies=self.cookies)
            if rec:
                self.db.add_record(rec)
                results.append(rec)
            else:
                self.db.record_failure(
                    item, error_msg="Album image download failed", target_dir=str(album_dir.resolve())
                )

        return results

    async def process_standalone_files(self, items: list[MediaItem]) -> list[DownloadRecord]:
        """處理來自社團檔案專區 (/files) 的獨立文件"""
        results: list[DownloadRecord] = []
        for item in items:
            if self.db.is_downloaded(
                group_id=item.group_id,
                media_id=item.media_id,
                original_url=item.source_url,
            ):
                continue
            group_folder = item.get_group_folder_name()
            target_dir = self.storage_dir / group_folder / "files"
            rec = await self.file_downloader.download(item, target_dir=target_dir, cookies=self.cookies)
            if rec:
                self.db.add_record(rec)
                results.append(rec)
            else:
                self.db.record_failure(item, error_msg="File download failed", target_dir=str(target_dir.resolve()))
        return results

    async def retry_pending_failures(self, group_id: str) -> list[DownloadRecord]:
        """自動重試特定社團先前下載失敗的所有項目"""
        pending_items = self.db.get_pending_failures(group_id)
        if not pending_items:
            return []

        logger.info(f"🔄 發現 {len(pending_items)} 個先前下載失敗的項目，正在自動重新嘗試下載...")
        recovered: list[DownloadRecord] = []

        for item in pending_items:
            # 檢查是否已下載過
            if self.db.is_downloaded(group_id=item.group_id, original_url=item.source_url):
                continue

            rec: DownloadRecord | None = None
            if item.media_type == MediaType.IMAGE:
                rec = await self.image_downloader.download(item, cookies=self.cookies)
            elif item.media_type == MediaType.VIDEO:
                rec = await self.video_downloader.download(item, cookies=self.cookies)
            elif item.media_type == MediaType.FILE:
                rec = await self.file_downloader.download(item, cookies=self.cookies)

            if rec:
                self.db.add_record(rec)
                recovered.append(rec)
                logger.info(f"🎉 重試下載成功：{rec.local_filepath}")
            else:
                self.db.record_failure(item, error_msg="Retry failed")

        return recovered
