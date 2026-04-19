"""
RSS 抓取器 - 支持 ETag/Last-Modified 增量抓取
"""
import re
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient, NotModifiedError
from ..utils.etag_cache import get_etag_cache
from ..config import CONFIG
from ..utils.text import strip_html, infer_tags


# 部分站点（含 Microsoft 博客）会拦截默认 httpx User-Agent，需浏览器标识
_RSS_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 AI-Frontier-Tracker/2"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}


# RSS订阅源配置 - 支持论文源标记
# 优化说明：
#   - Google 家族合并为 3 个核心源，避免重复
#   - 新增 TechCrunch AI、VentureBeat AI 捕捉产品/融资动向
RSS_FEEDS = [
    # 新闻源 (type=news)
    ("OpenAI Blog", "https://openai.com/blog/rss.xml", 8, "news"),
    ("Google / DeepMind Blog", "https://blog.google/innovation-and-ai/models-and-research/google-deepmind/rss/", 8, "news"),
    # 保留 DeepMind 独立源（DeepMind 专属研究公告，与 Google/DeepMind 合并博客互补）
    ("DeepMind", "https://deepmind.google/blog/rss.xml", 6, "news"),
    # Google AI 技术博客（合并原 Google AI Blog + Google Research Blog + Google Developers AI 三源为一）
    ("Google AI & Research", "https://blog.google/innovation-and-ai/technology/ai/rss/", 8, "news"),
    ("Google Cloud — AI & ML", "https://cloudblog.withgoogle.com/products/ai-machine-learning/rss/", 6, "news"),
    ("Hugging Face Blog", "https://huggingface.co/blog/feed.xml", 8, "news"),
    ("NVIDIA Blog", "https://blogs.nvidia.com/feed/", 7, "news"),
    ("AWS Machine Learning Blog", "https://aws.amazon.com/blogs/machine-learning/feed/", 7, "news"),
    ("Microsoft AI Blog", "https://blogs.microsoft.com/ai/feed/", 7, "news"),
    ("Meta Engineering", "https://engineering.fb.com/feed/", 7, "news"),
    # 论文源见独立 arxiv fetcher（关键词限定为 LLM/Agent 等）。
    # HF Papers 官方 RSS 曾公开，现常 401；若恢复可换自建 RSSHub 或 HF API 集成。
    # Berkeley AI Research（官方博客，归类为 news）
    ("BAIR", "https://bair.berkeley.edu/blog/feed.xml", 10, "news"),
    # Stanford HAI（机构资讯流，归类为 news）
    ("Stanford HAI", "https://hai.stanford.edu/news?format=rss", 8, "news"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/", 7, "news"),
    ("Apple ML Research", "https://machinelearning.apple.com/rss.xml", 7, "news"),
    # Anthropic / xAI（通过 GitHub 托管的 RSS）
    (
        "Anthropic News",
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_anthropic_news.xml",
        6, "news",
    ),
    (
        "xAI News",
        "https://raw.githubusercontent.com/Olshansk/rss-feeds/main/feeds/feed_xainews.xml",
        6, "news",
    ),
    # 新增：AI 产品发布与行业动向
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 7, "news"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", 6, "news"),
]


@register_fetcher("rss")
class RSSFetcher(BaseFetcher):
    """RSS聚合抓取器 - 支持条件请求缓存"""

    def __init__(self, config=None):
        super().__init__(config or CONFIG.rss)
        self.feeds = RSS_FEEDS
        self.etag_cache = get_etag_cache()
        self._not_modified_count = 0
        self._cached_items_by_url: dict[str, list] = {}

    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """顺序抓取所有RSS源（支持ETag缓存）"""
        all_items = []
        errors = []
        not_modified_sources = []
        cached_sources = []

        async with RetryableHTTPClient(
            base_headers=_RSS_HTTP_HEADERS,
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=self.config.rate_limit_delay,
            cache_condition=True,  # 启用条件请求
        ) as client:

            for feed_config in self.feeds:
                # 解析配置元组
                if len(feed_config) == 4:
                    name, url, max_items, item_type = feed_config
                else:
                    name, url, max_items = feed_config
                    item_type = "news"  # 默认类型

                try:
                    items, used_cache = await self._fetch_single(client, name, url, max_items, item_type)
                    all_items.extend(items)
                    if used_cache:
                        self._not_modified_count += 1
                        not_modified_sources.append(name)
                        if items:
                            cached_sources.append(name)
                except Exception as e:
                    errors.append(f"{name}: {str(e)}")

        # 构建状态信息
        status_parts = []
        if not_modified_sources:
            if cached_sources:
                status_parts.append(f"{len(not_modified_sources)} 个源未变化(304)，其中 {len(cached_sources)} 个复用缓存")
            else:
                status_parts.append(f"{len(not_modified_sources)} 个源未变化(304)")
        if errors:
            status_parts.append(f"{len(errors)} 个源失败")

        outcome = "success"
        if errors and all_items:
            outcome = "partial"
        elif errors:
            outcome = "error"
        elif not_modified_sources:
            outcome = "unchanged"

        return FetchResult(
            items=all_items,
            error="; ".join(errors) if errors else None,
            status=f"[{', '.join(status_parts)}]" if status_parts else None,
            source_status={
                "outcome": outcome,
                "used_cache": bool(cached_sources),
                "fetched_count": len(all_items),
                "unchanged_sources": len(not_modified_sources),
                "failed_sources": len(errors),
                "total_sources": len(self.feeds),
                "cached_sources": cached_sources,
                "not_modified": bool(not_modified_sources),
            },
        )

    async def _fetch_single(self, client: RetryableHTTPClient, name: str, url: str, max_items: int, item_type: str = "news") -> tuple[list, bool]:
        """抓取单个RSS源（支持ETag缓存）"""
        # 获取条件请求头
        etag_headers = self.etag_cache.get_headers(url)

        try:
            response = await client.get(url, etag_headers=etag_headers)

            # 更新缓存标记
            self.etag_cache.update(
                url,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified")
            )

        except NotModifiedError:
            # 304 Not Modified - 优先使用内存缓存内容
            return list(self._cached_items_by_url.get(url, [])), True

        items = []
        cap = max(3, min(max_items, 14))

        for title, link, summary, raw_date in self._parse_rss_atom(response.content)[:cap]:
            desc = summary[:320] + ("…" if len(summary) > 320 else "")
            date_str = self._normalize_date(raw_date)
            tags = infer_tags(title + " " + summary)

            items.append({
                "type": item_type,
                "title": title,
                "desc": desc or "资讯",
                "tags": tags,
                "date": date_str,
                "venue": name,
                "link": link,
                "heat": 0,
            })

        self._cached_items_by_url[url] = list(items)
        return items, False
    
    def get_stats(self):
        """获取统计信息"""
        stats = super().get_stats()
        stats.not_modified_count = self._not_modified_count
        stats.cache_stats = self.etag_cache.stats()
        return stats
    
    def _parse_rss_atom(self, xml_bytes: bytes) -> list:
        """解析RSS 2.0或Atom"""
        out = []
        
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError:
            return out
        
        def localname(tag: str) -> str:
            if "}" in tag:
                return tag.split("}", 1)[1]
            return tag
        
        tag_root = localname(root.tag)
        
        if tag_root == "rss":
            channel = root.find("channel")
            if channel is None:
                return out
            
            for item in channel.findall("item")[:10]:
                t_el = item.find("title")
                l_el = item.find("link")
                d_el = item.find("description") or item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                p_el = item.find("pubDate") or item.find("date")
                
                title = strip_html(t_el.text if t_el is not None and t_el.text else "")
                link = (l_el.text or "").strip() if l_el is not None and l_el.text else ""
                summary = strip_html(d_el.text if d_el is not None and d_el.text else "")
                raw_date = (p_el.text or "").strip() if p_el is not None and p_el.text else ""
                
                if title and link:
                    out.append((title, link, summary, raw_date))
        
        elif tag_root == "feed":
            ns_uri = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else "http://www.w3.org/2005/Atom"
            ns = {"a": ns_uri}
            
            for entry in root.findall("a:entry", ns)[:10]:
                t_el = entry.find("a:title", ns)
                summary_el = entry.find("a:summary", ns) or entry.find("a:content", ns)
                updated_el = entry.find("a:updated", ns) or entry.find("a:published", ns)
                
                title = strip_html(t_el.text if t_el is not None and t_el.text else "")
                summary = strip_html(summary_el.text if summary_el is not None and summary_el.text else "")
                
                link = ""
                for lk in entry.findall("a:link", ns):
                    href = lk.get("href") or ""
                    rel = lk.get("rel") or "alternate"
                    if rel == "alternate" or not link:
                        link = href
                
                raw_date = (updated_el.text or "").strip() if updated_el is not None and updated_el.text else ""
                
                if title and link:
                    out.append((title, link, summary, raw_date))
        
        return out
    
    def _normalize_date(self, raw_date: str) -> str:
        """标准化日期格式"""
        if not raw_date:
            return ""
        
        date_str = raw_date[:10]
        
        if len(date_str) < 8:
            # RFC822格式: Tue, 31 Mar 2026 ...
            m = re.search(r"\d{1,2}\s+\w+\s+\d{4}", raw_date)
            if m:
                try:
                    dt = datetime.strptime(m.group(0), "%d %b %Y")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        
        return date_str
