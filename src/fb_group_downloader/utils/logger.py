import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from typing import TYPE_CHECKING

from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

if TYPE_CHECKING:
    from fb_group_downloader.config import LogConfig

console = Console(soft_wrap=True)

_logger: logging.Logger | None = None


class UnboundedRichHandler(RichHandler):
    """
    自訂 RichHandler：移除終端機寬度限制（soft_wrap=True），
    避免在 Docker 或非 TTY 環境下預設被 80 字元強制換行折疊。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            message_renderable = self.render_message(record, message)
            level = self.get_level_text(record)
            time_format = getattr(self._log_render, "time_format", "[%x %X]")
            log_time = datetime.fromtimestamp(record.created).strftime(time_format)

            line = Text()
            if getattr(self._log_render, "show_time", True):
                line.append(f"{log_time} ", style="log.time")
            if getattr(self._log_render, "show_level", True):
                line.append(level)
                line.append(" " * max(1, 10 - len(level.plain)))
            line.append(message_renderable if isinstance(message_renderable, Text) else Text(str(message_renderable)))

            self.console.print(line, soft_wrap=True)
            if self.rich_tracebacks and record.exc_info and record.exc_info != (None, None, None):
                from rich.traceback import Traceback

                tb = Traceback.from_exception(*record.exc_info)
                self.console.print(tb)
        except Exception:
            self.handleError(record)


def setup_logger(log_config: "LogConfig | None" = None, debug: bool = False) -> logging.Logger:
    global _logger

    level = logging.DEBUG if debug else logging.INFO
    logger = logging.getLogger("fb_downloader")
    logger.setLevel(level)
    logger.handlers.clear()

    # Rich Console Handler (無長度限制)
    rich_handler = UnboundedRichHandler(
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
        try:
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
        except (PermissionError, OSError) as e:
            console.print(
                f"[bold yellow]⚠️  無法建立或寫入日誌檔案 ({e})，請確認目錄權限：{log_file.resolve()}[/bold yellow]"
            )

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        return setup_logger()
    return _logger
