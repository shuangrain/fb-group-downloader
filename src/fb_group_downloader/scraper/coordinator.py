from rich.table import Table

from fb_group_downloader.auth.session import SessionManager
from fb_group_downloader.config import AppConfig, GroupConfig
from fb_group_downloader.db import Database
from fb_group_downloader.downloader.manager import DownloadManager
from fb_group_downloader.downloader.models import DownloadRecord
from fb_group_downloader.scraper.base import BaseScraper
from fb_group_downloader.scraper.group_feed import GroupFeedScraper
from fb_group_downloader.scraper.group_files import GroupFilesScraper
from fb_group_downloader.scraper.group_media import GroupMediaScraper
from fb_group_downloader.utils.logger import console, get_logger

logger = get_logger()


class SyncCoordinator:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.session_mgr = SessionManager(config.session_file)

    async def run_sync_for_group(
        self, base_scraper: BaseScraper, download_mgr: DownloadManager, group_cfg: GroupConfig
    ) -> list[DownloadRecord]:
        """對單個社團執行增量抓取與下載（以貼文與相簿為獨立單位）"""
        group_id = group_cfg.group_id
        group_name = group_cfg.name or group_id
        logger.info(f"========== 開始同步社團：{group_name} ({group_id}) ==========")

        sync_state = self.db.get_sync_state(group_id)
        last_seen_post_id = sync_state.get("last_seen_post_id") if sync_state else None
        if last_seen_post_id:
            logger.info(f"上次同步進度貼文 ID: {last_seen_post_id}")

        group_downloaded: list[DownloadRecord] = []

        # 0. 先自動重試先前下載失敗的項目
        retry_recs = await download_mgr.retry_pending_failures(group_id)
        if retry_recs:
            group_downloaded.extend(retry_recs)

        page = await base_scraper.new_page()
        newest_post_id: str | None = None

        try:
            # 1. 掃描動態貼文（以 PostBundle 貼文為單位）
            if group_cfg.scan_feed:
                feed_scraper = GroupFeedScraper(base_scraper, group_cfg)
                post_bundles = await feed_scraper.scan_feed(page, stop_at_post_id=last_seen_post_id)
                if feed_scraper.seen_post_ids:
                    newest_post_id = next(iter(feed_scraper.seen_post_ids))

                logger.info(f"開始處理 {len(post_bundles)} 篇貼文的媒體下載與獨立資料夾歸檔...")
                for post_bundle in post_bundles:
                    recs = await download_mgr.process_post(post_bundle)
                    group_downloaded.extend(recs)

            # 2. 掃描相簿專區（若啟用 scan_media_tab）
            if group_cfg.scan_media_tab:
                media_scraper = GroupMediaScraper(base_scraper, group_cfg)
                album_bundles = await media_scraper.scan_albums(page)
                logger.info(f"開始處理 {len(album_bundles)} 本相簿的相片下載與獨立資料夾歸檔...")
                for album_bundle in album_bundles:
                    recs = await download_mgr.process_album(album_bundle)
                    group_downloaded.extend(recs)

            # 3. 掃描檔案專區
            if group_cfg.scan_files_tab:
                files_scraper = GroupFilesScraper(base_scraper, group_cfg)
                tab_file_items = await files_scraper.scan_files(page)
                recs = await download_mgr.process_standalone_files(tab_file_items)
                group_downloaded.extend(recs)

            # 4. 更新同步狀態
            self.db.update_sync_state(
                group_id=group_id,
                last_seen_post_id=newest_post_id or last_seen_post_id,
                status="success",
            )

        except Exception as e:
            logger.error(f"同步社團 {group_id} 時發生未預期錯誤：{e}", exc_info=True)
            self.db.update_sync_state(group_id=group_id, status="error")
        finally:
            await page.close()

        return group_downloaded

    async def run_all(self, target_group_id: str | None = None) -> list[DownloadRecord]:
        """執行所有社團（或指定社團）的同步作業"""
        cookies = self.session_mgr.get_cookies_dict()
        download_mgr = DownloadManager(self.config, self.db, cookies=cookies)
        base_scraper = BaseScraper(self.config)

        all_downloaded: list[DownloadRecord] = []

        try:
            await base_scraper.initialize()

            # 決定要執行的社團
            groups_to_sync = (
                [g for g in self.config.groups if g.group_id == target_group_id]
                if target_group_id
                else self.config.groups
            )

            if not groups_to_sync:
                if target_group_id:
                    logger.warning(f"在設定檔中找不到社團 ID：{target_group_id}")
                else:
                    logger.warning("設定檔中未設定任何社團 (groups: [])。")
                return []

            for group_cfg in groups_to_sync:
                recs = await self.run_sync_for_group(base_scraper, download_mgr, group_cfg)
                all_downloaded.extend(recs)

        finally:
            await base_scraper.close()

        # 輸出同步成果統計表格
        self._print_sync_summary(all_downloaded)
        return all_downloaded

    def _print_sync_summary(self, records: list[DownloadRecord]) -> None:
        """以 Rich 表格呈現本次同步摘要"""
        if not records:
            logger.info("本次同步無任何新下載項目（皆為最新或已下載過）。")
            return

        table = Table(title="本次同步下載成果摘要", show_header=True, header_style="bold green")
        table.add_column("類型", style="cyan", width=8)
        table.add_column("社團", style="dim", width=15)
        table.add_column("作者 / 來源", width=18)
        table.add_column("檔案大小", justify="right", width=12)
        table.add_column("儲存路徑")

        for r in records:
            size_mb = r.file_size / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB" if size_mb >= 1.0 else f"{r.file_size / 1024:.1f} KB"
            table.add_row(
                r.media_type.value,
                r.group_id,
                r.post_author or r.album_name or "-",
                size_str,
                r.local_filepath,
            )

        console.print(table)
