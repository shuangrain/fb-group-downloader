import asyncio
import re
from datetime import datetime

from playwright.async_api import Page, Response

from fb_group_downloader.config import GroupConfig
from fb_group_downloader.downloader.models import MediaItem, MediaType, PostBundle
from fb_group_downloader.scraper.base import BaseScraper
from fb_group_downloader.scraper.video_extractor import FacebookVideoExtractor
from fb_group_downloader.utils.logger import get_logger

logger = get_logger()


class GroupFeedScraper:
    def __init__(self, base_scraper: BaseScraper, group_config: GroupConfig):
        self.base = base_scraper
        self.config = group_config
        self.group_id = group_config.group_id
        self.seen_post_ids: set[str] = set()
        self.seen_media_urls: set[str] = set()

        # 儲存網路攔截到的影片串流直鏈：video_id / post_id -> mp4_url
        self.intercepted_video_streams: dict[str, str] = {}
        self.captured_cdn_mp4s: list[str] = []

    def _extract_post_id(self, url: str) -> str | None:
        """從貼文 URL 中擷取 Post ID"""
        patterns = [
            r"/posts/(\d+)",
            r"/permalink/(\d+)",
            r"multi_permalinks=(\d+)",
            r"story_fbid=(\d+)",
            r"/videos/(\d+)",
            r"v=(\d+)",
            r"/photos/[^/]+/(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    def _extract_photo_id(self, url: str) -> str | None:
        """從相片 URL 中擷取 Photo ID / FBID"""
        patterns = [
            r"fbid=(\d+)",
            r"/photo/\?fbid=(\d+)",
            r"/photos/[^/]+/(\d+)",
            r"/photos/(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    def _extract_video_id(self, url: str) -> str | None:
        """從影片網址擷取 Video ID"""
        patterns = [
            r"/videos/[^/]+/(\d+)",
            r"/videos/(\d+)",
            r"v=(\d+)",
            r"story_fbid=(\d+)",
        ]
        for pat in patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    async def _handle_response(self, response: Response) -> None:
        """監聽網路回應，攔截 GraphQL 與 CDN 中的高解析度影片直鏈"""
        url = response.url
        try:
            # 攔截 direct CDN mp4 請求
            if ".mp4" in url and "fbcdn.net" in url:
                if url not in self.captured_cdn_mp4s:
                    self.captured_cdn_mp4s.append(url)
                    logger.debug(f"攔截到 CDN 影片直鏈：{url[:80]}...")

            # 攔截 GraphQL 回傳的影片 JSON 結構
            if "graphql" in url and response.status == 200:
                try:
                    text = await response.text()
                    matches = re.findall(r'"playable_url(?:_quality_hd)?":\s*"([^"]+)"', text)
                    ids = re.findall(r'"video_id":\s*"(\d+)"', text) or re.findall(r'"id":\s*"(\d+)"', text)
                    for m_url in matches:
                        clean_url = m_url.replace("\\/", "/")
                        if ids:
                            for v_id in ids:
                                self.intercepted_video_streams[v_id] = clean_url
                        if clean_url not in self.captured_cdn_mp4s:
                            self.captured_cdn_mp4s.append(clean_url)
                except Exception:
                    pass
        except Exception:
            pass

    async def scan_feed(self, page: Page, stop_at_post_id: str | None = None) -> list[PostBundle]:
        """
        掃描社團動態貼文，依序將每篇貼文包裝為 PostBundle（包含圖片、影片與附件檔案）
        """
        page.on("response", self._handle_response)

        url = f"https://www.facebook.com/groups/{self.group_id}?sorting_setting=CHRONOLOGICAL"
        logger.info(f"正在前往社團動態頁面：{url}")

        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await self.base.close_dialogs(page)

        extracted_bundles: list[PostBundle] = []
        consecutive_no_new_posts = 0
        max_posts = self.config.max_posts_per_scan

        logger.info(f"開始以貼文為單位掃描（最多掃描 {max_posts} 篇貼文）...")

        while len(self.seen_post_ids) < max_posts:
            posts_data = await page.evaluate(
                """() => {
                    const articles = Array.from(document.querySelectorAll('div[role="feed"] > div, div[role="article"], div[data-pagelet^="FeedUnit"]'));
                    const results = [];

                    for (const article of articles) {
                        const links = Array.from(article.querySelectorAll('a[href*="/posts/"], a[href*="/permalink/"], a[href*="multi_permalinks="], a[href*="story_fbid="]'));
                        let postUrl = links.length > 0 ? links[0].href : "";

                        const authorElem = article.querySelector('h2 strong, h3 strong, a strong, strong span');
                        const author = authorElem ? authorElem.innerText.trim() : "";

                        const msgElem = article.querySelector('div[data-ad-preview="message"], div[dir="auto"]');
                        const text = msgElem ? msgElem.innerText.trim() : "";

                        const images = [];
                        const imgElems = Array.from(article.querySelectorAll('img[src*="fbcdn.net"], img[src*="scontent"]'));
                        for (const img of imgElems) {
                            const src = img.src;
                            if (img.naturalWidth > 150 || img.naturalHeight > 150 || (!img.naturalWidth && !src.includes('emoji.php') && !src.includes('rsrc.php'))) {
                                const parentLink = img.closest('a');
                                const photoViewerUrl = parentLink ? parentLink.href : "";
                                images.push({
                                    src: src,
                                    photoUrl: photoViewerUrl,
                                    alt: img.alt || ""
                                });
                            }
                        }

                        const videos = [];
                        const videoElems = Array.from(article.querySelectorAll('video'));
                        for (const v of videoElems) {
                            if (v.src && !v.src.startsWith('blob:')) {
                                videos.push({ url: v.src, isDirect: true });
                            }
                        }
                        const videoLinks = Array.from(article.querySelectorAll('a[href*="/videos/"], a[href*="/watch"], div[data-video-id]'));
                        for (const vl of videoLinks) {
                            const href = vl.href || (vl.getAttribute('data-video-id') ? `https://www.facebook.com/watch/?v=${vl.getAttribute('data-video-id')}` : "");
                            if (href) {
                                videos.push({ url: href, isDirect: false });
                            }
                        }

                        const files = [];
                        const fileLinks = Array.from(article.querySelectorAll('a[href*="/download/"], a[href*="/files/"], a[href*="download"], a[href*="attachment"]'));
                        for (const fl of fileLinks) {
                            files.push({
                                url: fl.href,
                                text: fl.innerText ? fl.innerText.trim() : ""
                            });
                        }

                        if (postUrl || images.length > 0 || videos.length > 0 || files.length > 0) {
                            results.push({
                                postUrl: postUrl,
                                author: author,
                                text: text,
                                images: images,
                                videos: videos,
                                files: files
                            });
                        }
                    }
                    return results;
                }"""
            )

            new_posts_found = 0

            for p_data in posts_data:
                post_url = p_data.get("postUrl", "")
                post_id = self._extract_post_id(post_url) if post_url else None

                if not post_id:
                    post_id = f"post_{hash(post_url or p_data.get('text', '') or str(len(self.seen_post_ids))) & 0xFFFFFFFF:08x}"

                if post_id in self.seen_post_ids:
                    continue

                self.seen_post_ids.add(post_id)
                new_posts_found += 1

                if stop_at_post_id and post_id == stop_at_post_id:
                    logger.info(f"已抵達上次同步的貼文 (ID: {stop_at_post_id})，停止向下滾動。")
                    return extracted_bundles

                author = p_data.get("author", "")
                text = p_data.get("text", "")
                post_time = datetime.utcnow().isoformat()
                media_items: list[MediaItem] = []

                # 收集圖片
                if self.config.download_images:
                    for idx, img_info in enumerate(p_data.get("images", [])):
                        img_src = img_info.get("src")
                        photo_url = img_info.get("photoUrl", "")
                        media_id = self._extract_photo_id(photo_url) or f"{post_id}_img_{idx + 1}"

                        if img_src and img_src not in self.seen_media_urls:
                            self.seen_media_urls.add(img_src)
                            media_items.append(
                                MediaItem(
                                    group_id=self.group_id,
                                    group_name=self.config.name,
                                    media_type=MediaType.IMAGE,
                                    source_url=img_src,
                                    media_id=media_id,
                                    post_id=post_id,
                                    post_author=author,
                                    post_text=text,
                                    post_url=post_url,
                                    post_time=post_time,
                                )
                            )

                # 收集影片（優先比對網路攔截到的直鏈，若無則透過 FacebookVideoExtractor 即時解析）
                if self.config.download_videos:
                    for _idx, vid_info in enumerate(p_data.get("videos", [])):
                        vid_url = vid_info.get("url")
                        vid_id = self._extract_video_id(vid_url) or post_id

                        direct_stream_url = self.intercepted_video_streams.get(vid_id)
                        if not direct_stream_url and vid_url and not vid_url.endswith(".mp4"):
                            direct_stream_url = await FacebookVideoExtractor.resolve_video_url(page, vid_url)
                            if direct_stream_url:
                                self.intercepted_video_streams[vid_id] = direct_stream_url

                        final_vid_url = direct_stream_url or vid_url

                        if final_vid_url and final_vid_url not in self.seen_media_urls:
                            self.seen_media_urls.add(final_vid_url)
                            media_items.append(
                                MediaItem(
                                    group_id=self.group_id,
                                    group_name=self.config.name,
                                    media_type=MediaType.VIDEO,
                                    source_url=final_vid_url,
                                    media_id=f"{vid_id}",
                                    post_id=post_id,
                                    post_author=author,
                                    post_text=text,
                                    post_url=post_url or vid_url,
                                    post_time=post_time,
                                )
                            )

                # 收集附件檔案
                if self.config.download_files:
                    for idx, file_info in enumerate(p_data.get("files", [])):
                        file_url = file_info.get("url")
                        file_name = file_info.get("text")
                        if file_url and file_url not in self.seen_media_urls:
                            self.seen_media_urls.add(file_url)
                            media_items.append(
                                MediaItem(
                                    group_id=self.group_id,
                                    group_name=self.config.name,
                                    media_type=MediaType.FILE,
                                    source_url=file_url,
                                    filename=file_name,
                                    media_id=f"{post_id}_file_{idx + 1}",
                                    post_id=post_id,
                                    post_author=author,
                                    post_text=text,
                                    post_url=post_url,
                                    post_time=post_time,
                                )
                            )

                # 若貼文包含任何媒體或附件，封裝為 PostBundle
                if media_items:
                    bundle = PostBundle(
                        group_id=self.group_id,
                        group_name=self.config.name,
                        post_id=post_id,
                        post_author=author,
                        post_text=text,
                        post_url=post_url,
                        post_time=post_time,
                        media_items=media_items,
                    )
                    extracted_bundles.append(bundle)

            if new_posts_found == 0:
                consecutive_no_new_posts += 1
                if consecutive_no_new_posts >= 4:
                    logger.info("已滾動至底部或無更多新貼文載入。")
                    break
            else:
                consecutive_no_new_posts = 0

            await self.base.human_scroll(page, delay_range=self.base.config.scroll_delay_range)

        logger.info(
            f"動態掃描完成，共掃描 {len(self.seen_post_ids)} 篇貼文，發現 {len(extracted_bundles)} 篇含有媒體的貼文。"
        )
        return extracted_bundles
