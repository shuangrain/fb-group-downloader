import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    post_id TEXT,
                    album_id TEXT,
                    album_name TEXT,
                    media_id TEXT,
                    media_type TEXT NOT NULL,
                    original_url TEXT NOT NULL,
                    local_filepath TEXT NOT NULL,
                    folder_path TEXT,
                    file_size INTEGER DEFAULT 0,
                    sha256 TEXT,
                    post_author TEXT,
                    post_text TEXT,
                    post_url TEXT,
                    post_time TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    group_id TEXT PRIMARY KEY,
                    last_sync_at TEXT,
                    last_seen_post_id TEXT,
                    total_downloads INTEGER DEFAULT 0,
                    status TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS download_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    media_id TEXT,
                    original_url TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    post_id TEXT,
                    album_id TEXT,
                    folder_path TEXT,
                    item_json TEXT,
                    error_msg TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_failed_at TEXT NOT NULL,
                    UNIQUE(group_id, original_url)
                )
            """)

            # 確保舊版資料庫平滑升級新增欄位
            cursor.execute("PRAGMA table_info(downloads)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "album_id" not in columns:
                cursor.execute("ALTER TABLE downloads ADD COLUMN album_id TEXT")
            if "album_name" not in columns:
                cursor.execute("ALTER TABLE downloads ADD COLUMN album_name TEXT")
            if "folder_path" not in columns:
                cursor.execute("ALTER TABLE downloads ADD COLUMN folder_path TEXT")

            # 索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_media ON downloads (group_id, media_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_url ON downloads (group_id, original_url)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sha256 ON downloads (sha256)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_post_id ON downloads (group_id, post_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_album_id ON downloads (group_id, album_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_fail_group ON download_failures (group_id)")
            conn.commit()

    def is_downloaded(
        self,
        group_id: str,
        media_id: str | None = None,
        original_url: str | None = None,
        sha256: str | None = None,
    ) -> bool:
        """檢查特定項目是否已經下載成功過"""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if media_id:
                cursor.execute(
                    "SELECT file_size, media_type, original_url, local_filepath FROM downloads WHERE group_id = ? AND media_id = ? LIMIT 1",
                    (group_id, media_id),
                )
                row = cursor.fetchone()
                if row:
                    file_size, media_type, orig_url, local_path = row
                    # 1. 若為影片且檔案小於 50KB，判定為歷史殘留的 DASH 分段標頭，視為未下載並自動清除
                    if media_type == "video" and (
                        file_size < 50 * 1024
                        or (local_path and Path(local_path).exists() and Path(local_path).stat().st_size < 50 * 1024)
                    ):
                        if local_path and Path(local_path).exists():
                            Path(local_path).unlink(missing_ok=True)
                        cursor.execute(
                            "DELETE FROM downloads WHERE group_id = ? AND media_id = ?", (group_id, media_id)
                        )
                        conn.commit()
                        return False
                    # 2. 若為圖片且檔案小於 80KB 且帶有動態牆縮圖參數，自動清除以重新抓取高畫質原圖
                    if (
                        media_type == "image"
                        and file_size < 80 * 1024
                        and ("ctp=s" in orig_url or "/s526x296/" in orig_url or "/p720x720/" in orig_url)
                    ):
                        if local_path and Path(local_path).exists():
                            Path(local_path).unlink(missing_ok=True)
                        cursor.execute(
                            "DELETE FROM downloads WHERE group_id = ? AND media_id = ?", (group_id, media_id)
                        )
                        conn.commit()
                        return False
                    return True

            if original_url:
                cursor.execute(
                    "SELECT 1 FROM downloads WHERE group_id = ? AND original_url = ? LIMIT 1",
                    (group_id, original_url),
                )
                if cursor.fetchone():
                    return True

            if sha256:
                cursor.execute(
                    "SELECT 1 FROM downloads WHERE sha256 = ? LIMIT 1",
                    (sha256,),
                )
                if cursor.fetchone():
                    return True

            return False

    def cleanup_corrupted_and_low_res_records(self, group_id: str | None = None) -> tuple[int, int]:
        """清除歷史資料庫中損毀的影片分片 (< 50KB) 與低解析度縮圖 (< 80KB)，使後續能下載完整高畫質內容"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            v_filter = " AND group_id = ?" if group_id else ""
            params = (group_id,) if group_id else ()

            # 刪除損毀影片
            cursor.execute(
                f"SELECT local_filepath FROM downloads WHERE media_type = 'video' AND file_size < 50000{v_filter}",
                params,
            )
            for (path_str,) in cursor.fetchall():
                if path_str:
                    p = Path(path_str)
                    if not p.exists() and path_str.startswith("/app/downloads"):
                        p = Path(path_str.replace("/app/downloads", "downloads"))
                    if p.exists():
                        p.unlink(missing_ok=True)
            cursor.execute(
                f"DELETE FROM downloads WHERE media_type = 'video' AND file_size < 50000{v_filter}",
                params,
            )
            v_deleted = cursor.rowcount

            # 刪除低解析度圖片
            cursor.execute(
                f"SELECT local_filepath FROM downloads WHERE media_type = 'image' AND file_size < 80000 AND (original_url LIKE '%ctp=s%' OR original_url LIKE '%/s526x296/%' OR original_url LIKE '%/p720x720/%'){v_filter}",
                params,
            )
            for (path_str,) in cursor.fetchall():
                if path_str:
                    p = Path(path_str)
                    if not p.exists() and path_str.startswith("/app/downloads"):
                        p = Path(path_str.replace("/app/downloads", "downloads"))
                    if p.exists():
                        p.unlink(missing_ok=True)
            cursor.execute(
                f"DELETE FROM downloads WHERE media_type = 'image' AND file_size < 80000 AND (original_url LIKE '%ctp=s%' OR original_url LIKE '%/s526x296/%' OR original_url LIKE '%/p720x720/%'){v_filter}",
                params,
            )
            img_deleted = cursor.rowcount
            conn.commit()
            return v_deleted, img_deleted

    def add_record(self, record: DownloadRecord) -> int:
        """記錄下載成功的項目，並自動清除該項目的失敗重試紀錄"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO downloads (
                    group_id, post_id, album_id, album_name, media_id,
                    media_type, original_url, local_filepath, folder_path,
                    file_size, sha256, post_author, post_text,
                    post_url, post_time, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.group_id,
                    record.post_id,
                    record.album_id,
                    record.album_name,
                    record.media_id,
                    record.media_type.value if isinstance(record.media_type, MediaType) else str(record.media_type),
                    record.original_url,
                    record.local_filepath,
                    record.folder_path,
                    record.file_size,
                    record.sha256,
                    record.post_author,
                    record.post_text,
                    record.post_url,
                    record.post_time,
                    record.created_at,
                ),
            )
            record_id = cursor.lastrowid or 0

            # 更新 sync_state 的總計
            cursor.execute(
                """
                INSERT INTO sync_state (group_id, last_sync_at, total_downloads, status)
                VALUES (?, ?, 1, 'active')
                ON CONFLICT(group_id) DO UPDATE SET
                    total_downloads = total_downloads + 1,
                    last_sync_at = excluded.last_sync_at
                """,
                (record.group_id, datetime.utcnow().isoformat()),
            )

            # 清除失敗重試隊列中的對應記錄
            cursor.execute(
                "DELETE FROM download_failures WHERE group_id = ? AND original_url = ?",
                (record.group_id, record.original_url),
            )

            conn.commit()
            return record_id

    def record_failure(
        self,
        item: MediaItem,
        error_msg: str,
        target_dir: str | None = None,
    ) -> None:
        """記錄下載失敗的項目，以便下次同步時自動重新嘗試"""
        now = datetime.utcnow().isoformat()
        item_json = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
        media_type_str = item.media_type.value if isinstance(item.media_type, MediaType) else str(item.media_type)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO download_failures (
                    group_id, media_id, original_url, media_type,
                    post_id, album_id, folder_path, item_json,
                    error_msg, retry_count, last_failed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(group_id, original_url) DO UPDATE SET
                    retry_count = retry_count + 1,
                    error_msg = excluded.error_msg,
                    last_failed_at = excluded.last_failed_at,
                    item_json = excluded.item_json
                """,
                (
                    item.group_id,
                    item.media_id,
                    item.source_url,
                    media_type_str,
                    item.post_id,
                    item.album_id,
                    target_dir,
                    item_json,
                    error_msg,
                    now,
                ),
            )
            conn.commit()

    def get_pending_failures(self, group_id: str, max_retries: int = 5) -> list[MediaItem]:
        """取得特定社團尚待重試的失敗下載項目（預設最多重試 5 次）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT item_json FROM download_failures
                WHERE group_id = ? AND retry_count <= ?
                ORDER BY last_failed_at ASC
                """,
                (group_id, max_retries),
            )
            rows = cursor.fetchall()
            items = []
            for r in rows:
                try:
                    data = json.loads(r["item_json"])
                    items.append(MediaItem(**data))
                except Exception:
                    pass
            return items

    def update_sync_state(
        self,
        group_id: str,
        last_seen_post_id: str | None = None,
        status: str = "success",
    ) -> None:
        """更新社團同步狀態"""
        now = datetime.utcnow().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if last_seen_post_id:
                cursor.execute(
                    """
                    INSERT INTO sync_state (group_id, last_sync_at, last_seen_post_id, status)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        last_sync_at = excluded.last_sync_at,
                        last_seen_post_id = excluded.last_seen_post_id,
                        status = excluded.status
                    """,
                    (group_id, now, last_seen_post_id, status),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO sync_state (group_id, last_sync_at, status)
                    VALUES (?, ?, ?)
                    ON CONFLICT(group_id) DO UPDATE SET
                        last_sync_at = excluded.last_sync_at,
                        status = excluded.status
                    """,
                    (group_id, now, status),
                )
            conn.commit()

    def get_sync_state(self, group_id: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sync_state WHERE group_id = ?", (group_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def get_stats(self, group_id: str | None = None) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if group_id:
                cursor.execute(
                    "SELECT COUNT(*), SUM(file_size) FROM downloads WHERE group_id = ?",
                    (group_id,),
                )
                total_count, total_bytes = cursor.fetchone()
                total_count = total_count or 0
                total_bytes = total_bytes or 0

                cursor.execute(
                    "SELECT media_type, COUNT(*) FROM downloads WHERE group_id = ? GROUP BY media_type",
                    (group_id,),
                )
                type_counts = dict(cursor.fetchall())

                cursor.execute(
                    "SELECT COUNT(*) FROM download_failures WHERE group_id = ?",
                    (group_id,),
                )
                fail_count = cursor.fetchone()[0] or 0
            else:
                cursor.execute("SELECT COUNT(*), SUM(file_size) FROM downloads")
                total_count, total_bytes = cursor.fetchone()
                total_count = total_count or 0
                total_bytes = total_bytes or 0

                cursor.execute("SELECT media_type, COUNT(*) FROM downloads GROUP BY media_type")
                type_counts = dict(cursor.fetchall())

                cursor.execute("SELECT COUNT(*) FROM download_failures")
                fail_count = cursor.fetchone()[0] or 0

            return {
                "total_count": total_count,
                "total_bytes": total_bytes,
                "images": type_counts.get(MediaType.IMAGE.value, 0),
                "videos": type_counts.get(MediaType.VIDEO.value, 0),
                "files": type_counts.get(MediaType.FILE.value, 0),
                "pending_failures": fail_count,
            }

    def get_recent_downloads(self, limit: int = 20) -> list[DownloadRecord]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM downloads ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
            return [DownloadRecord(**dict(r)) for r in rows]
