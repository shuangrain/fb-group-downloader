from datetime import datetime, timedelta

from fb_group_downloader.utils.date_parser import parse_fb_date


def test_parse_fb_date_full():
    res = parse_fb_date("2024年10月15日 下午2:30")
    dt = datetime.fromisoformat(res)
    assert dt.strftime("%Y%m%d") == "20241015"

    res2 = parse_fb_date("2023/5/8")
    dt2 = datetime.fromisoformat(res2)
    assert dt2.strftime("%Y%m%d") == "20230508"


def test_parse_fb_date_roc():
    res = parse_fb_date("113年9月1日")
    dt = datetime.fromisoformat(res)
    assert dt.strftime("%Y%m%d") == "20240901"


def test_parse_fb_date_reference():
    res = parse_fb_date(None, reference_text="8/31-9/11學習區")
    dt = datetime.fromisoformat(res)
    # 應抓到 8/31
    assert dt.month == 8
    assert dt.day == 31


def test_parse_fb_date_relative():
    res = parse_fb_date("昨天 上午10:20")
    dt = datetime.fromisoformat(res)
    expected = datetime.now() - timedelta(days=1)
    assert dt.strftime("%Y%m%d") == expected.strftime("%Y%m%d")

    res_h = parse_fb_date("3小時前")
    dt_h = datetime.fromisoformat(res_h)
    assert dt_h.strftime("%Y%m%d") == datetime.now().strftime("%Y%m%d")
