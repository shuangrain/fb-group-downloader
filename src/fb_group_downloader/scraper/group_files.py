import asyncio
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

            new_found = 0
            for f in files_data:
                file_url = f.get("url", "")
                filename = f.get("filename", "")

                # 去重
                unique_key = f"{file_url}_{filename}"
                if unique_key in self.seen_file_urls:
                    continue

                self.seen_file_urls.add(unique_key)
                new_found += 1

                extracted_items.append(
                    MediaItem(
                        group_id=self.group_id,
                        group_name=self.config.name,
                        media_type=MediaType.FILE,
                        source_url=file_url,
                        filename=filename,
                        media_id=f"file_{hash(unique_key) & 0xFFFFFFFF:08x}",
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
