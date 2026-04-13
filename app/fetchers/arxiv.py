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
        self._cached_items: List[Dict[str, Any]] = []          # 合并后缓存
        self._cached_items_by_topic: Dict[str, List[Dict[str, Any]]] = {}  # 按主题缓存

    # 按主题分组的 arXiv 查询：每组独立召回，合并去重，保证六个方向均有论文。
    # 每组 (label, search_query, per_query_n)
    TOPIC_QUERIES: List[tuple] = [
        (
            "LLM",
            "(cat:cs.CL OR cat:cs.AI OR cat:cs.LG) AND "
            "(all:LLM OR all:LLMs OR all:\"large language model\" OR "
            "all:\"foundation model\" OR all:\"language model\" OR "
            "all:\"pretrain\" OR all:GPT OR all:\"reasoning\" OR "
            "all:\"chain of thought\" OR all:\"chain-of-thought\" OR "
            "all:\"long context\" OR all:\"context window\" OR "
            "all:\"mixture of expert\" OR all:MoE)",
            14,
        ),
        (
            "推理优化",
            "(cat:cs.LG OR cat:cs.AI OR cat:cs.AR) AND "
            "(all:\"inference\" AND (all:LLM OR all:\"language model\" OR all:transformer)) OR "
            "(all:\"speculative decoding\" OR all:\"kv cache\" OR all:\"kv-cache\" OR "
            "all:\"continuous batching\" OR all:\"paged attention\" OR "
            "all:\"flash attention\" OR all:quantization OR all:\"model compression\" OR "
            "all:\"weight quantiz\" OR all:\"int4\" OR all:\"int8\" OR "
            "all:\"kernel optim\" OR all:\"serving\" AND all:LLM)",
            10,
        ),
        (
            "RAG",
            "(cat:cs.CL OR cat:cs.IR OR cat:cs.AI) AND "
            "(all:RAG OR all:\"retrieval augmented\" OR all:\"retrieval-augmented\" OR "
            "all:\"retrieval augmentation\" OR all:\"dense retrieval\" OR "
            "all:\"knowledge retrieval\" OR all:\"document retrieval\" AND all:\"language model\" OR "
            "all:\"hybrid search\" OR all:rerank OR all:\"vector database\" OR "
            "all:embedding AND all:\"language model\")",
            10,
        ),
        (
            "Agent",
            "(cat:cs.CL OR cat:cs.AI OR cat:cs.LG) AND "
            "(all:agent OR all:agents OR all:agentic OR all:\"language agent\" OR "
            "all:\"autonomous agent\" OR all:\"llm agent\" OR "
            "all:\"tool use\" OR all:\"tool-use\" OR all:\"tool calling\" OR "
            "all:\"function calling\" OR all:\"multi-agent\" OR all:\"multi agent\" OR "
            "all:\"code generation\" AND all:agent OR "
            "all:\"web agent\" OR all:\"gui agent\" OR all:AutoGPT OR "
            "all:\"planning\" AND (all:LLM OR all:\"language model\"))",
            10,
        ),
        (
            "多模态",
            "(cat:cs.CV OR cat:cs.CL OR cat:cs.AI) AND "
            "(all:\"multimodal\" OR all:\"multi-modal\" OR "
            "all:\"vision language\" OR all:\"visual language\" OR "
            "all:VLM OR all:\"vision-language model\" OR "
            "all:\"image-text\" OR all:\"text-to-image\" OR "
            "all:\"text-to-video\" OR all:\"video understanding\" OR "
            "all:\"visual question\" OR all:\"vision transformer\" AND all:\"language model\" OR "
            "all:\"multimodal LLM\" OR all:MLLM OR all:\"omni model\")",
            10,
        ),
        (
            "训练",
            "(cat:cs.LG OR cat:cs.CL OR cat:cs.AI) AND "
            "(all:RLHF OR all:\"reinforcement learning from human\" OR "
            "all:\"preference optim\" OR all:DPO OR all:\"PPO\" AND all:\"language model\" OR "
            "all:\"supervised fine-tun\" OR all:SFT OR "
            "all:\"instruction tun\" OR all:instruct OR "
            "all:alignment OR all:\"human feedback\" OR "
            "all:LoRA OR all:QLoRA OR all:\"parameter efficient\" OR "
            "all:\"continual learning\" AND all:LLM OR "
            "all:\"distributed training\" AND all:\"language model\")",
            10,
        ),
    ]

    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """多主题顺序查询，六个方向各自召回后合并去重（遵守 arXiv API 访问频率）"""

        async with RetryableHTTPClient(
            base_headers={
                "User-Agent": "AI-Frontier-Tracker/2 (+https://arxiv.org/help/api; scholarly aggregator)",
            },
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=self.config.rate_limit_delay,
            cache_condition=True,
        ) as client:
            # 顺序执行（arXiv 要求友好访问，不做并发）
            all_results: List[Dict[str, Any]] = []
            errors: List[str] = []
            used_any_cache = False

            # 请求间隔由 RetryableHTTPClient.rate_limit_delay 保证（≥3s/次），此处不再额外 sleep
            for label, query, n in self.TOPIC_QUERIES:
                try:
                    items = await self._fetch_one(client, query, n, cache_key=label)
                    all_results.extend(items)
                except NotModifiedError:
                    self._not_modified_count += 1
                    used_any_cache = True
                    all_results.extend(self._cached_items_by_topic.get(label, []))
                except Exception as e:
                    errors.append(f"{label}: {e}")

            # 按 link 去重，保留首次出现（各主题已按时间降序）
            by_link: Dict[str, Dict[str, Any]] = {}
            for item in all_results:
                lk = item.get("link", "")
                if lk and lk not in by_link:
                    by_link[lk] = item

            items = list(by_link.values())
            # 全局按日期降序
            items.sort(key=lambda x: x.get("date") or "", reverse=True)
            # 遵守 max_items 上限
            cap = int(self.config.max_items or 50)
            items = items[:cap]

            # 缓存供下轮 304 使用
            self._cached_items = items

            outcome = "success"
            if errors and items:
                outcome = "partial"
            elif errors and not items:
                outcome = "error"
            elif used_any_cache:
                outcome = "unchanged"

            status_parts = []
            if used_any_cache:
                status_parts.append("部分主题命中304缓存")
            if errors:
                status_parts.append(f"{len(errors)} 个主题失败: {'; '.join(errors[:2])}")

            return FetchResult(
                items=items,
                error="; ".join(errors) if errors else None,
                status=f"[arXiv {', '.join(status_parts)}]" if status_parts else None,
                source_status={
                    "outcome": outcome,
                    "used_cache": used_any_cache,
                    "fetched_count": len(items),
                    "topics": len(self.TOPIC_QUERIES),
                    "errors": len(errors),
                },
                cursor=None,
            )

    async def _fetch_one(
        self,
        client: RetryableHTTPClient,
        query: str,
        max_results: int,
        cache_key: str = "",
    ) -> List[Dict[str, Any]]:
        """单次arXiv查询（支持按主题 ETag 缓存）"""
        # 每个主题用独立的 cache_key 区分 ETag
        ck = f"{self.base_url}#{cache_key}" if cache_key else self.base_url
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        etag_headers = self.etag_cache.get_headers(ck)

        try:
            response = await client.get(self.base_url, params=params, etag_headers=etag_headers)

            self.etag_cache.update(
                ck,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )

            items = self._parse_entries(response.text)
            # 按主题存入缓存
            if cache_key:
                self._cached_items_by_topic[cache_key] = items
            return items

        except NotModifiedError:
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
