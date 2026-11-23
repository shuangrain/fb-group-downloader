from pathlib import Path

import pytest
import respx

from fb_group_downloader.config import AppConfig
from fb_group_downloader.db import Database
from fb_group_downloader.downloader.image import ImageDownloader
from fb_group_downloader.downloader.manager import DownloadManager
from fb_group_downloader.downloader.models import (
    AlbumBundle,
    MediaItem,
    MediaType,
    PostBundle,
)


def test_post_and_album_folder_naming():
    # 測試 PostBundle 命名規則：yyyyMMdd <name>
    post = PostBundle(
        group_id="group_123",
        post_id="999888777",
        post_author="張小明",
        post_text="這是社團公告活動摘要內容...",
        post_time="2026-08-31T14:30:00Z",
    )
    folder_name = post.get_folder_name()
    assert folder_name.startswith("20260831 ")
    assert "張小明" in folder_name
    assert "這是社團公告活動摘要內容" in folder_name

    # 測試 AlbumBundle 命名規則：yyyyMMdd <name>
    album = AlbumBundle(
        group_id="group_123",
        album_id="555666",
        album_name="2026年夏季團聚活動相簿",
        album_time="2026-08-31T10:00:00Z",
    )
    album_folder_name = album.get_folder_name()
    assert album_folder_name == "20260831 2026年夏季團聚活動相簿"


@pytest.mark.asyncio
@respx.mock
async def test_image_downloader(tmp_path: Path):
    storage_dir = tmp_path / "downloads"
    downloader = ImageDownloader(storage_dir)

    img_url = "https://scontent.fbcdn.net/v/t39.30808-6/test.jpg"
    fake_img_data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"

    respx.get(img_url).respond(200, content=fake_img_data, headers={"Content-Type": "image/jpeg"})

    item = MediaItem(
        group_id="group_123",
        media_type=MediaType.IMAGE,
        source_url=img_url,
        media_id="photo_999",
        post_id="post_888",
    )

    custom_dir = storage_dir / "group_123" / "posts" / "20260831 測試貼文"
    record = await downloader.download(item, target_dir=custom_dir, index=1)
    assert record is not None
    assert record.media_type == MediaType.IMAGE
    assert record.file_size == len(fake_img_data)
    assert Path(record.local_filepath).exists()
    assert "20260831 測試貼文" in record.local_filepath


@pytest.mark.asyncio
@respx.mock
async def test_download_manager_process_post_bundle(tmp_path: Path):
    storage_dir = tmp_path / "downloads"
    db = Database(storage_dir / "test.db")
    cfg = AppConfig(storage_dir=storage_dir)
    manager = DownloadManager(cfg, db)

    img1_url = "https://scontent.fbcdn.net/v/photo1.jpg"
    img2_url = "https://scontent.fbcdn.net/v/photo2.jpg"
    respx.get(img1_url).respond(200, content=b"fake image 1", headers={"Content-Type": "image/jpeg"})
    respx.get(img2_url).respond(200, content=b"fake image 2", headers={"Content-Type": "image/jpeg"})

    post_bundle = PostBundle(
        group_id="group_1",
        post_id="post_101",
        post_author="陳大文",
        post_text="社團聚會活動精彩照片",
        post_time="2026-08-31T12:00:00Z",
        media_items=[
            MediaItem(
                group_id="group_1",
                media_type=MediaType.IMAGE,
                source_url=img1_url,
                media_id="p1",
                post_id="post_101",
            ),
            MediaItem(
                group_id="group_1",
                media_type=MediaType.IMAGE,
                source_url=img2_url,
                media_id="p2",
                post_id="post_101",
            ),
        ],
    )

    # 執行貼文下載
    records = await manager.process_post(post_bundle)
    assert len(records) == 2

    # 檢查貼文獨立資料夾是否存在且格式為 yyyyMMdd <name>
    expected_folder_name = post_bundle.get_folder_name()
    assert expected_folder_name.startswith("20260831 ")
    post_dir = storage_dir / "group_1" / "posts" / expected_folder_name
    assert post_dir.exists()
    assert post_dir.is_dir()

    # 檢查 post_info.txt 與 post_info.json 是否已生成
    info_txt = post_dir / "post_info.txt"
    info_json = post_dir / "post_info.json"
    assert info_txt.exists()
    assert info_json.exists()
    assert "陳大文" in info_txt.read_text(encoding="utf-8")

    # 檢查 DB 是否記錄了 folder_path
    assert db.is_downloaded("group_1", media_id="p1")
    assert db.is_downloaded("group_1", media_id="p2")

    # 再次下載相同貼文，應自動去重跳過
    records_again = await manager.process_post(post_bundle)
    assert len(records_again) == 0


