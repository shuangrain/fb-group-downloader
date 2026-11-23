import asyncio
import hashlib
import re
from datetime import datetime

from playwright.async_api import Page

from fb_group_downloader.config import GroupConfig
from fb_group_downloader.downloader.models import MediaItem, MediaType
from fb_group_downloader.scraper.base import BaseScraper
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class GroupFilesScraper:
    def __init__(self, base_scraper: BaseScraper, group_config: GroupConfig):
        self.base = base_scraper
        self.config = group_config
        self.group_id = group_config.group_id
        self.seen_file_urls: set[str] = set()

    @staticmethod
    def _is_files_tab_url(url: str) -> bool:
        """判斷是否為檔案專區列表頁本身（如 /groups/<id>/files 或 /files/files/），而非實際檔案"""
        path = url.split("?")[0].split("#")[0].rstrip("/")
        return path.endswith("/files")

    @staticmethod
    def _clean_file_url(url: str) -> str:
        """剝離 __cft__ / __tn__ 等動態追蹤參數，使檔案網址與 ID 在不同執行輪次保持穩定"""
        base = url.split("#")[0]
        if "?" not in base:
            return base
        path, qs = base.split("?", 1)
        kept = [p for p in qs.split("&") if not p.startswith("__cft__") and not p.startswith("__tn__")]
        return f"{path}?{'&'.join(kept)}" if kept else path

    @classmethod
    def _extract_download_urls_from_html(cls, html: str) -> dict[str, str]:
        """從頁面 HTML 內嵌 JSON 擷取真實的 /download/<id>/<filename> 直鏈（以 unquoted 檔名或路徑為 key）"""
        raw_matches = re.findall(r'https?:\\?/\\?/www\.facebook\.com\\?/download\\?/\d+\\?/[^"\'<>\s]+', html)
        mapping: dict[str, str] = {}
        for raw in raw_matches:
            clean = raw.replace(r"\/", "/").replace("\\u0025", "%").replace("\\u0026", "&")
            try:
                path_part = clean.split("?")[0].rstrip("/")
                fname = path_part.rsplit("/", 1)[-1]
                from urllib.parse import unquote

                unquoted_name = unquote(fname)
                mapping[unquoted_name] = clean
            except Exception:
                continue
        return mapping

    @classmethod
    def _stable_file_id(cls, url: str, filename: str) -> str:
        """產生跨執行輪次穩定的檔案 media_id（優先擷取 download/<id> 或 permalink/<id>，否則用 md5）"""
        m = re.search(r"/(?:download|permalink|files)/(\d+)", url)
        if m:
            return f"file_{m.group(1)}"
        clean = f"{cls._clean_file_url(url)}_{filename}"
        return f"file_{hashlib.md5(clean.encode('utf-8')).hexdigest()[:8]}"

    async def scan_files(self, page: Page) -> list[MediaItem]:
        """
        掃描社團「檔案/文件」分頁 (https://www.facebook.com/groups/{group_id}/files)
        """
        url = f"https://www.facebook.com/groups/{self.group_id}/files"
        logger.info(f"正在前往社團檔案專區：{url}")

        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await self.base.close_dialogs(page)

        extracted_items: list[MediaItem] = []
        consecutive_no_new = 0
        scroll_count = 0
        max_scrolls = 10

        while scroll_count < max_scrolls:
            scroll_count += 1

            files_data = await page.evaluate(
                """() => {
                    const results = [];
                    // 尋找檔案列表中的所有連結或項目
                    const links = Array.from(document.querySelectorAll('a[href*="/files/"], a[href*="download"], div[role="main"] a'));

                    for (const a of links) {
                        const href = a.href;
                        const text = a.innerText.trim();

                        // 判斷是否為檔案下載連結或檔案名稱連結
                        const isFileLink = (
                            href.includes('/files/') ||
                            href.includes('download') ||
                            /\\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|csv|epub|mp3)$/i.test(text) ||
                            /\\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|csv|epub|mp3)$/i.test(href)
                        );

                        if (isFileLink && text) {
                            // 尋找該項目的上層容器以取得上傳者與時間
                            const row = a.closest('div[role="row"], div[role="article"], tr, div[data-visualcompletion]');
                            let author = "";
                            let dateText = "";
                            if (row) {
                                const rowText = row.innerText;
                                author = rowText.split('\\n')[1] || "";
                            }

                            results.push({
                                url: href,
                                filename: text,
                                author: author
                            });
                        }
                    }
                    return results;
                }"""
            )

            html_download_map = self._extract_download_urls_from_html(await page.content())

            new_found = 0
            for f in files_data:
                file_url = f.get("url", "")
                filename = f.get("filename", "")
                if not file_url or self._is_files_tab_url(file_url):
                    continue

                # 若連結為 /permalink/ 貼文頁而非 /download/ 直鏈，優先替換為頁面 JSON 內嵌的真實 /download/ 直鏈
                if "/download/" not in file_url and filename in html_download_map:
                    file_url = html_download_map[filename]

                clean_url = file_url if "/download/" in file_url else self._clean_file_url(file_url)
                # 去重（以穩定的 file_id 去重，避免帶 token 的下載直鏈重複）
                stable_id = self._stable_file_id(file_url, filename)
                if stable_id in self.seen_file_urls:
                    continue

                self.seen_file_urls.add(stable_id)
                new_found += 1

                extracted_items.append(
                    MediaItem(
                        group_id=self.group_id,
                        group_name=self.config.name,
                        media_type=MediaType.FILE,
                        source_url=clean_url,
                        filename=filename,
                        media_id=stable_id,
                        post_author=f.get("author", ""),
                        post_time=datetime.utcnow().isoformat(),
                    )
                )

            if new_found == 0:
                consecutive_no_new += 1
                if consecutive_no_new >= 2:
                    break
            else:
                consecutive_no_new = 0

            await self.base.human_scroll(page, delay_range=self.base.config.scroll_delay_range)

        logger.info(f"檔案專區掃描完成，共發現 {len(extracted_items)} 個社團文件/檔案。")
        return extracted_items
