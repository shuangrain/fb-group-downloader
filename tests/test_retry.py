from pathlib import Path

from fb_group_downloader.db import Database
from fb_group_downloader.downloader.models import MediaItem, MediaType


def test_database_failure_recording_and_recovery(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)

    item = MediaItem(
        group_id="12345",
        media_type=MediaType.IMAGE,
        source_url="https://example.com/failed.jpg",
        media_id="img_001",
    )

    # 1. 記錄失敗
    db.record_failure(item, error_msg="HTTP 500 Internal Error")
    pending = db.get_pending_failures("12345")
    assert len(pending) == 1
    assert pending[0].source_url == "https://example.com/failed.jpg"

    # 2. 再次失敗增加 retry_count
    db.record_failure(item, error_msg="HTTP 502 Bad Gateway")
    stats = db.get_stats()
    assert stats["pending_failures"] == 1

    # 3. 成功下載後自動清除失敗
    from fb_group_downloader.downloader.models import DownloadRecord

    rec = DownloadRecord(
        group_id="12345",
        media_id="img_001",
        media_type=MediaType.IMAGE,
        original_url="https://example.com/failed.jpg",
        local_filepath=str(tmp_path / "failed.jpg"),
        file_size=1024,
    )
    db.add_record(rec)

    # 驗證失敗記錄已被清除
    pending_after = db.get_pending_failures("12345")
    assert len(pending_after) == 0
    assert db.is_downloaded("12345", original_url="https://example.com/failed.jpg") is True
