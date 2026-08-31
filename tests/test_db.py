from pathlib import Path

from fb_group_downloader.db import Database
from fb_group_downloader.downloader.models import DownloadRecord, MediaType


def test_database_crud_and_deduplication(tmp_path: Path):
    db_file = tmp_path / "test.db"
    db = Database(db_file)

    # 1. 初始狀態
    assert not db.is_downloaded(group_id="group1", media_id="img_1")
    assert not db.is_downloaded(group_id="group1", original_url="https://example.com/a.jpg")
    assert not db.is_downloaded(group_id="group1", sha256="abc123hash")

    # 2. 新增一筆記錄
    record = DownloadRecord(
        group_id="group1",
        post_id="post_100",
        media_id="img_1",
        media_type=MediaType.IMAGE,
        original_url="https://example.com/a.jpg",
        local_filepath="/downloads/group1/images/a.jpg",
        file_size=1024,
        sha256="abc123hash",
        post_author="Test User",
        post_text="Hello world",
    )
    rec_id = db.add_record(record)
    assert rec_id > 0

    # 3. 測試去重查詢
    assert db.is_downloaded(group_id="group1", media_id="img_1")
    assert db.is_downloaded(group_id="group1", original_url="https://example.com/a.jpg")
    assert db.is_downloaded(group_id="group1", sha256="abc123hash")
    # 不同 group 但相同 sha256 也能識別
    assert db.is_downloaded(group_id="group2", sha256="abc123hash")

    # 4. 統計與同步狀態
    stats = db.get_stats("group1")
    assert stats["total_count"] == 1
    assert stats["images"] == 1
    assert stats["videos"] == 0
    assert stats["files"] == 0
    assert stats["total_bytes"] == 1024

    db.update_sync_state(group_id="group1", last_seen_post_id="post_100", status="success")
    sync_state = db.get_sync_state("group1")
    assert sync_state is not None
    assert sync_state["last_seen_post_id"] == "post_100"
    assert sync_state["status"] == "success"

    # 5. 最近下載列表
    recent = db.get_recent_downloads(limit=10)
    assert len(recent) == 1
    assert recent[0].post_author == "Test User"
