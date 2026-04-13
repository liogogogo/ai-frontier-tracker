"""
HuggingFace Papers 抓取器
来源：https://huggingface.co/api/papers
特点：社区投票排序的最新 AI 论文，upvotes 可直接作为社交热度信号。
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient
from ..config import CONFIG
from ..utils.text import infer_tags


# 每次拉取的论文数量上限，API 单次最多 50 条
_MAX_FETCH = 50


@register_fetcher("huggingface")
class HuggingFaceFetcher(BaseFetcher):
    """HuggingFace Papers 社区热榜抓取器"""

    def __init__(self, config=None):
        super().__init__(config or CONFIG.huggingface)
        self.api_url = "https://huggingface.co/api/papers"

    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        limit = min(self.config.max_items, _MAX_FETCH)
        items: List[Dict[str, Any]] = []
        errors: List[str] = []

        async with RetryableHTTPClient(
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
        ) as client:
            try:
                params = {"q": "", "limit": limit}
                resp = await client.get(self.api_url, params=params)
                papers = resp.json()

                for paper in papers:
                    item = self._parse_paper(paper)
                    if item:
                        items.append(item)

            except Exception as exc:
                errors.append(str(exc))

        return FetchResult(
            items=items,
            error="; ".join(errors) if errors else None,
        )

    def _parse_paper(self, paper: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        arxiv_id = paper.get("id", "").strip()
        title = paper.get("title", "").strip()
        if not arxiv_id or not title:
            return None

        # 使用 arXiv canonical URL，便于与 arXiv fetcher 在去重层合并
        link = f"https://arxiv.org/abs/{arxiv_id}"

        summary = (paper.get("ai_summary") or paper.get("summary") or "").strip()

        published_raw = paper.get("publishedAt") or ""
        try:
            published_at = datetime.fromisoformat(
                published_raw.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            published_at = datetime.now(tz=timezone.utc)

        upvotes: int = paper.get("upvotes") or 0

        tags = infer_tags(f"{title} {summary}")

        # 组织信息可能不存在
        org = paper.get("organization") or {}
        org_name = org.get("name") or "papers"
        venue = f"huggingface:{org_name}"

        return {
            "title": title,
            "link": link,
            "desc": summary[:800] if summary else "",
            "date": published_at.strftime("%Y-%m-%d"),
            "venue": venue,
            "type": "paper",
            "tags": tags,
            # 将 upvotes 附带，供 heat 计算加成
            "upvotes": upvotes,
        }
