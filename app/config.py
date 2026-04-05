"""
AI Frontier Tracker - 可扩展架构配置
"""
from dataclasses import dataclass, field
from typing import FrozenSet, Optional


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
    """缓存配置"""
    ttl_seconds: int = 300  # 5分钟默认缓存
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
    allowed_article_types: FrozenSet[str] = field(
        default_factory=lambda: frozenset({"paper", "news", "repo"})
    )


@dataclass
class AppConfig:
    """应用全局配置"""
    feed_max_items: int = 95
    feed_min_papers: int = 14
    schema_version: int = 14
    
    # 各抓取器配置（arXiv：单次宽查询 + 更多重试，减轻 429）
    arxiv: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=40.0,
        max_items=28,
        max_retries=6,
        retry_delay=2.5,
        rate_limit_delay=0.55,
    ))
    rss: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=25.0, max_items=8, rate_limit_delay=0.35
    ))
    github: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=25.0, max_items=4
    ))
    hackernews: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=22.0, max_items=14
    ))
    reddit: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=22.0, max_items=12
    ))
    lobsters: FetcherConfig = field(default_factory=lambda: FetcherConfig(
        timeout=18.0, max_items=14
    ))
    
    # 全局配置
    cache: CacheConfig = field(default_factory=CacheConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    analytics: AnalyticsApiConfig = field(default_factory=AnalyticsApiConfig)


# 全局配置实例
CONFIG = AppConfig()
