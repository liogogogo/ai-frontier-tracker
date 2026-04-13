"""
产品意图：汇总大模型相关前沿论文、实践与社区讨论，辅助挖掘「未来有潜力的 AI 项目与方向」
（早期技术与产品线索）；词频/洞察 API 用于趋势感知。对齐公开渠道上最活跃的工程与产业叙事。
数据含：大厂与云厂商技术博客、HN/Reddit/Lobsters、arXiv 工程向与 GitHub 等。
"""
"""
AI Frontier Tracker - 主入口
可扩展架构：插件化 Fetcher + 数据库存储 + 缓存
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .models import FeedItem
from .services.collector import collector
from .services.cache import cache
from .config import CONFIG
from .utils.feed_insights import build_feed_insights
from .services.weekly_digest import get_or_create_weekly_zh_digest

APP_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)

# 初始化数据库
init_db()

_ANALYTICS = CONFIG.analytics

# 排序元信息
FEED_SORT_META = {
    "primary": "heat",
    "primary_order": "desc",
    "tiebreak": "date",
    "tiebreak_order": "desc",
    "model": (
        "heat = clamp(1..999, 渠道底分 + log 压缩后的互动 + 时效 + 主题匹配)；"
        "GitHub 开源项量化：stars、forks、open issues；其它渠道见 heat_breakdown。"
        "列表保留至少 FEED_MIN_PAPERS 篇论文再补资讯。"
    ),
}

app = FastAPI(
    title="AI Frontier Tracker API",
    version="2.0",
    description="可扩展架构：插件化Fetcher + 数据库存储 + 缓存",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
static_dir = APP_ROOT / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup_event():
    """启动时预热缓存（后台执行，避免阻塞启动）"""

    async def _warmup():
        try:
            items, _ = await collector.collect_all()
            cache.set_feed(items)
        except Exception:
            logger.exception(
                "Startup feed warmup failed; serving stale or empty cache until first refresh"
            )

    # 不阻塞 Uvicorn startup；collect_all 可能受限流/网络影响耗时较长
    import asyncio

    asyncio.create_task(_warmup())


@app.get("/api/health")
def health():
    """健康检查端点"""
    return {
        "ok": True,
        "version": "2.0",
        "schema_version": CONFIG.schema_version,
    }


@app.get("/api/health/detailed")
def health_detailed():
    """详细健康状态"""
    return collector.get_health_status()


@app.get("/api/weekly-digest")
async def weekly_digest_zh():
    """
    基于当前内存 Feed 生成一段中文「本周前沿综述」。
    需配置 OpenAI / Anthropic / Modelverse 任一 API Key；否则返回统计兜底文案。
    """
    items = cache.get_feed() or []
    result = await get_or_create_weekly_zh_digest(items)
    return JSONResponse(
        {
            **result,
            "schema_version": CONFIG.schema_version,
        }
    )


@app.get("/api/feed")
async def get_feed(refresh: bool = False):
    """获取Feed数据"""
    if refresh or not cache.get_feed():
        items, errors = await collector.collect_all()
        fetch_summary = collector.get_last_fetch_summary()
    else:
        items = cache.get_feed()
        errors = []
        fetch_summary = {
            "status": "ok",
            "message": f"已加载缓存中的 {len(items or [])} 条内容",
            "sources": {"total": 0, "success": 0, "unchanged": 0, "failed": 0},
            "cache": {"used": True, "unchanged_fetchers": []},
            "fetchers": {},
        }

    items = items or []
    insights = build_feed_insights(items)

    return JSONResponse({
        "items": items,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
        "fetch_summary": fetch_summary,
        "count": len(items),
        "sort": FEED_SORT_META,
        "cache_size": cache.memory.size,
        "schema_version": CONFIG.schema_version,
        "insights": insights,
    })


@app.post("/api/feed/refresh")
async def post_refresh():
    """强制刷新Feed"""
    items, errors = await collector.collect_all()
    items = items or []
    insights = build_feed_insights(items)

    return {
        "items": items,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "errors": errors,
        "fetch_summary": collector.get_last_fetch_summary(),
        "count": len(items),
        "sort": FEED_SORT_META,
        "schema_version": CONFIG.schema_version,
        "insights": insights,
    }


@app.get("/api/stats")
def get_stats():
    """获取统计信息"""
    from sqlalchemy import func
    from sqlmodel import select

    from .database import get_session
    from .models import CollectionRun, Article
    
    with get_session() as session:
        # 总文章数
        total_articles = session.exec(select(func.count()).select_from(Article)).one()
        
        # 按类型统计
        type_counts = session.exec(
            select(Article.type, func.count()).group_by(Article.type)
        ).all()
        
        # 最近抓取运行
        latest_run = session.exec(
            select(CollectionRun).order_by(CollectionRun.started_at.desc())
        ).first()
        
        return {
            "total_articles": total_articles,
            "by_type": {t: c for t, c in type_counts},
            "latest_run": {
                "started_at": latest_run.started_at.isoformat() if latest_run else None,
                "completed_at": latest_run.completed_at.isoformat() if latest_run and latest_run.completed_at else None,
                "total_items": latest_run.total_items if latest_run else 0,
                "duration_ms": latest_run.duration_ms if latest_run else 0,
            } if latest_run else None,
        }


@app.get("/api/analytics/word-freq")
def get_word_frequency(
    days: int = Query(
        _ANALYTICS.default_word_freq_days,
        ge=1,
        le=_ANALYTICS.max_days,
    ),
    article_type: Optional[str] = Query(
        None, description="paper | news | repo；省略表示全部"
    ),
    top_k: int = Query(50, ge=1, le=_ANALYTICS.max_top_k),
):
    """
    词频分析端点
    
    Args:
        days: 分析最近N天的文章（默认30天）
        article_type: 筛选文章类型 (paper/news/repo)，None表示全部
        top_k: 返回词数上限
    """
    # 查询串里常出现 article_type=（空串），须视为「未筛选」，否则会 422
    if article_type is not None:
        article_type = article_type.strip() or None

    if article_type is not None and article_type not in _ANALYTICS.allowed_article_types:
        raise HTTPException(
            status_code=422,
            detail=(
                "article_type must be one of: "
                + ", ".join(sorted(_ANALYTICS.allowed_article_types))
            ),
        )

    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .utils.text_analysis import analyze_articles, filter_by_time_range
    from datetime import datetime, timedelta
    
    # 构建查询
    with get_session() as session:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        stmt = select(Article).where(Article.date >= cutoff)
        if article_type:
            stmt = stmt.where(Article.type == article_type)
        
        articles_db = session.exec(stmt).all()
        
        # 转换为字典格式
        import json as _json
        articles = [
            {
                "title": a.title,
                "desc": a.desc,
                "type": a.type,
                "date": a.date,
                "tags": (
                    _json.loads(a.tags)
                    if a.tags and a.tags.strip().startswith("[")
                    else [t for t in (a.tags or "").split(",") if t]
                ),
                "heat": a.heat,
                "venue": a.venue,
            }
            for a in articles_db
        ]
    
    # 分析
    result = analyze_articles(articles, top_k=top_k)
    
    return {
        "query": {"days": days, "article_type": article_type, "top_k": top_k},
        "result": result
    }


@app.get("/api/analytics/trending")
def get_trending_words(
    recent_days: int = Query(
        _ANALYTICS.default_trend_recent_days,
        ge=1,
        le=_ANALYTICS.max_trend_recent_days,
    ),
    compare_days: int = Query(
        _ANALYTICS.default_trend_compare_days,
        ge=2,
        le=_ANALYTICS.max_trend_compare_days,
    ),
    top_k: int = Query(20, ge=1, le=_ANALYTICS.max_top_k),
):
    """
    趋势分析端点 - 对比近期和历史的词频变化
    
    Args:
        recent_days: 近期天数（默认7天）
        compare_days: 对比的历史天数（默认30天）
        top_k: 返回趋势词数
    """
    if compare_days <= recent_days:
        compare_days = min(recent_days + 1, _ANALYTICS.max_trend_compare_days)

    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .utils.text_analysis import get_trending_words
    from datetime import datetime, timedelta
    
    with get_session() as session:
        now = datetime.now()
        recent_cutoff = (now - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        historical_cutoff = (now - timedelta(days=compare_days)).strftime("%Y-%m-%d")
        
        # 近期文章
        recent_stmt = select(Article).where(Article.date >= recent_cutoff)
        recent_db = session.exec(recent_stmt).all()
        
        # 历史文章（排除近期）
        hist_stmt = select(Article).where(
            Article.date >= historical_cutoff,
            Article.date < recent_cutoff
        )
        hist_db = session.exec(hist_stmt).all()
        
        # 转换为字典
        import json as _json2
        def _to_dict(a):
            return {
                "title": a.title, "desc": a.desc,
                "type": a.type, "date": a.date, "heat": a.heat,
            }
        recent_articles = [_to_dict(a) for a in recent_db]
        hist_articles = [_to_dict(a) for a in hist_db]
    
    trending = get_trending_words(recent_articles, hist_articles, top_k)
    
    return {
        "query": {"recent_days": recent_days, "compare_days": compare_days, "top_k": top_k},
        "recent_count": len(recent_articles),
        "historical_count": len(hist_articles),
        "trending": trending
    }


@app.get("/api/analytics/topics")
def get_topic_cards(
    recent_days: int = Query(_ANALYTICS.default_trend_recent_days, ge=1, le=_ANALYTICS.max_trend_recent_days),
    compare_days: int = Query(_ANALYTICS.default_trend_compare_days, ge=2, le=_ANALYTICS.max_trend_compare_days),
    top_k: int = Query(12, ge=1, le=50),
    per_type_top_k: int = Query(30, ge=5, le=120),
    evidence_k: int = Query(3, ge=1, le=6),
):
    """
    主题卡片：从 paper / repo / news 三类数据里挖掘“高价值主题”，并回溯证据条目。
    """
    if compare_days <= recent_days:
        compare_days = min(recent_days + 1, _ANALYTICS.max_trend_compare_days)

    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .utils.text_analysis import build_topic_cards
    from datetime import datetime, timedelta

    with get_session() as session:
        now = datetime.now()
        recent_cutoff = (now - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        historical_cutoff = (now - timedelta(days=compare_days)).strftime("%Y-%m-%d")

        recent_stmt = select(Article).where(Article.date >= recent_cutoff)
        hist_stmt = select(Article).where(
            Article.date >= historical_cutoff,
            Article.date < recent_cutoff
        )

        recent_db = session.exec(recent_stmt).all()
        hist_db = session.exec(hist_stmt).all()

        def _to_dict(a):
            return {
                "title": a.title,
                "desc": a.desc,
                "type": a.type,
                "date": a.date,
                "heat": a.heat,
                "link": a.link,
                "venue": a.venue,
            }

        recent_articles = [_to_dict(a) for a in recent_db]
        hist_articles = [_to_dict(a) for a in hist_db]

    cards = build_topic_cards(
        recent_articles=recent_articles,
        historical_articles=hist_articles,
        top_k=top_k,
        per_type_top_k=per_type_top_k,
        evidence_k=evidence_k,
    )

    return {
        "query": {
            "recent_days": recent_days,
            "compare_days": compare_days,
            "top_k": top_k,
            "per_type_top_k": per_type_top_k,
            "evidence_k": evidence_k,
        },
        "recent_count": len(recent_articles),
        "historical_count": len(hist_articles),
        "cards": cards,
    }


@app.get("/api/analytics/industry")
async def get_industry_impact(
    days: int = Query(30, ge=1, le=365),
    min_score: float = Query(3.0, ge=0.0, le=10.0),
    use_llm: bool = Query(False),
    force_rescore: bool = Query(False),
    limit: int = Query(300, ge=10, le=2000),
):
    """
    AI Agent 工程行业影响分布。

    参考 karpathy/jobs 的"可复现评分流水线"：
    - 对指定时间窗内的所有 article 用统一 rubric 打分（规则 or LLM）
    - 增量缓存到 Article.raw_data（中断可续，重跑加 force_rescore=true）
    - 聚合成子领域分布 + 类型分布 + score_distribution + top 20 条目
    """
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .services.industry_scorer import (
        score_articles_batch,
        aggregate_distribution,
        get_cached_score,
        SCORE_FIELD,
    )
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with get_session() as session:
        stmt = select(Article).where(Article.date >= cutoff).limit(limit)
        db_articles = list(session.exec(stmt).all())

        # 打分（增量，未缓存的才跑；force=True 全量重跑）
        llm_svc = None
        if use_llm:
            from .services.llm_summary import get_llm_service
            svc = get_llm_service()
            if svc.config.api_key:
                llm_svc = svc

        scored_count, skipped_count = await score_articles_batch(
            db_articles,
            llm_service=llm_svc,
            force=force_rescore,
            limit=limit,
        )

        # 持久化回 DB
        session.add_all(db_articles)
        session.commit()

        # 组装成 dict 列表（附带评分字段）
        import json as _json_ind
        def _to_scored_dict(a: Article) -> dict:
            sc = get_cached_score(a.raw_data)
            return {
                "title": a.title,
                "link": a.link,
                "type": a.type,
                "date": a.date,
                "venue": a.venue,
                "heat": a.heat,
                "agent_eng_score": sc,
            }

        scored_dicts = [_to_scored_dict(a) for a in db_articles]

    distribution = aggregate_distribution(scored_dicts, min_score=min_score)

    return {
        "query": {
            "days": days,
            "min_score": min_score,
            "use_llm": use_llm,
            "articles_fetched": len(db_articles),
        },
        "scoring": {
            "newly_scored": scored_count,
            "from_cache": skipped_count,
            "method": "llm" if llm_svc else "rule",
        },
        **distribution,
    }


@app.get("/api/analytics/entities")
def get_entities(
    recent_days: int = Query(_ANALYTICS.default_trend_recent_days, ge=1, le=_ANALYTICS.max_trend_recent_days),
    compare_days: int = Query(_ANALYTICS.default_trend_compare_days, ge=2, le=_ANALYTICS.max_trend_compare_days),
    top_k: int = Query(40, ge=1, le=120),
    category: Optional[str] = Query(None, description="model|technique|tool|org|benchmark；省略表示全部"),
):
    """
    实体词频 + 动量聚合。

    对近期与历史文章分别统计命名实体（模型/技术/工具/机构/Benchmark）的出现频次，
    计算 velocity（近期频率/历史频率）与跨源分（paper+repo+news 三角），
    按综合得分排序返回。
    """
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .services.signal_analytics import aggregate_entities, _article_to_dict
    from datetime import datetime, timedelta

    if compare_days <= recent_days:
        compare_days = min(recent_days + 1, _ANALYTICS.max_trend_compare_days)

    if category is not None and category not in {"model", "technique", "tool", "org", "benchmark"}:
        raise HTTPException(status_code=422, detail="category must be one of: model, technique, tool, org, benchmark")

    with get_session() as session:
        now = datetime.now()
        recent_cutoff = (now - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        hist_cutoff   = (now - timedelta(days=compare_days)).strftime("%Y-%m-%d")

        recent_db = session.exec(select(Article).where(Article.date >= recent_cutoff)).all()
        hist_db   = session.exec(select(Article).where(
            Article.date >= hist_cutoff, Article.date < recent_cutoff
        )).all()

        recent_articles = [_article_to_dict(a) for a in recent_db]
        hist_articles   = [_article_to_dict(a) for a in hist_db]

    entities = aggregate_entities(
        recent_articles, hist_articles,
        top_k=top_k,
        category_filter=category or None,
    )

    return {
        "query": {
            "recent_days": recent_days,
            "compare_days": compare_days,
            "top_k": top_k,
            "category": category,
        },
        "recent_count": len(recent_articles),
        "historical_count": len(hist_articles),
        "entities": entities,
    }


@app.get("/api/analytics/convergence")
def get_convergence(
    days: int = Query(30, ge=1, le=180),
    min_source_types: int = Query(2, ge=2, le=3),
    top_k: int = Query(20, ge=1, le=50),
):
    """
    收敛信号卡。

    以 arXiv ID 为 key，把论文（paper）、开源实现（repo）、社区讨论（news）关联起来。
    当同一 arXiv ID 被 ≥ min_source_types 种来源覆盖时说明该工作已形成"论文→实现→讨论"
    的完整链路，是高置信度的工程化信号。
    """
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .services.signal_analytics import build_convergence_cards, _article_to_dict
    from datetime import datetime, timedelta

    with get_session() as session:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        db_articles = session.exec(select(Article).where(Article.date >= cutoff)).all()
        articles = [_article_to_dict(a) for a in db_articles]

    cards = build_convergence_cards(articles, min_source_types=min_source_types, top_k=top_k)

    return {
        "query": {"days": days, "min_source_types": min_source_types, "top_k": top_k},
        "article_count": len(articles),
        "cards": cards,
    }


@app.get("/api/analytics/emergence")
def get_emergence(
    recent_days: int = Query(_ANALYTICS.default_trend_recent_days, ge=1, le=_ANALYTICS.max_trend_recent_days),
    compare_days: int = Query(_ANALYTICS.default_trend_compare_days, ge=2, le=_ANALYTICS.max_trend_compare_days),
    top_k: int = Query(20, ge=1, le=60),
    min_mentions: int = Query(2, ge=1, le=10),
):
    """
    涌现实体检测。

    找出近期首次出现（历史窗口中从未见过）或近期提及加速度 > 2×
    的实体，用于早期信号探测。
    """
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .services.signal_analytics import detect_emergence, _article_to_dict
    from datetime import datetime, timedelta

    if compare_days <= recent_days:
        compare_days = min(recent_days + 1, _ANALYTICS.max_trend_compare_days)

    with get_session() as session:
        now = datetime.now()
        recent_cutoff = (now - timedelta(days=recent_days)).strftime("%Y-%m-%d")
        hist_cutoff   = (now - timedelta(days=compare_days)).strftime("%Y-%m-%d")

        recent_db = session.exec(select(Article).where(Article.date >= recent_cutoff)).all()
        hist_db   = session.exec(select(Article).where(
            Article.date >= hist_cutoff, Article.date < recent_cutoff
        )).all()

        recent_articles = [_article_to_dict(a) for a in recent_db]
        hist_articles   = [_article_to_dict(a) for a in hist_db]

    emerged = detect_emergence(
        recent_articles, hist_articles,
        top_k=top_k,
        min_recent_mentions=min_mentions,
    )

    return {
        "query": {
            "recent_days": recent_days,
            "compare_days": compare_days,
            "top_k": top_k,
            "min_mentions": min_mentions,
        },
        "recent_count": len(recent_articles),
        "historical_count": len(hist_articles),
        "emerged": emerged,
    }


@app.get("/api/analytics/paper-struct")
async def get_paper_struct(
    days: int = Query(30, ge=1, le=365),
    use_llm: bool = Query(False),
    force_reextract: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
):
    """
    论文结构化抽取（可选 LLM）。

    对时间窗内的论文提取 problem/method/key_metric/impl_url/novelty/one_liner，
    结果增量缓存到 Article.raw_data["paper_struct"]。
    无 LLM Key 时返回已有缓存结果（不触发 LLM 调用）。
    """
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .services.paper_extractor import extract_batch, get_cached_struct, STRUCT_FIELD
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    with get_session() as session:
        stmt = (
            select(Article)
            .where(Article.date >= cutoff, Article.type == "paper")
            .limit(limit)
        )
        db_articles = list(session.exec(stmt).all())

        llm_svc = None
        if use_llm:
            from .services.llm_summary import get_llm_service
            svc = get_llm_service()
            if svc.config.api_key:
                llm_svc = svc

        extracted, skipped = await extract_batch(
            db_articles, llm_service=llm_svc,
            force=force_reextract, limit=limit,
        )

        if extracted > 0:
            session.add_all(db_articles)
            session.commit()

        results = []
        for art in db_articles:
            st = get_cached_struct(art.raw_data)
            if st:
                results.append({
                    "title": art.title,
                    "link": art.link,
                    "date": art.date,
                    "heat": art.heat,
                    **st,
                })

    results.sort(key=lambda x: -x.get("heat", 0))

    return {
        "query": {"days": days, "use_llm": use_llm, "limit": limit},
        "extraction": {"newly_extracted": extracted, "from_cache": skipped, "method": "llm" if llm_svc else "none"},
        "papers": results[:50],
    }


@app.post("/api/admin/backfill-entities")
def backfill_entities(
    days: int = Query(90, ge=1, le=365),
    force: bool = Query(False),
):
    """
    补填历史文章的实体抽取结果（admin 接口）。
    对 DB 中尚未有 raw_data['entities'] 的文章运行 extract_entities()，
    并将结果合并写入 raw_data。
    """
    import re as _re
    from sqlmodel import select
    from .database import get_session
    from .models import Article
    from .utils.entity_dict import extract_entities
    from .services.cache import DatabaseCache
    from datetime import datetime, timedelta

    _ARXIV_ID_RE_LOCAL = _re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", _re.IGNORECASE)

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    updated = 0
    skipped = 0

    import json as _j
    with get_session() as session:
        db_articles = session.exec(select(Article).where(Article.date >= cutoff)).all()

        for art in db_articles:
            # 检查是否已有 entities
            if not force:
                try:
                    d = _j.loads(art.raw_data or "{}")
                    if "entities" in d:
                        skipped += 1
                        continue
                except Exception:
                    pass

            text = f"{art.title} {art.title} {art.title} {art.desc or ''}"
            entities = extract_entities(text)

            raw_updates: dict = {"entities": entities}
            m = _ARXIV_ID_RE_LOCAL.search(art.link or "")
            if m:
                raw_updates["arxiv_id"] = m.group(1)

            art.raw_data = DatabaseCache._merge_raw_data(art.raw_data, raw_updates)
            updated += 1

        session.add_all(db_articles)
        session.commit()

    return {"updated": updated, "skipped": skipped, "days": days}


@app.post("/api/enhance")
async def enhance_article(
    url: str,
    title: str = "",
    desc: str = ""
):
    """
    使用 Firecrawl 增强文章内容
    
    智能抓取网页并提取主要内容，输出 LLM-ready 的 Markdown 格式
    
    Args:
        url: 文章链接
        title: 现有标题（可选）
        desc: 现有描述（可选）
        
    Returns:
        增强后的内容 {"desc": "...", "tags": [...], "full_content": "...", "source": "firecrawl|fallback"}
    """
    from .services.firecrawl import get_firecrawl_service
    
    service = get_firecrawl_service()
    result = await service.enhance_article(title, url, desc)
    
    # 标记来源
    if service.config.api_key:
        result["source"] = "firecrawl" if result.get("full_content") else "fallback"
    else:
        result["source"] = "fallback (no API key)"
        result["note"] = "设置 FIRECRAWL_API_KEY 环境变量以启用智能抓取"
    
    return {
        "url": url,
        "enhanced": result
    }


@app.get("/api/scrape")
async def scrape_url(
    url: str,
    only_main_content: bool = True
):
    """
    直接抓取任意 URL（使用 Firecrawl）
    
    Args:
        url: 目标 URL
        only_main_content: 是否只提取主要内容
        
    Returns:
        Firecrawl 抓取结果
    """
    from .services.firecrawl import get_firecrawl_service
    
    service = get_firecrawl_service()
    result = await service.scrape(url, only_main_content=only_main_content, formats=["markdown", "text"])
    
    if result:
        return {
            "success": True,
            "url": url,
            "title": result.get("metadata", {}).get("title"),
            "description": result.get("metadata", {}).get("description"),
            "markdown": result.get("markdown", "")[:2000],  # 截断
            "links": len(result.get("links", [])),
        }
    else:
        return {
            "success": False,
            "url": url,
            "error": "抓取失败或未配置 Firecrawl API Key"
        }


@app.post("/api/summarize")
async def generate_summary(
    title: str,
    content: str = "",
    max_length: int = 200
):
    """
    使用 LLM 生成文章摘要
    
    支持 OpenAI 和 Claude API
    
    Args:
        title: 文章标题
        content: 文章内容
        max_length: 摘要最大长度
        
    Returns:
        生成的摘要
    """
    from .services.llm_summary import get_llm_service
    
    service = get_llm_service()
    
    if not service.config.api_key:
        return {
            "success": False,
            "error": "未配置 LLM API Key",
            "note": "设置 OPENAI_API_KEY 或 ANTHROPIC_API_KEY 环境变量"
        }
    
    summary = await service.generate_summary(title, content, max_length)
    
    if summary:
        return {
            "success": True,
            "title": title,
            "summary": summary,
            "provider": service.config.provider.value,
            "model": service.config.model,
        }
    else:
        return {
            "success": False,
            "error": "摘要生成失败"
        }


@app.get("/")
def index():
    """首页"""
    index_path = APP_ROOT / "static" / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="static/index.html missing")
    return FileResponse(index_path)



