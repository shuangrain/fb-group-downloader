import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"


def sanitize_folder_name(name: str, max_length: int = 60) -> str:
    """清理資料夾與檔名中的非法字元，替換為安全字元"""
    if not name:
        return ""
    # 移除非法字元 \ / : * ? " < > | 以及換行符號
    clean = re.sub(r'[\\/*?:"<>|\r\n\t]', " ", name)
    # 合併多個連續空格
    clean = re.sub(r"\s+", " ", clean).strip(". ")
    return clean[:max_length].strip()


class MediaItem(BaseModel):
    group_id: str
    group_name: str | None = None
    media_type: MediaType
    source_url: str
    media_id: str | None = None
    filename: str | None = None

    # 貼文資訊
    post_id: str | None = None
    post_author: str | None = None
    post_text: str | None = None
    post_url: str | None = None
    post_time: str | None = None

    # 相簿資訊
    album_id: str | None = None
    album_name: str | None = None
    album_url: str | None = None

    def get_group_folder_name(self) -> str:
        """取得社團外層資料夾名稱（優先使用社團名稱，否則使用 ID）"""
        if self.group_name:
            return sanitize_folder_name(self.group_name, 50) or self.group_id
        return self.group_id


class PostBundle(BaseModel):
    """以一篇貼文為單位的資料封裝"""

    group_id: str
    group_name: str | None = None
    post_id: str
    post_author: str | None = None
    post_text: str | None = None
    post_url: str | None = None
    post_time: str | None = None
    media_items: list[MediaItem] = Field(default_factory=list)

    def get_group_folder_name(self) -> str:
        """取得社團外層資料夾名稱（優先使用社團名稱）"""
        if self.group_name:
            return sanitize_folder_name(self.group_name, 50) or self.group_id
        return self.group_id

    def get_folder_name(self) -> str:
        """
        產生貼文獨立資料夾名稱，格式規範為：yyyyMMdd <name>
        例如：20260831 王小明 - 貼文摘要內容
        """
        date_str = ""
        if self.post_time:
            try:
                dt = datetime.fromisoformat(self.post_time.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y%m%d")
            except Exception:
                pass
        if not date_str:
            date_str = datetime.utcnow().strftime("%Y%m%d")

        name_parts = []
        if self.post_author:
            name_parts.append(sanitize_folder_name(self.post_author, 20))

        if self.post_text:
            text_snippet = sanitize_folder_name(self.post_text, 35)
            if text_snippet:
                name_parts.append(text_snippet)

        if name_parts:
            name_content = " - ".join(name_parts)
        else:
            name_content = f"貼文_{self.post_id}"

        clean_name = sanitize_folder_name(name_content, 60)
        return f"{date_str} {clean_name}"


class AlbumBundle(BaseModel):
    """以一本相簿為單位的資料封裝"""

    group_id: str
    group_name: str | None = None
    album_id: str
    album_name: str
    album_url: str | None = None
    album_time: str | None = None
    media_items: list[MediaItem] = Field(default_factory=list)

    def get_group_folder_name(self) -> str:
        """取得社團外層資料夾名稱（優先使用社團名稱）"""
        if self.group_name:
            return sanitize_folder_name(self.group_name, 50) or self.group_id
        return self.group_id

    def get_folder_name(self) -> str:
        """
        產生相簿獨立資料夾名稱，格式規範為：yyyyMMdd <name>
        例如：20260831 2026年社團聚會活動相簿
        """
        date_str = ""
        if self.album_time:
            try:
                dt = datetime.fromisoformat(self.album_time.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y%m%d")
            except Exception:
                pass
        if not date_str:
            date_str = datetime.utcnow().strftime("%Y%m%d")

        clean_name = sanitize_folder_name(self.album_name, 50) or f"相簿_{self.album_id}"
        return f"{date_str} {clean_name}"


class DownloadRecord(BaseModel):
    id: int | None = None
    group_id: str
    post_id: str | None = None
    album_id: str | None = None
    album_name: str | None = None
    media_id: str | None = None
    media_type: MediaType
    original_url: str
    local_filepath: str
    folder_path: str | None = None
    file_size: int = 0
    sha256: str | None = None
    post_author: str | None = None
    post_text: str | None = None
    post_url: str | None = None
    post_time: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
