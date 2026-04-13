"""
Firecrawl 聚合抓取器（最小可行）
- 所有外部内容均通过 Firecrawl v2 API 获取
- 不直接请求源站 RSS/API
"""
from typing import Optional

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..config import CONFIG
from ..services.firecrawl import get_firecrawl_service
from ..utils.text import infer_tags


def _infer_item_type(link: str) -> str:
    u = (link or "").lower()
    if "arxiv.org" in u:
        return "paper"
    if "github.com/" in u or "gitlab.com/" in u:
        return "repo"
    return "news"


FIRECRAWL_QUERIES = [
    # LLM 通用官宣
    ("OpenAI", "OpenAI GPT language model reasoning blog 2025 2026", 5),
    ("Anthropic", "Anthropic Claude language model alignment blog", 5),
    ("Google DeepMind", "Google DeepMind Gemini language model research", 4),
    ("Meta Llama", "Meta Llama open language model blog", 4),
    ("Hugging Face", "Hugging Face LLM open weights model release", 4),
    # 推理优化
    ("LLM Inference", "vllm sglang llama.cpp inference optimization LLM blog", 4),
    # RAG
    ("RAG", "retrieval augmented generation RAG LLM 2025 2026", 4),
    # Agent
    ("AI Agents", "LLM agent tool use multi-agent framework 2025 2026", 5),
    # 多模态
    ("Multimodal LLM", "multimodal large language model vision language 2025 2026", 4),
    # 训练
    ("LLM Training", "RLHF DPO instruction tuning fine-tuning LLM 2025 2026", 4),
    # 研究
    ("arXiv LLM", "arxiv LLM agent RAG multimodal training paper 2026", 5),
]


@register_fetcher("firecrawl")
class FirecrawlFetcher(BaseFetcher):
    """通过 Firecrawl 搜索+抓取聚合 AI 资讯"""

    def __init__(self, config=None):
        super().__init__(config or CONFIG.rss)
        self.service = get_firecrawl_service()

    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        if not self.service.config.api_key:
            return FetchResult(items=[], error="FIRECRAWL_API_KEY 未配置，firecrawl-only 模式无法抓取")

        items = []
        errors = []
        seen_links = set()

        for venue, query, limit in FIRECRAWL_QUERIES:
            try:
                results = await self.service.search_and_crawl(query=query, limit=limit, lang="en")
                for row in results:
                    link = (row.get("url") or row.get("sourceURL") or "").strip()
                    if not link or link in seen_links:
                        continue
                    seen_links.add(link)

                    title = (row.get("title") or "").strip()
                    desc = (row.get("description") or row.get("markdown") or row.get("content") or "").strip()
                    desc = desc[:320] + ("…" if len(desc) > 320 else "") if desc else "资讯"

                    tags = infer_tags(f"{title} {desc}")

                    items.append({
                        "type": _infer_item_type(link),
                        "title": title or link,
                        "desc": desc,
                        "tags": tags,
                        "date": "",
                        "venue": venue,
                        "link": link,
                        "heat": 0,
                    })
            except Exception as e:
                errors.append(f"{venue}: {str(e)}")

        outcome = "success"
        if errors and items:
            outcome = "partial"
        elif errors:
            outcome = "error"

        return FetchResult(
            items=items,
            error="; ".join(errors) if errors else None,
            source_status={
                "outcome": outcome,
                "used_cache": False,
                "fetched_count": len(items),
                "failed_sources": len(errors),
                "total_sources": len(FIRECRAWL_QUERIES),
                "fetch_via": "firecrawl",
            },
        )
