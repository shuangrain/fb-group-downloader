import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import TYPE_CHECKING

from rich.console import Console
from rich.logging import RichHandler

if TYPE_CHECKING:
    from fb_group_downloader.config import LogConfig

console = Console()

_logger: logging.Logger | None = None


def setup_logger(log_config: "LogConfig | None" = None, debug: bool = False) -> logging.Logger:
    global _logger

    level = logging.DEBUG if debug else logging.INFO
    logger = logging.getLogger("fb_downloader")
    logger.setLevel(level)
    logger.handlers.clear()

    # Rich Console Handler
    rich_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        markup=True,
    )
    rich_handler.setLevel(level)
    logger.addHandler(rich_handler)

    # 檔案日誌處理器（支援輪替機制）
    if log_config and log_config.enabled:
        log_file = log_config.file_path
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_level = logging.DEBUG if debug else getattr(logging, log_config.level.upper(), logging.INFO)
        file_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        rotation_mode = (
            log_config.rotation_mode.value
            if hasattr(log_config.rotation_mode, "value")
            else str(log_config.rotation_mode)
        )

        if rotation_mode == "daily":
            file_handler = TimedRotatingFileHandler(
                filename=str(log_file),
                when="midnight",
                interval=1,
                backupCount=log_config.backup_count,
                encoding="utf-8",
            )
            file_handler.suffix = "%Y-%m-%d"
        else:
            # 預設：依檔案大小輪替 (size)
            file_handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=log_config.max_bytes,
                backupCount=log_config.backup_count,
                encoding="utf-8",
            )

        file_handler.setLevel(file_level)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        return setup_logger()
    return _logger
