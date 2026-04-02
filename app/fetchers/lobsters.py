"""
Lobsters 抓取器
"""
import re
from typing import Optional

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient
from ..config import CONFIG
from ..utils.text import infer_tags


LOBSTERS_ML = re.compile(
    r"llm|language\s+model|transformer|inference|pytorch|jax|tensorflow|"
    r"cuda|openai|gpt|neural|machine\s+learning|mlp|diffusion|"
    r"\brag\b|embedding|\bmo[eE]\b|gpu|distributed|vllm|llama|quantization|"
    r"fine-?tun|training\s+at\s+scale|kernel|compiler.*ml",
    re.I,
)


@register_fetcher("lobsters")
class LobstersFetcher(BaseFetcher):
    """Lobsters抓取器"""
    
    def __init__(self, config=None):
        super().__init__(config or CONFIG.lobsters)
        self.base_url = "https://lobste.rs/hottest.json"
    
    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """抓取Lobsters热门帖子（过滤AI相关）"""
        async with RetryableHTTPClient(
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
        ) as client:
            
            try:
                response = await client.get(self.base_url)
                rows = response.json()
            except Exception:
                return FetchResult(items=[])
            
            if not isinstance(rows, list):
                return FetchResult(items=[])
            
            items = []
            for story in rows[:45]:
                if not isinstance(story, dict):
                    continue
                
                title = (story.get("title") or "").strip()
                url_link = (story.get("url") or "").strip()
                
                if not title or not url_link:
                    continue
                
                tag_str = " ".join(story.get("tags") or [])
                
                # 过滤AI相关内容
                if not LOBSTERS_ML.search(title + " " + tag_str):
                    continue
                
                score = int(story.get("score") or 0)
                comments = int(story.get("comment_count") or 0)
                
                raw_ct = story.get("created_at") or ""
                date_str = raw_ct[:10] if raw_ct else ""
                
                if len(date_str) < 8 and raw_ct:
                    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw_ct)
                    if m:
                        date_str = m.group(1)
                
                tags = infer_tags(title + " " + tag_str)
                
                items.append({
                    "type": "news",
                    "title": title,
                    "desc": f"Lobsters · {score}↑ · {comments} 评 · 技术社群",
                    "tags": tags,
                    "date": date_str,
                    "venue": "Lobsters",
                    "link": url_link,
                    "heat": 0,
                    "_lob_score": score,
                    "_lob_comments": comments,
                })
            
            return FetchResult(items=items[:14])
