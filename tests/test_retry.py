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

    # 1. 記錄失敗（含 target_dir）
    expected_dir = str(tmp_path / "測試社團" / "posts" / "20260831 貼文")
    db.record_failure(item, error_msg="HTTP 500 Internal Error", target_dir=expected_dir)
    pending = db.get_pending_failures("12345")
    assert len(pending) == 1
    assert pending[0].source_url == "https://example.com/failed.jpg"
    assert pending[0].target_dir == expected_dir

    # 2. 再次失敗增加 retry_count（未傳 target_dir 時亦應保留原 folder_path）
    db.record_failure(item, error_msg="HTTP 502 Bad Gateway")
    pending_retry = db.get_pending_failures("12345")
    assert pending_retry[0].target_dir == expected_dir
    stats = db.get_stats()
    assert stats["pending_failures"] == 1

    # 3. 測試空資料夾自動清理
    empty_num_dir = tmp_path / "12345" / "images"
    empty_num_dir.mkdir(parents=True, exist_ok=True)
    assert empty_num_dir.exists()
    db.cleanup_corrupted_and_low_res_records("12345")
    assert not empty_num_dir.exists()
    assert not (tmp_path / "12345").exists()

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
