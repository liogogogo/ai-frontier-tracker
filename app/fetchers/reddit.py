"""
Reddit 抓取器
"""
from datetime import datetime, timezone
from typing import Optional

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient
from ..config import CONFIG
from ..utils.text import infer_tags


REDDIT_SUBS_HOT = [
    ("LocalLLaMA", 16),
    ("HuggingFace", 8),
    ("MachineLearning", 6),
]


@register_fetcher("reddit")
class RedditFetcher(BaseFetcher):
    """Reddit抓取器"""
    
    def __init__(self, config=None):
        super().__init__(config or CONFIG.reddit)
        self.subreddits = REDDIT_SUBS_HOT
    
    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """抓取Reddit热门帖子"""
        # 使用友好的User-Agent
        base_headers = {
            "User-Agent": "AIFrontierTracker/2.0 (read-only community digest; educational project)"
        }
        
        async with RetryableHTTPClient(
            base_headers=base_headers,
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=0.45,
        ) as client:
            
            all_items = []
            
            for sub, limit in self.subreddits:
                try:
                    items = await self._fetch_subreddit(client, sub, limit)
                    all_items.extend(items)
                except Exception:
                    continue
            
            return FetchResult(items=all_items[:26])
    
    async def _fetch_subreddit(self, client: RetryableHTTPClient, sub: str, limit: int) -> list:
        """抓取单个subreddit"""
        url = f"https://www.reddit.com/r/{sub}/hot.json"
        params = {"limit": limit}
        
        response = await client.get(url, params=params)
        
        # Reddit返回403时跳过
        if response.status_code == 403:
            return []
        
        response.raise_for_status()
        data = response.json()
        
        items = []
        children = (data.get("data") or {}).get("children") or []
        
        for child in children:
            post = (child.get("data") or {}) if isinstance(child, dict) else {}
            title = (post.get("title") or "").strip()
            if not title:
                continue
            
            score = int(post.get("score") or 0)
            comments = int(post.get("num_comments") or 0)
            
            if score < 4 and comments < 4:
                continue
            
            url_link = (post.get("url_overridden_by_dest") or post.get("url") or "").strip()
            
            if not url_link or url_link.startswith("/"):
                perm = (post.get("permalink") or "").strip()
                if perm.startswith("/"):
                    url_link = "https://www.reddit.com" + perm
                elif perm:
                    url_link = perm
            elif url_link.startswith("/r/"):
                url_link = "https://www.reddit.com" + url_link
            
            if not url_link:
                continue
            
            created = post.get("created_utc")
            date_str = ""
            if isinstance(created, (int, float)):
                date_str = datetime.fromtimestamp(float(created), tz=timezone.utc).strftime("%Y-%m-%d")
            
            tags = infer_tags(title + f" {sub}")
            
            items.append({
                "type": "news",
                "title": title,
                "desc": f"Reddit r/{sub} · {score}↑ · {comments} 讨论 · 近期热度",
                "tags": tags,
                "date": date_str,
                "venue": f"Reddit r/{sub}",
                "link": url_link,
                "heat": 0,
                "_reddit_score": score,
                "_reddit_comments": comments,
            })
        
        return items
