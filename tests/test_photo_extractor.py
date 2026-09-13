from fb_group_downloader.scraper.photo_extractor import FacebookPhotoExtractor


def test_photo_extractor_is_thumbnail():
    # 縮圖路徑標記
    thumb_url1 = "https://scontent-tpe1-1.xx.fbcdn.net/v/t39.30808-6/s526x296/123_n.jpg?oh=abc"
    thumb_url2 = "https://scontent-tpe1-1.xx.fbcdn.net/v/t39.30808-6/p720x720/456_n.jpg?oh=def"
    thumb_url3 = "https://scontent-tpe1-1.xx.fbcdn.net/v/t39.30808-6/c0.0.526.296a/789_n.jpg?oh=xyz"
    assert FacebookPhotoExtractor.is_thumbnail_url(thumb_url1) is True
    assert FacebookPhotoExtractor.is_thumbnail_url(thumb_url2) is True
    assert FacebookPhotoExtractor.is_thumbnail_url(thumb_url3) is True

    # 完整原圖 (非縮圖)
    full_url = "https://scontent-tpe1-1.xx.fbcdn.net/v/t39.30808-6/123456_n.jpg?oh=abc"
    assert FacebookPhotoExtractor.is_thumbnail_url(full_url) is False


def test_photo_extractor_extract_from_html():
    fake_html = """
    <html>
      <script>
        {"viewer_image": {"uri": "https:\\/\\/scontent-tpe1-1.xx.fbcdn.net\\/v\\/t39.30808-6\\/high_res_photo.jpg"}}
      </script>
    </html>
    """
    extracted = FacebookPhotoExtractor.extract_from_html(fake_html)
    assert extracted is not None
    assert "high_res_photo.jpg" in extracted
    assert "https://scontent-tpe1-1.xx.fbcdn.net" in extracted
