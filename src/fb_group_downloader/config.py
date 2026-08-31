from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GroupConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    group_id: str = Field(..., description="Facebook 社團 ID 或網址別名 (Vanity Name)")
    name: str | None = Field(None, description="社團備註名稱（可選）")
    download_images: bool = Field(True, description="是否下載圖片")
    download_videos: bool = Field(True, description="是否下載影片")
    download_files: bool = Field(True, description="是否下載社團檔案/文件 (PDF, DOC, ZIP 等)")
    scan_feed: bool = Field(True, description="是否掃描貼文動態")
    scan_files_tab: bool = Field(True, description="是否掃描社團檔案專區 (/files)")
    scan_media_tab: bool = Field(False, description="是否獨立掃描社團多媒體專區 (/media)")
    max_posts_per_scan: int = Field(30, description="每次同步最多掃描貼文數")

    @field_validator("group_id", mode="before")
    @classmethod
    def coerce_group_id_to_str(cls, v: Any) -> str:
        """自動將純數字的 group_id 轉型為字串"""
        return str(v).strip()


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(True, description="是否啟用定時排程")
    interval_minutes: int = Field(60, description="定時間隔（分鐘）")
    cron: str | None = Field(
        None, description="可選的 Cron 表達式，例如 '0 */2 * * *'。若設定則優先於 interval_minutes"
    )


class LogRotationMode(str, Enum):
    SIZE = "size"  # 依檔案大小輪替 (RotatingFileHandler)
    DAILY = "daily"  # 依每日時間輪替 (TimedRotatingFileHandler)


class LogConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(True, description="是否啟用日誌輸出至檔案")
    file_path: Path = Field(default=Path("./logs/fb_downloader.log"), description="日誌檔案儲存路徑")
    level: str = Field(default="INFO", description="檔案日誌記錄等級 (DEBUG, INFO, WARNING, ERROR)")
    rotation_mode: LogRotationMode = Field(
        default=LogRotationMode.SIZE,
        description="日誌輪替機制: 'size' (依檔案大小) 或 'daily' (依每日時間)",
    )
    max_bytes: int = Field(default=10 * 1024 * 1024, description="依大小輪替時，單一檔案大小上限 (bytes)，預設 10MB")
    backup_count: int = Field(default=5, description="保留的歷史備份日誌檔案數量")


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    storage_dir: Path = Field(default=Path("./downloads"), description="下載檔案與資料庫儲存目錄")
    session_file: Path = Field(default=Path("./session.json"), description="Facebook 登入 Session / Cookies 儲存路徑")
    headless: bool = Field(default=True, description="爬蟲執行時是否使用無頭模式 (Headless)")
    browser_timeout_sec: int = Field(default=60, description="瀏覽器請求超時時間 (秒)")
    scroll_delay_range: tuple[float, float] = Field(default=(1.5, 3.5), description="捲動頁面隨機延遲區間 (秒)")
    user_agent: str | None = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        description="自訂 User-Agent",
    )
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    logging: LogConfig = Field(default_factory=LogConfig)
    groups: list[GroupConfig] = Field(default_factory=list, description="要監控的社團列表")

    @classmethod
    def load_from_yaml(cls, path: Path | str) -> "AppConfig":
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"找不到設定檔：{file_path.resolve()}")
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def save_to_yaml(self, path: Path | str) -> None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert pydantic model to dict, converting Path to str
        data = self.model_dump(mode="json")
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False, width=1000)


def get_default_config_path() -> Path:
    return Path("./config.yaml")


def load_or_create_config(path: Path | str | None = None) -> AppConfig:
    target_path = Path(path) if path else get_default_config_path()
    if target_path.exists():
        return AppConfig.load_from_yaml(target_path)

    # 預設範例設定
    default_cfg = AppConfig(
        groups=[
            GroupConfig(
                group_id="example_group_id",
                name="範例社團",
                download_images=True,
                download_videos=True,
                download_files=True,
                scan_feed=True,
                scan_files_tab=True,
                max_posts_per_scan=30,
            )
        ]
    )
    return default_cfg
