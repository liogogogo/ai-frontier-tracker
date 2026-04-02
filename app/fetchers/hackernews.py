"""
Hacker News 抓取器
"""
from datetime import datetime, timezone
from typing import Optional

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient
from ..config import CONFIG
from ..utils.text import infer_tags


HN_ALGOLIA_QUERIES = [
    "LLM inference",
    "vllm OR llama.cpp",
    "GPU training transformer distributed",
    "speculative decoding OR quantization llm",
    "RAG retrieval augmented generation",
    "CUDA kernel transformer OR flash attention",
    "OpenAI OR Anthropic OR Google Gemini release",
    "NVIDIA OR CUDA OR TensorRT inference LLM",
]


@register_fetcher("hackernews")
class HackerNewsFetcher(BaseFetcher):
    """Hacker News抓取器"""
    
    def __init__(self, config=None):
        super().__init__(config or CONFIG.hackernews)
        self.base_url = "https://hn.algolia.com/api/v1/search"
    
    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """搜索HN上的AI相关讨论"""
        async with RetryableHTTPClient(
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=0.28,
        ) as client:
            
            all_items = []
            seen_urls = set()
            
            for query in HN_ALGOLIA_QUERIES:
                try:
                    items = await self._search(client, query, seen_urls)
                    all_items.extend(items)
                except Exception:
                    continue
            
            return FetchResult(items=all_items[:28])
    
    async def _search(self, client: RetryableHTTPClient, query: str, seen_urls: set) -> list:
        """单次搜索"""
        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": 14,
        }
        
        response = await client.get(self.base_url, params=params)
        data = response.json()
        
        items = []
        for hit in data.get("hits", []):
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            
            points = int(hit.get("points") or 0)
            comments = int(hit.get("num_comments") or 0)
            
            # 过滤低热度内容
            if points < 4 and comments < 6:
                continue
            
            url_link = (hit.get("url") or "").strip()
            oid = str(hit.get("objectID") or hit.get("story_id") or "")
            
            if not url_link:
                url_link = f"https://news.ycombinator.com/item?id={oid}" if oid else ""
            
            if not url_link or url_link in seen_urls:
                continue
            seen_urls.add(url_link)
            
            created_i = hit.get("created_at_i")
            date_str = ""
            if isinstance(created_i, (int, float)):
                date_str = datetime.fromtimestamp(int(created_i), tz=timezone.utc).strftime("%Y-%m-%d")
            
            tags = infer_tags(title + " " + query)
            
            items.append({
                "type": "news",
                "title": title,
                "desc": f"Hacker News · {points}▲ · {comments} 条评论 · 社群热度",
                "tags": tags,
                "date": date_str,
                "venue": "Hacker News",
                "link": url_link,
                "heat": 0,
                "_hn_points": points,
                "_hn_comments": comments,
            })
        
        return items
