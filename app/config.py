"""
AI Frontier Tracker - 可扩展架构配置
"""
import os
from dataclasses import dataclass, field
from typing import FrozenSet, Optional


def _bounded_int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw.strip())))
    except ValueError:
        return default


@dataclass
class FetcherConfig:
    """单个抓取器配置"""
    enabled: bool = True
    timeout: float = 25.0
    max_retries: int = 3
    retry_delay: float = 1.0
    rate_limit_delay: float = 0.35
    max_items: int = 10


@dataclass
class CacheConfig:
    """内存 Feed 缓存：过期后 GET /api/feed 会重新 collect（不点「刷新」也会更新）。"""
    ttl_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "FEED_CACHE_TTL_SECONDS",
            1800,
            120,
            86400,
        )
    )  # 默认 30 分钟；AI 领域较合适（约 15–60 分钟档）。可用环境变量覆盖。
    max_items: int = 1000


@dataclass
class DatabaseConfig:
    """数据库配置"""
    url: str = "sqlite:///./data/ai_news.db"
    echo: bool = False


@dataclass
class AnalyticsApiConfig:
    """词频/趋势 API 参数上限，防止单次请求拉全库导致内存与延迟失控。"""
    max_days: int = 365
    max_top_k: int = 200
    max_trend_recent_days: int = 90
    max_trend_compare_days: int = 730
    # 与 GET /api/analytics/* 默认查询一致：7d 风向 vs 30d 主题基线；词频默认 30 天
    default_word_freq_days: int = 30
    default_trend_recent_days: int = 7
    default_trend_compare_days: int = 30
    allowed_article_types: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"paper", "news", "repo"})
    )


@dataclass
class AppConfig:
    """应用全局配置"""
    feed_max_items: int = 95
    feed_min_papers: int = 28
    schema_version: int = 14
    # False：并行跑 arXiv / GitHub / RSS / 社区等，论文与开源有稳定来源。
    # True：仅 Firecrawl 搜索聚合（条目多为 news；需 FIRECRAWL_API_KEY）。
    firecrawl_only: bool = False
    
    # 各抓取器配置（arXiv：官方建议约每 ≥3s 一次 API 请求；多主题顺序拉取须严格限流）
    arxiv: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=40.0,
        max_items=60,  # 6 个主题 × 10~14 条，合并去重后保留上限
        max_retries=8,
        retry_delay=3.0,
        rate_limit_delay=3.25,
    ))
    rss: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=30.0,
        max_items=8,
        max_retries=5,
        retry_delay=1.5,
        rate_limit_delay=0.45,
    ))
    github: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=25.0, max_items=4, rate_limit_delay=0.35
    ))
    hackernews: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=22.0, max_items=8
    ))
    reddit: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=22.0, max_items=6
    ))
    lobsters: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=18.0, max_items=8
    ))
    huggingface: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=25.0, max_items=50, max_retries=4, retry_delay=1.5
    ))
    
    # 全局配置
    cache: CacheConfig = field(default_factory=CacheConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    analytics: AnalyticsApiConfig = field(default_factory=AnalyticsApiConfig)


# 全局配置实例
CONFIG = AppConfig()
