"""
arXiv 抓取器 - 支持 ETag/Last-Modified 增量抓取
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from xml.etree import ElementTree as ET

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient, NotModifiedError
from ..utils.etag_cache import get_etag_cache
from ..config import CONFIG
from ..utils.text import strip_html, infer_tags


ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@register_fetcher("arxiv")
class ArxivFetcher(BaseFetcher):
    """arXiv论文抓取器 - 支持条件请求缓存"""

    def __init__(self, config=None):
        super().__init__(config or CONFIG.arxiv)
        self.base_url = "https://export.arxiv.org/api/query"
        self.etag_cache = get_etag_cache()
        self._not_modified_count = 0
        self._cached_items: List[Dict[str, Any]] = []  # 缓存上次内容

    def _query_broad(self) -> str:
        return "cat:cs.LG OR cat:cs.CL OR cat:cs.AI"

    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """单次 API 调用（支持ETag缓存）"""
        async with RetryableHTTPClient(
            base_headers={
                "User-Agent": "AI-Frontier-Tracker/2 (+https://arxiv.org/help/api; scholarly aggregator)",
            },
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=self.config.rate_limit_delay,
            cache_condition=True,  # 启用条件请求
        ) as client:
            n = max(12, min(40, int(self.config.max_items or 24)))
            used_cache = False

            try:
                results = await self._fetch_one(client, self._query_broad(), n)
                # 更新缓存内容
                self._cached_items = results
            except NotModifiedError:
                # 304 Not Modified - 使用缓存内容
                self._not_modified_count += 1
                used_cache = True
                results = self._cached_items

            by_link = {}
            for item in results:
                link = item.get("link", "")
                if link and link not in by_link:
                    by_link[link] = item

            items = list(by_link.values())

            # 构建状态信息
            status_msg = None
            if used_cache:
                status_msg = f"[arXiv 未变化(304)，使用缓存 {len(items)} 条]"

            return FetchResult(
                items=items,
                status=status_msg,
                source_status={
                    "outcome": "unchanged" if used_cache else "success",
                    "used_cache": used_cache,
                    "fetched_count": len(items),
                    "not_modified": used_cache,
                },
                cursor=None  # arXiv 不支持游标分页
            )

    async def _fetch_one(self, client: RetryableHTTPClient, query: str, max_results: int) -> List[Dict[str, Any]]:
        """单次arXiv查询（支持ETag缓存）"""
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        # 获取条件请求头
        etag_headers = self.etag_cache.get_headers(self.base_url)

        try:
            response = await client.get(self.base_url, params=params, etag_headers=etag_headers)

            # 更新缓存标记
            self.etag_cache.update(
                self.base_url,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified")
            )

            return self._parse_entries(response.text)

        except NotModifiedError:
            # 304 Not Modified - 内容未变化
            raise

    def _parse_entries(self, xml_text: str) -> List[Dict[str, Any]]:
        """解析arXiv Atom响应"""
        root = ET.fromstring(xml_text)
        items = []

        for entry in root.findall("atom:entry", ARXIV_NS):
            title_el = entry.find("atom:title", ARXIV_NS)
            summary_el = entry.find("atom:summary", ARXIV_NS)
            published_el = entry.find("atom:published", ARXIV_NS)
            id_el = entry.find("atom:id", ARXIV_NS)

            title = (title_el.text or "").strip().replace("\n", " ") if title_el else ""
            summary = strip_html(summary_el.text or "") if summary_el else ""
            desc = summary[:320] + ("…" if len(summary) > 320 else "")
            link = (id_el.text or "").strip() if id_el else ""

            date_str = ""
            if published_el is not None and published_el.text:
                try:
                    dt = datetime.fromisoformat(published_el.text.replace("Z", "+00:00"))
                    date_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
                except ValueError:
                    date_str = published_el.text[:10]

            tags = infer_tags(title + " " + summary)

            venue = "arXiv"
            prim = entry.find("arxiv:primary_category", ARXIV_NS)
            if prim is not None and prim.get("term"):
                venue = prim.get("term", venue)

            items.append({
                "type": "paper",
                "title": title,
                "desc": desc or "arXiv 预印本",
                "tags": tags,
                "date": date_str,
                "venue": venue,
                "link": link,
                "heat": 0,
            })

        return items

    def get_stats(self):
        """获取统计信息"""
        stats = super().get_stats()
        stats.not_modified_count = self._not_modified_count
        stats.cache_stats = self.etag_cache.stats()
        return stats
