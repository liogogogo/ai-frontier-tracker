"""
数据收集协调器 - 统一管理所有抓取器
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Set

from sqlmodel import select

from ..fetchers import FetcherRegistry, FetchResult
from ..utils.heat import finalize_heat
from ..utils.dedup_urls import canonicalize_link
from ..utils.bloom import get_deduplicator
from ..utils.entity_dict import extract_entities
from ..services.cache import cache, DatabaseCache
from ..services.scheduler import get_scheduler
from ..database import get_session
from ..models import Article
from ..config import CONFIG

# arXiv ID 提取正则（匹配 abs 或 pdf 路径中的 YYMM.NNNNN 格式）
_ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.IGNORECASE)

logger = logging.getLogger(__name__)


class CollectorService:
    """数据收集服务 - 集成 Bloom Filter + 自适应调度器"""

    def __init__(self):
        self.registry = FetcherRegistry
        self.db_cache = DatabaseCache()
        self.dedup = get_deduplicator()  # Bloom Filter 去重器
        self.scheduler = get_scheduler()  # 自适应调度器
        self._initialized = False
        self._last_fetch_summary: Dict[str, Any] = {
            "status": "unknown",
            "message": "尚未执行抓取",
            "sources": {"total": 0, "success": 0, "unchanged": 0, "failed": 0},
            "fetchers": {},
        }

    def _init_dedup_from_db(self):
        """从数据库初始化 Bloom Filter（启动时一次性）"""
        if self._initialized:
            return

        try:
            with get_session() as session:
                # 加载最近 7 天的文章链接到过滤器
                from datetime import timedelta
                cutoff = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
                stmt = select(Article.link).where(Article.date >= cutoff)
                links = [
                    canonicalize_link(row[0])
                    for row in session.exec(stmt).all()
                    if row[0]
                ]

                # 批量加入 Bloom Filter
                self.dedup.add_batch(links)

        except Exception:
            logger.exception(
                "Bloom filter seed from DB failed; dedup runs without recent link history"
            )

        self._initialized = True

    async def collect_all(self) -> Tuple[List[Dict], List[str]]:
        """
        并行收集所有数据源（集成 Bloom Filter + 自适应调度）

        Returns:
            (items, errors): 抓取到的数据和错误信息
        """
        start_time = datetime.utcnow()

        # 初始化 Bloom Filter
        self._init_dedup_from_db()

        fetchers = self.registry.get_all(enabled_only=True)
        fetcher_names = list(fetchers.keys())

        # 使用自适应调度器执行抓取
        async def fetch_one(name: str) -> FetchResult:
            fetcher = fetchers.get(name)
            if not fetcher:
                return FetchResult(items=[], error="Fetcher not found")
            return await self._fetch_with_error_handling(fetcher)

        # 按调度顺序执行
        results = await self.scheduler.run_with_scheduling(fetch_one, fetcher_names)

        # 合并结果
        all_items = []
        errors = []
        fetcher_summaries = {}

        for name, result in results.items():
            if isinstance(result, Exception):
                errors.append(f"{name}: {str(result)}")
                fetcher_summaries[name] = {
                    "outcome": "error",
                    "used_cache": False,
                    "fetched_count": 0,
                    "status": str(result),
                }
                continue

            if not isinstance(result, FetchResult):
                continue

            all_items.extend(result.items)
            fetcher_summaries[name] = self._build_fetcher_summary(name, result)
            if result.error:
                errors.append(f"{name}: {result.error}")

        self._last_fetch_summary = self._build_fetch_summary(fetcher_names, fetcher_summaries, len(all_items))

        # URL 规范化后再算热度与去重（避免 abs/pdf、版本号、追踪参数导致重复）
        for item in all_items:
            lk = item.get("link") or ""
            if lk:
                item["link"] = canonicalize_link(lk)

        # 计算热度
        for item in all_items:
            finalize_heat(item)

        # 实体抽取 + arXiv ID 标注
        for item in all_items:
            text = f"{item.get('title', '')} {item.get('title', '')} {item.get('title', '')} {item.get('desc', '')}"
            item["entities"] = extract_entities(text)
            m = _ARXIV_ID_RE.search(item.get("link", ""))
            if m:
                item["arxiv_id"] = m.group(1)

        # 去重 - 按热度保留
        by_link = self._deduplicate(all_items)
        items = list(by_link.values())

        paper_min = 0 if CONFIG.firecrawl_only else min(CONFIG.feed_min_papers, CONFIG.feed_max_items // 2)
        if paper_min > 0 and sum(1 for it in items if it.get("type") == "paper") < paper_min:
            exclude: Set[str] = {it.get("link") or "" for it in items}
            exclude.discard("")
            for it in self._papers_from_database(
                exclude, limit=max(paper_min * 4, 28)
            ):
                finalize_heat(it)
                if "entities" not in it:
                    text = f"{it.get('title', '')} {it.get('title', '')} {it.get('title', '')} {it.get('desc', '')}"
                    it["entities"] = extract_entities(text)
                if "arxiv_id" not in it:
                    m = _ARXIV_ID_RE.search(it.get("link", ""))
                    if m:
                        it["arxiv_id"] = m.group(1)
                lk = it.get("link") or ""
                if lk and lk not in by_link:
                    by_link[lk] = it
            items = list(by_link.values())

        # Firecrawl 增强（可选）
        await self._enhance_with_firecrawl(items)

        # LLM 自动生成摘要（可选）
        await self._generate_llm_summaries(items)

        # 排序
        items.sort(
            key=lambda x: (int(x.get("heat") or 0), x.get("date") or ""),
            reverse=True
        )

        # 保证论文数量
        items = self._apply_paper_floor(items)

        # 清理临时字段
        self._cleanup_temp_fields(items)

        # 持久化到数据库
        self._persist_items(items)

        # 更新缓存
        cache.set_feed(items)

        # 记录运行统计
        duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        self._record_run(duration_ms, len(items), errors)

        return items, errors

    async def _fetch_with_error_handling(self, fetcher) -> FetchResult:
        """包装抓取器调用，捕获异常"""
        try:
            return await fetcher.fetch_with_state()
        except Exception as e:
            return FetchResult(items=[], error=str(e))

    def _build_fetcher_summary(self, name: str, result: FetchResult) -> Dict[str, Any]:
        """构建单个 fetcher 的结构化摘要"""
        summary = {
            "outcome": "error" if result.error else "success",
            "used_cache": False,
            "fetched_count": len(result.items),
            "status": result.status,
        }
        if result.source_status:
            summary.update(result.source_status)
        if result.error:
            summary["error"] = result.error
        return summary

    def _build_fetch_summary(self, fetcher_names: List[str], fetcher_summaries: Dict[str, Dict[str, Any]], total_items: int) -> Dict[str, Any]:
        """构建整体抓取摘要"""
        failed = 0
        unchanged = 0
        success = 0
        used_cache = False
        unchanged_fetchers = []

        for name in fetcher_names:
            info = fetcher_summaries.get(name, {})
            outcome = info.get("outcome") or "success"
            if outcome == "error":
                failed += 1
            elif outcome == "unchanged":
                unchanged += 1
                success += 1
            else:
                success += 1

            if info.get("used_cache"):
                used_cache = True
            if info.get("not_modified") or outcome == "unchanged":
                unchanged_fetchers.append(name)

        if failed and success:
            status = "partial"
        elif failed:
            status = "degraded"
        else:
            status = "ok"

        message_parts = [f"已更新 {total_items} 条"]
        if unchanged:
            if used_cache:
                message_parts.append(f"{unchanged} 个来源未变化并复用缓存")
            else:
                message_parts.append(f"{unchanged} 个来源未变化")
        if failed:
            message_parts.append(f"{failed} 个来源失败")
        elif not unchanged:
            message_parts.append("所有来源正常")

        return {
            "status": status,
            "message": "，".join(message_parts),
            "sources": {
                "total": len(fetcher_names),
                "success": success,
                "unchanged": unchanged,
                "failed": failed,
            },
            "cache": {
                "used": used_cache,
                "unchanged_fetchers": unchanged_fetchers,
            },
            "fetchers": fetcher_summaries,
        }

    def get_last_fetch_summary(self) -> Dict[str, Any]:
        """获取最近一次抓取摘要"""
        return self._last_fetch_summary

    def _papers_from_database(self, exclude_links: Set[str], limit: int) -> List[Dict]:
        """arXiv 限流等导致本轮无论文时，用库中近期论文兜底"""
        if limit <= 0:
            return []
        try:
            with get_session() as session:
                stmt = (
                    select(Article)
                    # 仅用真正论文源做兜底：限定 arXiv 链接，避免机构新闻被历史误标为 paper
                    .where(Article.type == "paper", Article.link.contains("arxiv.org"))
                    .order_by(Article.date.desc(), Article.id.desc())
                    .limit(limit)
                )
                rows = list(session.exec(stmt).all())
                out: List[Dict] = []
                exclude_c = {canonicalize_link(x) for x in exclude_links if x}
                for r in rows:
                    if canonicalize_link(r.link) in exclude_c:
                        continue
                    try:
                        tags = json.loads(r.tags or "[]")
                    except (json.JSONDecodeError, TypeError):
                        tags = []
                    cl = canonicalize_link(r.link)
                    out.append({
                        "type": "paper",
                        "title": r.title,
                        "desc": r.desc,
                        "tags": tags if isinstance(tags, list) else [],
                        "date": r.date,
                        "venue": r.venue or "arXiv",
                        "link": cl,
                        "heat": r.heat or 0,
                    })
                return out
        except Exception:
            return []

    def _deduplicate(self, items: List[Dict]) -> Dict[str, Dict]:
        """按链接去重，保留热度更高者"""
        by_link = {}

        for item in items:
            link = item.get("link") or ""
            if not link:
                continue

            prev = by_link.get(link)
            if not prev:
                by_link[link] = item
                continue

            h_new = int(item.get("heat") or 0)
            h_old = int(prev.get("heat") or 0)
            d_new = item.get("date") or ""
            d_old = prev.get("date") or ""

            if h_new > h_old or (h_new == h_old and d_new > d_old):
                by_link[link] = item

        return by_link

    def _apply_paper_floor(self, items: List[Dict]) -> List[Dict]:
        """保证列表中至少有指定数量的论文"""
        paper_min = min(CONFIG.feed_min_papers, CONFIG.feed_max_items // 2)
        max_n = CONFIG.feed_max_items

        # 分离论文和其他内容
        papers = [it for it in items if it.get("type") == "paper"]
        others = [it for it in items if it.get("type") != "paper"]

        result = []
        seen = set()

        # 先填充非论文内容，保留空间给论文
        reserve = max(0, max_n - paper_min)
        for it in others:
            if len(result) >= reserve:
                break
            if it["link"] not in seen:
                result.append(it)
                seen.add(it["link"])

        # 填充论文
        for it in papers:
            if len(result) >= max_n:
                break
            if it["link"] not in seen:
                result.append(it)
                seen.add(it["link"])

        # 填充剩余
        for it in items:
            if len(result) >= max_n:
                break
            if it["link"] not in seen:
                result.append(it)
                seen.add(it["link"])

        # 重新排序
        result.sort(
            key=lambda x: (int(x.get("heat") or 0), x.get("date") or ""),
            reverse=True
        )

        return result[:max_n]

    def _cleanup_temp_fields(self, items: List[Dict]):
        """清理临时字段（entities/arxiv_id 保留至 _persist_items 写库后再移除）"""
        temp_fields = (
            "_hn_points", "_hn_comments",
            "_reddit_score", "_reddit_comments",
            "_lob_score", "_lob_comments",
            "_gh_stars",
            "_gh_forks",
            "_gh_open_issues",
        )

        for item in items:
            for field in temp_fields:
                item.pop(field, None)

    def _persist_items(self, items: List[Dict]):
        """持久化到数据库，然后移除不对外暴露的分析字段"""
        _internal_fields = ("entities", "arxiv_id", "heat_breakdown")
        for item in items:
            try:
                self.db_cache.save_or_update_article(item)
            except Exception:
                pass
            for f in _internal_fields:
                item.pop(f, None)

    def _record_run(self, duration_ms: int, total_items: int, errors: List[str]):
        """记录运行统计"""
        from ..database import get_session
        from ..models import CollectionRun
        import json

        try:
            with get_session() as session:
                run = CollectionRun(
                    completed_at=datetime.utcnow(),
                    total_items=total_items,
                    new_items=total_items,  # 简化处理
                    duration_ms=duration_ms,
                    errors=json.dumps(errors),
                )
                session.add(run)
        except Exception:
            pass

    async def _enhance_with_firecrawl(self, items: List[Dict[str, Any]]) -> None:
        """
        使用增强服务增强文章内容（Crawl4AI 免费兜底 + Firecrawl 可选）

        对描述太短或需要详细内容的文章进行智能抓取
        """
        # 检查 Firecrawl 是否可用
        from .firecrawl import get_firecrawl_service
        service = get_firecrawl_service()

        if not service.is_enabled():
            return  # provider=firecrawl 且无 key 时禁用；其它 provider 始终尝试

        # 选择需要增强的文章（描述太短或热度较高）
        to_enhance = []
        for item in items:
            desc = item.get("desc", "")
            heat = item.get("heat", 0)
            # 条件：描述少于100字符 或 热度超过50
            if len(desc) < 100 or heat > 50:
                to_enhance.append(item)

        # 限制增强数量，避免 API 配额耗尽
        max_enhance = min(10, len(to_enhance))
        to_enhance = to_enhance[:max_enhance]

        if not to_enhance:
            return

        # 并发增强，但限制并发数
        semaphore = asyncio.Semaphore(3)

        async def enhance_one(item: Dict[str, Any]):
            async with semaphore:
                try:
                    result = await service.enhance_article(
                        title=item.get("title", ""),
                        link=item.get("link", ""),
                        existing_desc=item.get("desc", ""),
                    )

                    # 更新文章内容
                    if result.get("desc") and len(result["desc"]) > len(item.get("desc", "")):
                        item["desc"] = result["desc"]
                    if result.get("tags"):
                        # 合并标签，去重
                        existing_tags = set(item.get("tags", []))
                        new_tags = set(result["tags"])
                        item["tags"] = list(existing_tags | new_tags)
                    if result.get("full_content"):
                        item["full_content"] = result["full_content"]

                except Exception:
                    # 增强失败不影响主流程
                    pass

        # 执行增强
        await asyncio.gather(*[enhance_one(item) for item in to_enhance], return_exceptions=True)

    async def _generate_llm_summaries(self, items: List[Dict[str, Any]]) -> None:
        """
        使用 LLM 为文章自动生成高质量摘要

        优先为论文和高热度文章生成摘要
        """
        # 检查 LLM 服务是否可用
        from .llm_summary import get_llm_service
        service = get_llm_service()

        if not service.config.api_key:
            return  # 未配置 API key，跳过摘要生成

        # 选择需要生成摘要的文章（论文优先，然后是高热度文章）
        to_summarize = []
        for item in items:
            item_type = item.get("type", "")
            heat = item.get("heat", 0)
            existing_desc = item.get("desc", "")

            # 条件：论文类型 或 热度超过40 或 描述少于150字符
            if item_type == "paper" or heat > 40 or len(existing_desc) < 150:
                to_summarize.append(item)

        # 限制数量，避免 API 配额耗尽
        max_summaries = min(5, len(to_summarize))
        to_summarize = to_summarize[:max_summaries]

        if not to_summarize:
            return

        # 并发生成，但限制并发数
        semaphore = asyncio.Semaphore(2)

        async def summarize_one(item: Dict[str, Any]):
            async with semaphore:
                try:
                    # 准备内容
                    title = item.get("title", "")
                    content = item.get("full_content", item.get("desc", ""))
                    existing_desc = item.get("desc", "")

                    result = await service.generate_summary_for_article(
                        title=title,
                        content=content,
                        existing_desc=existing_desc,
                    )

                    if result.get("success"):
                        # 更新文章摘要
                        summary = result.get("summary", "")
                        if summary and len(summary) > 50:
                            item["desc"] = summary
                            item["llm_enhanced"] = True

                        # 更新标签
                        llm_tags = result.get("tags", [])
                        if llm_tags:
                            existing_tags = set(item.get("tags", []))
                            new_tags = set(llm_tags)
                            item["tags"] = list(existing_tags | new_tags)

                except Exception:
                    # 摘要生成失败不影响主流程
                    pass

        # 执行摘要生成
        await asyncio.gather(*[summarize_one(item) for item in to_summarize], return_exceptions=True)

    def get_health_status(self) -> Dict[str, Any]:
        """获取所有抓取器的健康状态（含 Bloom Filter 统计）"""
        fetchers = self.registry.get_all(enabled_only=True)

        health = {}
        for name, fetcher in fetchers.items():
            stats = fetcher.get_stats()
            health[name] = {
                "last_success": stats.last_success.isoformat() if stats.last_success else None,
                "last_error": getattr(stats, "last_error", None),
                "total_fetches": stats.total_fetches,
                "total_items": stats.total_items,
                "error_count": stats.error_count,
                "avg_duration_ms": round(stats.avg_duration_ms, 1),
                "not_modified_count": getattr(stats, "not_modified_count", 0),
                "cache_stats": getattr(stats, "cache_stats", None),
            }

        # 添加 Bloom Filter 统计
        bloom_stats = self.dedup.stats()

        # 添加调度器统计
        scheduler_stats = self.scheduler.get_stats()
        recommendations = self.scheduler.get_recommendations()

        return {
            "fetchers": health,
            "last_fetch_summary": self._last_fetch_summary,
            "deduplication": {
                "bloom_filter": bloom_stats,
                "initialized": self._initialized,
            },
            "scheduling": {
                "metrics": scheduler_stats,
                "recommendations": recommendations,
            }
        }


# 全局实例
collector = CollectorService()
