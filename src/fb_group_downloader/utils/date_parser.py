import re
from datetime import datetime, timedelta


def parse_fb_date(date_str: str | None, reference_text: str | None = None) -> str:
    """
    解析 Facebook 貼文或相簿的發布/建立時間，回傳 ISO 格式 (YYYY-MM-DDTHH:MM:SS)
    支援：
    - 完整中文年月日：2024年10月15日、2024-10-15、2024/10/15
    - 當年度月日：8月25日、8/31-9/11學習區
    - 相對時間：昨天、前天、N天前、N小時前、剛剛
    - 標題或內文開頭的日期：如 9/7-9/11、20240901、113/09/01
    """
    now = datetime.now()

    # 候選字串清單
    candidates = []
    if date_str:
        candidates.append(date_str.strip())
    if reference_text:
        # 取得標題或內文前 50 個字元
        candidates.append(reference_text.strip().split("\n")[0][:50])

    for text in candidates:
        if not text:
            continue

        # 1. 完整年月日：例如 2024年10月15日、2024-10-15、2024/10/15
        m_full = re.search(r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})", text)
        if m_full:
            y, m, d = int(m_full.group(1)), int(m_full.group(2)), int(m_full.group(3))
            try:
                return datetime(y, m, d, 12, 0, 0).isoformat()
            except ValueError:
                pass

        # 2. 民國年格式：例如 113年9月1日 或 113/9/1
        m_roc = re.search(r"(\d{2,3})[年/-](\d{1,2})[月/-](\d{1,2})", text)
        if m_roc:
            roc_y = int(m_roc.group(1))
            if 90 <= roc_y <= 150:
                y = roc_y + 1911
                m, d = int(m_roc.group(2)), int(m_roc.group(3))
                try:
                    return datetime(y, m, d, 12, 0, 0).isoformat()
                except ValueError:
                    pass

        # 3. 當年度月日：例如 8月25日、8/31-9/11、9/7、08/25
        m_md = re.search(r"(?:^|[^\d])(\d{1,2})[月/](\d{1,2})[日號]?", text)
        if m_md:
            m, d = int(m_md.group(1)), int(m_md.group(2))
            if 1 <= m <= 12 and 1 <= d <= 31:
                y = now.year
                if m > now.month + 1:
                    y -= 1
                try:
                    return datetime(y, m, d, 12, 0, 0).isoformat()
                except ValueError:
                    pass

        # 4. 純 8 位數字日期：例如 20240831
        m_digits = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", text)
        if m_digits:
            y, m, d = int(m_digits.group(1)), int(m_digits.group(2)), int(m_digits.group(3))
            return datetime(y, m, d, 12, 0, 0).isoformat()

        # 5. 昨天 / 前天
        if "昨天" in text or "yesterday" in text.lower():
            return (now - timedelta(days=1)).isoformat()
        if "前天" in text:
            return (now - timedelta(days=2)).isoformat()

        # 6. N天前 / N 天
        m_days = re.search(r"(\d+)\s*(?:天|日|d)", text)
        if m_days:
            days = int(m_days.group(1))
            return (now - timedelta(days=days)).isoformat()

        # 7. N小時前 / N分鐘前 / 剛剛
        if any(k in text for k in ["小時", "分鐘", "剛剛", "秒", "hr", "min", "just now"]):
            return now.isoformat()

    return now.isoformat()
