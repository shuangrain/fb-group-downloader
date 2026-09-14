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


def test_strip_byte_range_params():
    url_with_range = "https://scontent.xx.fbcdn.net/o1/v/t2/test.mp4?_nc_cat=110&bytestart=0&byteend=1521&efg=xyz"
    cleaned = FacebookVideoExtractor.strip_byte_range_params(url_with_range)
    assert "bytestart" not in cleaned
    assert "byteend" not in cleaned
    assert "_nc_cat=110" in cleaned
    assert "efg=xyz" in cleaned


def test_extract_video_id_from_efg():
    import base64
    import json

    efg_json = json.dumps({"video_id": 987654321, "vencode_tag": "dash_vp9"})
    efg_b64 = base64.b64encode(efg_json.encode("utf-8")).decode("utf-8")
    url = f"https://scontent.xx.fbcdn.net/o1/v/test.mp4?efg={efg_b64}"

    vid = FacebookVideoExtractor.extract_video_id_from_url(url)
    assert vid == "987654321"


def test_stream_type_and_quality_detection():
    import base64
    import json

    # 1. 音訊串流
    audio_efg = json.dumps({"vencode_tag": "dash_ln_heaac_vbr3_audio"})
    audio_b64 = base64.b64encode(audio_efg.encode("utf-8")).decode("utf-8")
    audio_url = f"https://scontent.xx.fbcdn.net/o1/v/stream.mp4?efg={audio_b64}"

    assert FacebookVideoExtractor.is_audio_stream(audio_url)
    assert not FacebookVideoExtractor.is_vp9_stream(audio_url)

    # 2. 1080p VP9 視訊串流
    v1080_efg = json.dumps({"vencode_tag": "dash_vp9-basic-gen2_1080p", "bitrate": 3328499})
    v1080_b64 = base64.b64encode(v1080_efg.encode("utf-8")).decode("utf-8")
    v1080_url = f"https://scontent.xx.fbcdn.net/o1/v/stream.mp4?efg={v1080_b64}"

    assert not FacebookVideoExtractor.is_audio_stream(v1080_url)
    assert FacebookVideoExtractor.is_dash_stream(v1080_url)
    assert FacebookVideoExtractor.is_vp9_stream(v1080_url)
    assert FacebookVideoExtractor.get_video_stream_quality(v1080_url) == 1080

    # 3. 360p 視訊串流
    v360_efg = json.dumps({"vencode_tag": "dash_vp9-basic-gen2_360p", "bitrate": 663481})
    v360_b64 = base64.b64encode(v360_efg.encode("utf-8")).decode("utf-8")
    v360_url = f"https://scontent.xx.fbcdn.net/o1/v/stream.mp4?efg={v360_b64}"

    assert FacebookVideoExtractor.get_video_stream_quality(v1080_url) > FacebookVideoExtractor.get_video_stream_quality(
        v360_url
    )
