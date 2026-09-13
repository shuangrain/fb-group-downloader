import asyncio
import re
from datetime import datetime, timezone

from playwright.async_api import Page, Response

from fb_group_downloader.config import GroupConfig
from fb_group_downloader.downloader.models import AlbumBundle, MediaItem, MediaType
from fb_group_downloader.scraper.base import BaseScraper
from fb_group_downloader.scraper.photo_extractor import FacebookPhotoExtractor
from fb_group_downloader.utils.date_parser import parse_fb_date
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class GroupMediaScraper:
    def __init__(self, base_scraper: BaseScraper, group_config: GroupConfig):
        self.base = base_scraper
        self.config = group_config
        self.group_id = group_config.group_id
        self.seen_album_ids: set[str] = set()
        self.intercepted_album_times: dict[str, str] = {}

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

    async def _handle_response(self, response: Response) -> None:
        """監聽網路回應，攔截 GraphQL 回傳的相簿建立時間"""
        if "graphql" in response.url and response.status == 200:
            try:
                text = await response.text()
                # 擷取 (album_id, created_time)
                matches = re.findall(
                    r'"(?:id|album_id)":\s*"(\d+)".*?"(?:created_time|creation_time)":\s*(\d{10})',
                    text,
                )
                for a_id, ts in matches:
                    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
                    self.intercepted_album_times[a_id] = dt.isoformat()
            except Exception:
                pass

    async def scan_albums(self, page: Page) -> list[AlbumBundle]:
        """
        掃描社團相簿專區 (https://www.facebook.com/groups/{group_id}/media/albums)
        並依序抓取相簿內的相片建立 AlbumBundle
        """
        page.on("response", self._handle_response)

        url = f"https://www.facebook.com/groups/{self.group_id}/media/albums"
        logger.info(f"正在前往社團相簿專區：{url}")

        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await self.base.close_dialogs(page)

        # 擷取所有相簿列表
        albums_info = await page.evaluate(
            r"""() => {
                const results = [];
                const links = Array.from(document.querySelectorAll('a[href*="/media/set/"], a[href*="/albums/"]'));
                for (const link of links) {
                    const href = link.href;
                    const textElem = link.querySelector('span') || link;
                    let title = textElem.innerText ? textElem.innerText.trim() : "";
                    if (title && !title.includes('建立相簿') && !title.includes('Create Album') && !title.includes('相簿\n更多')) {
                        // 清理標題，去除張數後綴（如 "8/31-9/11學習區\\n72張相片" -> "8/31-9/11學習區"）
                        const cleanTitle = title.split('\\n')[0].trim();
                        results.push({
                            url: href,
                            title: cleanTitle
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
                    r"""() => {
                        const getBestImgSrc = (img) => {
                            let bestUrl = img.src || "";
                            let maxWidth = 0;

                            if (img.srcset) {
                                const parts = img.srcset.split(',');
                                for (const p of parts) {
                                    const trimmed = p.trim();
                                    const spaceIdx = trimmed.lastIndexOf(' ');
                                    if (spaceIdx > 0) {
                                        const url = trimmed.substring(0, spaceIdx).trim();
                                        const desc = trimmed.substring(spaceIdx + 1).trim();
                                        let width = 0;
                                        if (desc.endsWith('w')) {
                                            width = parseInt(desc.replace('w', ''), 10);
                                        } else if (desc.endsWith('x')) {
                                            width = parseFloat(desc.replace('x', '')) * 1000;
                                        }
                                        if (width > maxWidth && url.startsWith('http')) {
                                            maxWidth = width;
                                            bestUrl = url;
                                        }
                                    }
                                }
                            }

                            if (!maxWidth && img.currentSrc && img.currentSrc.startsWith('http')) {
                                bestUrl = img.currentSrc;
                            }

                            return bestUrl;
                        };

                        const imgs = Array.from(document.querySelectorAll('img[src*="fbcdn.net"], img[src*="scontent"]'));
                        return imgs.map(img => {
                            const parent = img.closest('a');
                            return {
                                src: getBestImgSrc(img),
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

                    # 若網址仍為縮圖標記且有相片頁面連結，透過 Photo Viewer 解析大圖
                    if FacebookPhotoExtractor.is_thumbnail_url(src) and photo_page_url:
                        high_res = await FacebookPhotoExtractor.resolve_high_res_photo_url(
                            page, photo_page_url, fallback_url=src
                        )
                        if high_res:
                            src = high_res

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
                # 依序使用 GraphQL 攔截的建立時間、或標題內嵌日期、或當前時間
                album_time = self.intercepted_album_times.get(album_id) or parse_fb_date(
                    None, reference_text=album_title
                )

                bundle = AlbumBundle(
                    group_id=self.group_id,
                    group_name=self.config.name,
                    album_id=album_id,
                    album_name=album_title,
                    album_url=album_url,
                    album_time=album_time,
                    media_items=media_items,
                )
                album_bundles.append(bundle)
                logger.info(f"✓ 相簿【{album_title}】掃描完成，包含 {len(media_items)} 張相片。")

        return album_bundles
