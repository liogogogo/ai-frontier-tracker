"""
Fetchers 模块初始化 - 自动导入所有抓取器
"""
from .base import BaseFetcher, FetchResult, FetcherStats
from .registry import FetcherRegistry, register_fetcher

# 导入所有抓取器以触发注册
from .arxiv import ArxivFetcher
from .rss import RSSFetcher
from .github import GitHubFetcher
from .hackernews import HackerNewsFetcher
from .reddit import RedditFetcher
from .lobsters import LobstersFetcher

__all__ = [
    "BaseFetcher",
    "FetchResult", 
    "FetcherStats",
    "FetcherRegistry",
    "register_fetcher",
]
