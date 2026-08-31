import logging

import httpx

from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


def create_async_client(
    cookies: dict | None = None,
    headers: dict | None = None,
    timeout: float = 60.0,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """
    建立具備 Debug Raw Request / Raw Response 攔截與日誌輸出的 AsyncClient
    """
    event_hooks = {}

    if logger.isEnabledFor(logging.DEBUG):

        async def log_request(request: httpx.Request):
            headers_str = "\n".join(f"    {k}: {v}" for k, v in request.headers.items())
            logger.debug(
                f"\n[bold cyan]➡️ ====== [RAW HTTP REQUEST] ======[/bold cyan]\n"
                f"  Method: {request.method}\n"
                f"  URL: {request.url}\n"
                f"  Headers:\n{headers_str}\n"
                f"====================================="
            )

        async def log_response(response: httpx.Response):
            headers_str = "\n".join(f"    {k}: {v}" for k, v in response.headers.items())
            body_preview = ""
            content_type = response.headers.get("Content-Type", "")

            # 若為文字或 JSON 且已讀取內容，輸出前 300 字元預覽
            if any(t in content_type for t in ["text", "json", "html", "xml"]):
                try:
                    if hasattr(response, "_content") and response._content:
                        sample = response.text[:300].replace("\r", " ").replace("\n", " ")
                        body_preview = f"\n  Body Preview:\n    {sample}..."
                except Exception:
                    pass

            logger.debug(
                f"\n[bold green]⬅️ ====== [RAW HTTP RESPONSE] ======[/bold green]\n"
                f"  Status: {response.status_code} {response.reason_phrase}\n"
                f"  URL: {response.url}\n"
                f"  Headers:\n{headers_str}"
                f"{body_preview}\n"
                f"======================================"
            )

        event_hooks = {"request": [log_request], "response": [log_response]}

    return httpx.AsyncClient(
        cookies=cookies,
        headers=headers,
        timeout=timeout,
        follow_redirects=follow_redirects,
        event_hooks=event_hooks,
    )
