from pathlib import Path

from fb_group_downloader.config import LogConfig, LogRotationMode
from fb_group_downloader.utils.logger import setup_logger


def test_logger_file_output_and_size_rotation(tmp_path: Path):
    log_file = tmp_path / "logs" / "test.log"
    log_cfg = LogConfig(
        enabled=True,
        file_path=log_file,
        rotation_mode=LogRotationMode.SIZE,
        max_bytes=200,  # small size to trigger rotation
        backup_count=2,
    )

    logger = setup_logger(log_config=log_cfg)
    logger.handlers.clear()
    # Re-run setup
    setup_logger(log_config=log_cfg)

    # 寫入多條日誌以觸發輪替
    for i in range(20):
        logger.info(f"Test log message number {i:03d} to trigger file rotation")

    assert log_file.exists()
    # 檢查是否有備份檔案產生 (例如 test.log.1)
    backup_1 = tmp_path / "logs" / "test.log.1"
    assert backup_1.exists()


def test_logger_daily_rotation_setup(tmp_path: Path):
    log_file = tmp_path / "logs" / "daily.log"
    log_cfg = LogConfig(
        enabled=True,
        file_path=log_file,
        rotation_mode=LogRotationMode.DAILY,
        backup_count=3,
    )

    logger = setup_logger(log_config=log_cfg)
    logger.info("Test daily rotation logger message")

    assert log_file.exists()
