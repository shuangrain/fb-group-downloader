from fb_group_downloader.scraper.video_extractor import FacebookVideoExtractor


def test_facebook_video_extractor_html_parsing():
    fake_html = """
    <html>
      <script>
        require("ScheduledServerJS", "handle", {
          "playable_url_quality_hd": "https:\\/\\/video-tpe1-1.xx.fbcdn.net\\/v\\/t42.17977-2\\/test_hd.mp4?bytestart=0",
          "playable_url": "https:\\/\\/video-tpe1-1.xx.fbcdn.net\\/v\\/t42.17977-2\\/test_sd.mp4?bytestart=0"
        });
      </script>
    </html>
    """
    stream_url = FacebookVideoExtractor.extract_from_html(fake_html)
    assert stream_url is not None
    assert "test_hd.mp4" in stream_url
    assert "fbcdn.net" in stream_url


def test_facebook_video_extractor_fallback():
    fake_html_sd = """
    <div>
      <video src="https://video.xx.fbcdn.net/v/t39/sample_video.mp4"></video>
    </div>
    """
    stream_url = FacebookVideoExtractor.extract_from_html(fake_html_sd)
    assert stream_url == "https://video.xx.fbcdn.net/v/t39/sample_video.mp4"
