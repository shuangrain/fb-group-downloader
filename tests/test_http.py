import logging
from pathlib import Path

import pytest
import respx

from fb_group_downloader.config import LogConfig
from fb_group_downloader.utils.http import create_async_client
from fb_group_downloader.utils.logger import setup_logger


@pytest.mark.asyncio
@respx.mock
async def test_create_async_client_debug_logging(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    log_file = tmp_path / "debug.log"
    log_cfg = LogConfig(enabled=True, file_path=log_file, level="DEBUG")
    setup_logger(log_config=log_cfg, debug=True)

    test_url = "https://example.com/test-endpoint"
    respx.get(test_url).respond(200, json={"status": "ok", "message": "hello"})

    with caplog.at_level(logging.DEBUG):
        async with create_async_client(timeout=10.0) as client:
            resp = await client.get(test_url, headers={"X-Custom-Header": "TestValue"})
            assert resp.status_code == 200

    # 驗證是否有記錄 RAW HTTP REQUEST 與 RAW HTTP RESPONSE
    assert any("RAW HTTP REQUEST" in record.message for record in caplog.records)
    assert any("RAW HTTP RESPONSE" in record.message for record in caplog.records)
    assert any("x-custom-header" in record.message for record in caplog.records)
