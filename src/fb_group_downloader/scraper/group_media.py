import asyncio
import re
from datetime import datetime

from playwright.async_api import Page

from fb_group_downloader.config import GroupConfig
from fb_group_downloader.downloader.models import AlbumBundle, MediaItem, MediaType
from fb_group_downloader.scraper.base import BaseScraper
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class GroupMediaScraper:
    def __init__(self, base_scraper: BaseScraper, group_config: GroupConfig):
        self.base = base_scraper
        self.config = group_config
        self.group_id = group_config.group_id
        self.seen_album_ids: set[str] = set()

    def _extract_album_id(self, url: str) -> str:
        """從相簿網址擷取 Album ID"""
        m = re.search(r"/media/set/\?set=[a-z\.]+(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/albums/(\d+)", url)
        if m:
            return m.group(1)
        return f"album_{hash(url) & 0xFFFFFFFF:08x}"

    def _extract_photo_id(self, url: str) -> str:
        m = re.search(r"fbid=(\d+)", url)
        if m:
            return m.group(1)
        return ""

    async def scan_albums(self, page: Page) -> list[AlbumBundle]:
        """
        掃描社團相簿專區 (https://www.facebook.com/groups/{group_id}/media/albums)
        並依序抓取相簿內的相片建立 AlbumBundle
        """
        url = f"https://www.facebook.com/groups/{self.group_id}/media/albums"
        logger.info(f"正在前往社團相簿專區：{url}")

        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await self.base.close_dialogs(page)

        # 擷取所有相簿列表
        albums_info = await page.evaluate(
            """() => {
                const results = [];
                const links = Array.from(document.querySelectorAll('a[href*="/media/set/"], a[href*="/albums/"]'));
                for (const link of links) {
                    const href = link.href;
                    const textElem = link.querySelector('span') || link;
                    const title = textElem.innerText ? textElem.innerText.trim() : "";
                    if (title && !title.includes('建立相簿') && !title.includes('Create Album')) {
                        results.push({
                            url: href,
                            title: title
                        });
                    }
                }
                return results;
            }"""
        )

        album_bundles: list[AlbumBundle] = []
        logger.info(f"在相簿專區發現 {len(albums_info)} 本相簿。")

        for album in albums_info:
            album_url = album.get("url", "")
            album_title = album.get("title", "")
            album_id = self._extract_album_id(album_url)

            if album_id in self.seen_album_ids:
                continue
            self.seen_album_ids.add(album_id)

            logger.info(f"正在掃描相簿：【{album_title}】 (ID: {album_id})...")
            await page.goto(album_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # 滾動加載相簿內的圖片
            photo_urls_seen: set[str] = set()
            media_items: list[MediaItem] = []

            for _ in range(3):
                photos_data = await page.evaluate(
                    """() => {
                        const imgs = Array.from(document.querySelectorAll('img[src*="fbcdn.net"], img[src*="scontent"]'));
                        return imgs.map(img => {
                            const parent = img.closest('a');
                            return {
                                src: img.src,
                                photoUrl: parent ? parent.href : ""
                            };
                        });
                    }"""
                )

                for p in photos_data:
                    src = p.get("src")
                    photo_page_url = p.get("photoUrl", "")
                    if not src or src in photo_urls_seen:
                        continue
                    photo_urls_seen.add(src)
                    photo_id = self._extract_photo_id(photo_page_url) or f"img_{len(media_items) + 1}"

                    media_items.append(
                        MediaItem(
                            group_id=self.group_id,
                            group_name=self.config.name,
                            media_type=MediaType.IMAGE,
                            source_url=src,
                            media_id=photo_id,
                            album_id=album_id,
                            album_name=album_title,
                            album_url=album_url,
                        )
                    )

                await self.base.human_scroll(page)

            if media_items:
                bundle = AlbumBundle(
                    group_id=self.group_id,
                    group_name=self.config.name,
                    album_id=album_id,
                    album_name=album_title,
                    album_url=album_url,
                    album_time=datetime.utcnow().isoformat(),
                    media_items=media_items,
                )
                album_bundles.append(bundle)
                logger.info(f"✓ 相簿【{album_title}】掃描完成，包含 {len(media_items)} 張相片。")

        return album_bundles
