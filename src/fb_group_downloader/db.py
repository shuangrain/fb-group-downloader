import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fb_group_downloader.downloader.models import DownloadRecord, MediaItem, MediaType
from fb_group_downloader.scraper.photo_extractor import FacebookPhotoExtractor
from fb_group_downloader.scraper.video_extractor import FacebookVideoExtractor
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _resolve_local_path(local_path: str | None) -> Path | None:
        """處理本機與容器間掛載路徑可能存在的 /app/downloads/ 與 downloads/ 差異"""
        if not local_path:
            return None
        p = Path(local_path)
        if p.exists():
            return p
        if local_path.startswith("/app/downloads/"):
            rel_p = Path(local_path[len("/app/") :])
            if rel_p.exists():
                return rel_p
        elif local_path.startswith("downloads/"):
            app_p = Path("/app") / local_path
            if app_p.exists():
                return app_p
        return p

    @staticmethod
    def is_valid_video_file(file_path: Path | str | None, original_url: str | None = None) -> bool:
        """檢驗本機影片檔案是否具備有效視訊軌且非損毀/純音訊/未完成分片/缺少音訊"""
        if not file_path:
            return False
        p = Path(file_path)
        if not p.exists() or p.stat().st_size < 50 * 1024:
            return False

        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin:
            try:
                # 1. 檢驗視訊畫面軌
                res = subprocess.run(
                    [
                        ffprobe_bin,
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=codec_type",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(p),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if res.returncode != 0 or "video" not in res.stdout.strip().lower():
                    return False

                # 2. 若原始網址為 DASH / VP9 分離串流，檢驗音訊軌是否存在
                if original_url and FacebookVideoExtractor.is_dash_stream(original_url):
                    res_a = subprocess.run(
                        [
                            ffprobe_bin,
                            "-v",
                            "error",
                            "-select_streams",
                            "a:0",
                            "-show_entries",
                            "stream=codec_type",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            str(p),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if res_a.returncode == 0 and "audio" not in res_a.stdout.strip().lower():
                        logger.warning(f"偵測到影片缺少音訊軌 (DASH 串流未合成音訊)：{p.name}")
                        return False

                return True
            except Exception:
                return False

        # 若環境未安裝 ffprobe，檢查 MP4 容器結構特徵
        try:
            with open(p, "rb") as f:
                header = f.read(1024 * 1024)
                if b"ftyp" not in header[:64]:
                    return False
                if b"vide" not in header:
                    return False
                return True
        except Exception:
            return False

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
                    # 1. 若為影片，檢查是否為歷史損毀分片、純音訊軌、缺少視訊軌、缺少音訊或檔案過小
                    if media_type == "video":
                        p = self._resolve_local_path(local_path)
                        is_chunk_url = "bytestart=" in (orig_url or "")
                        if is_chunk_url or not p or not p.exists() or not self.is_valid_video_file(p, orig_url):
                            actual_size = p.stat().st_size if (p and p.exists()) else file_size
                            logger.warning(
                                f"[畫質升級/修復] 偵測到歷史損毀或缺少畫面/聲音之影片 ({actual_size} bytes)，清除舊檔以重新下載完整影片：{local_path or media_id}"
                            )
                            if p and p.exists():
                                p.unlink(missing_ok=True)
                            cursor.execute(
                                "DELETE FROM downloads WHERE group_id = ? AND media_id = ?", (group_id, media_id)
                            )
                            conn.commit()
                            return False
                    # 2. 若為圖片且網址屬於縮圖標記（或檔案過小且帶有縮圖參數），自動刪除舊檔案與記錄，觸發以高畫質原圖取代
                    if media_type == "image":
                        p = self._resolve_local_path(local_path)
                        if FacebookPhotoExtractor.is_thumbnail_url(orig_url) or (
                            file_size < 100 * 1024
                            and ("ctp=s" in orig_url or "/s526x296/" in orig_url or "/p720x720/" in orig_url)
                        ):
                            actual_size = p.stat().st_size if (p and p.exists()) else file_size
                            logger.warning(
                                f"[畫質升級/修復] 偵測到低畫質縮圖 ({actual_size} bytes)，清除舊檔以重新抓取高解析度原圖：{local_path or orig_url}"
                            )
                            if p and p.exists():
                                p.unlink(missing_ok=True)
                            cursor.execute(
                                "DELETE FROM downloads WHERE group_id = ? AND media_id = ?", (group_id, media_id)
                            )
                            conn.commit()
                            return False
                    return True

            if original_url:
                cursor.execute(
                    "SELECT id, file_size, media_type, local_filepath FROM downloads WHERE group_id = ? AND original_url = ? LIMIT 1",
                    (group_id, original_url),
                )
                row = cursor.fetchone()
                if row:
                    rec_id, file_size, media_type, local_path = row
                    if media_type == "video":
                        p = self._resolve_local_path(local_path)
                        is_chunk_url = "bytestart=" in (original_url or "")
                        if is_chunk_url or not p or not p.exists() or not self.is_valid_video_file(p, original_url):
                            actual_size = p.stat().st_size if (p and p.exists()) else file_size
                            logger.warning(
                                f"[畫質升級/修復] 偵測到歷史損毀或缺少畫面/聲音之影片 ({actual_size} bytes)，清除舊檔以重新下載完整影片：{local_path or original_url}"
                            )
                            if p and p.exists():
                                p.unlink(missing_ok=True)
                            cursor.execute("DELETE FROM downloads WHERE id = ?", (rec_id,))
                            conn.commit()
                            return False
                    if media_type == "image":
                        p = self._resolve_local_path(local_path)
                        if FacebookPhotoExtractor.is_thumbnail_url(original_url) or (
                            file_size < 100 * 1024
                            and (
                                "ctp=s" in original_url or "/s526x296/" in original_url or "/p720x720/" in original_url
                            )
                        ):
                            actual_size = p.stat().st_size if (p and p.exists()) else file_size
                            logger.warning(
                                f"[畫質升級/修復] 偵測到低畫質縮圖 ({actual_size} bytes)，清除舊檔以重新抓取高解析度原圖：{local_path or original_url}"
                            )
                            if p and p.exists():
                                p.unlink(missing_ok=True)
                            cursor.execute("DELETE FROM downloads WHERE id = ?", (rec_id,))
                            conn.commit()
                            return False
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
        """清除歷史資料庫中損毀的影片分片/缺少畫面之影片與低解析度縮圖，使後續能下載完整高畫質內容"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            v_filter = " AND group_id = ?" if group_id else ""
            params = (group_id,) if group_id else ()

            # 刪除損毀影片（含分段碎片、純音訊或缺少畫面）
            cursor.execute(
                f"SELECT id, original_url, local_filepath FROM downloads WHERE media_type = 'video'{v_filter}",
                params,
            )
            v_rows = cursor.fetchall()
            v_to_delete = []
            for rec_id, orig_url, path_str in v_rows:
                p = self._resolve_local_path(path_str)
                is_chunk = "bytestart=" in (orig_url or "")
                if is_chunk or not p or not p.exists() or not self.is_valid_video_file(p, orig_url):
                    v_to_delete.append(rec_id)
                    if p and p.exists():
                        p.unlink(missing_ok=True)

            for rec_id in v_to_delete:
                cursor.execute("DELETE FROM downloads WHERE id = ?", (rec_id,))
            v_deleted = len(v_to_delete)

            # 刪除低解析度圖片
            cursor.execute(
                f"SELECT id, local_filepath FROM downloads WHERE media_type = 'image' AND file_size < 80000 AND (original_url LIKE '%ctp=s%' OR original_url LIKE '%/s526x296/%' OR original_url LIKE '%/p720x720/%'){v_filter}",
                params,
            )
            img_rows = cursor.fetchall()
            img_to_delete = []
            for rec_id, path_str in img_rows:
                img_to_delete.append(rec_id)
                p = self._resolve_local_path(path_str)
                if p and p.exists():
                    p.unlink(missing_ok=True)

            for rec_id in img_to_delete:
                cursor.execute("DELETE FROM downloads WHERE id = ?", (rec_id,))
            img_deleted = len(img_to_delete)

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