@pytest.mark.asyncio
@respx.mock
async def test_download_manager_process_album_bundle(tmp_path: Path):
    storage_dir = tmp_path / "downloads"
    db = Database(storage_dir / "test.db")
    cfg = AppConfig(storage_dir=storage_dir)
    manager = DownloadManager(cfg, db)

    img_url = "https://scontent.fbcdn.net/v/album_photo.jpg"
    respx.get(img_url).respond(200, content=b"album photo data", headers={"Content-Type": "image/jpeg"})

    album_bundle = AlbumBundle(
        group_id="group_1",
        album_id="alb_777",
        album_name="2026年度相簿",
        album_time="2026-08-31T08:00:00Z",
        media_items=[
            MediaItem(
                group_id="group_1",
                media_type=MediaType.IMAGE,
                source_url=img_url,
                media_id="alb_p1",
                album_id="alb_777",
                album_name="2026年度相簿",
            )
        ],
    )

    records = await manager.process_album(album_bundle)
    assert len(records) == 1

    album_dir = storage_dir / "group_1" / "albums" / "20260831 2026年度相簿"
    assert album_dir.exists()
    assert (album_dir / "album_info.json").exists()


def test_album_id_is_stable_and_list_url_detected():
    from fb_group_downloader.scraper.group_media import GroupMediaScraper

    f = GroupMediaScraper._extract_album_id
    url = "https://www.facebook.com/groups/1/media/albums/"
    assert f(None, url) == f(None, url)
    assert f(None, url).startswith("album_")
    assert GroupMediaScraper._is_album_list_url(url)
    assert not GroupMediaScraper._is_album_list_url("https://www.facebook.com/groups/1/media/set/?set=oa.123")
    a = GroupMediaScraper._stable_media_id("a1", "https://x/v/t1/515_137_n.jpg?stp=1")
    assert a == "a1_515_137_n"


def test_find_existing_dir_reuses_folder_across_date_change(tmp_path: Path):
    import json

    root = tmp_path / "albums"
    d = root / "20260831 foo"
    d.mkdir(parents=True)
    (d / "album_info.json").write_text(json.dumps({"album_id": "42"}))
    assert DownloadManager._find_existing_dir(root, "album_info.json", "album_id", "42") == d
    assert DownloadManager._find_existing_dir(root, "album_info.json", "album_id", "43") is None


def test_cleanup_find_and_delete_duplicates(tmp_path: Path):
    import json

    from fb_group_downloader.cleanup import delete_empty_duplicates, find_duplicates

    albums = tmp_path / "grp" / "albums"
    for name, files in [("20260831 a", 2), ("20260914 a", 0), ("20260915 a", 1)]:
        d = albums / name
        d.mkdir(parents=True)
        (d / "album_info.json").write_text(json.dumps({"album_id": "42"}))
        for i in range(files):
            (d / f"p{i}.jpg").write_bytes(b"x")
    res = find_duplicates(tmp_path)
    assert [d.name for d in res.empty] == ["20260914 a"]
    assert [(d.name, n) for d, n in res.manual] == [("20260915 a", 1)]
    delete_empty_duplicates(tmp_path, res)
    assert not (albums / "20260914 a").exists()
    assert (albums / "20260915 a").exists()


def test_files_scraper_helpers():
    from fb_group_downloader.downloader.file import FileDownloader
    from fb_group_downloader.scraper.group_files import GroupFilesScraper

    assert GroupFilesScraper._is_files_tab_url("https://www.facebook.com/groups/123/files/files/")
    assert GroupFilesScraper._is_files_tab_url("https://www.facebook.com/groups/123/files?__cft__[0]=abc")
    assert not GroupFilesScraper._is_files_tab_url("https://www.facebook.com/groups/123/permalink/456/")

    u1 = "https://www.facebook.com/groups/123/permalink/456/?__cft__[0]=aaa&__tn__=R"
    u2 = "https://www.facebook.com/groups/123/permalink/456/?__cft__[0]=bbb"
    assert GroupFilesScraper._clean_file_url(u1) == "https://www.facebook.com/groups/123/permalink/456/"
    assert GroupFilesScraper._stable_file_id(u1, "a.pdf") == GroupFilesScraper._stable_file_id(u2, "a.pdf")
    assert GroupFilesScraper._stable_file_id(u1, "a.pdf") == "file_456"

    sample_html = (
        r'{"url":"https:\/\/www.facebook.com\/download\/1812754796560037\/'
        r'115\u0025E5\u0025B9\u0025B4\u0025E6\u002598\u00259F\u0025E6\u002598\u00259F'
        r'\u0025E7\u00258F\u0025AD\u0025E5\u0025AE\u0025B6\u0025E9\u002595\u0025B7'
        r'\u0025E6\u00259C\u002583.pdf?ext=1&hash=abc"}'
    )
    dl_map = GroupFilesScraper._extract_download_urls_from_html(sample_html)
    assert "115年星星班家長會.pdf" in dl_map
    assert dl_map["115年星星班家長會.pdf"].startswith("https://www.facebook.com/download/1812754796560037/")

    fd = FileDownloader(Path("/tmp"))
    name = fd._extract_filename_from_headers(
        {"Content-Disposition": 'attachment; filename="115_.pdf"'},
        fallback="115年星星班家長會.pdf",
    )
    assert name == "115年星星班家長會.pdf"
