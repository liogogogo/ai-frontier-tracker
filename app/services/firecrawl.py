"""
内容增强服务（Firecrawl 可选 + Crawl4AI 免费兜底）

为什么要做双通道：
- Firecrawl：托管式抓取，稳定，但有额度/付费限制
- Crawl4AI：本地免费（Playwright 驱动），可控，但更依赖运行环境

本模块对外保持原有接口：
  - scrape()
  - batch_scrape()
  - enhance_article()
  - get_firecrawl_service()

运行策略：
  - 默认 provider=auto：有 FIRECRAWL_API_KEY → 走 Firecrawl；否则尝试 Crawl4AI；都不可用则返回 fallback
  - provider 可通过环境变量 ENHANCE_PROVIDER 强制：firecrawl | crawl4ai | auto
"""
import asyncio
import os
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx


@dataclass
class FirecrawlConfig:
    """增强服务配置（兼容 Firecrawl + Crawl4AI）"""
    api_key: str = ""
    base_url: str = "https://api.firecrawl.dev/v1"
    timeout: float = 30.0
    provider: str = "auto"  # firecrawl | crawl4ai | auto
    
    def __post_init__(self):
        if not self.api_key:
            self.api_key = os.getenv("FIRECRAWL_API_KEY", "")
        self.provider = (os.getenv("ENHANCE_PROVIDER", self.provider) or "auto").strip().lower()


class FirecrawlService:
    """
    增强服务封装（Firecrawl + Crawl4AI）
    
    提供智能内容提取功能，替代传统的 HTML 清洗逻辑
    """
    
    def __init__(self, config: Optional[FirecrawlConfig] = None):
        self.config = config or FirecrawlConfig()
        self._client: Optional[httpx.AsyncClient] = None

    def _provider_order(self) -> List[str]:
        p = (self.config.provider or "auto").strip().lower()
        if p in ("firecrawl", "crawl4ai"):
            return [p]
        # auto
        # 有 key 时先 firecrawl，否则先 crawl4ai
        if self.config.api_key:
            return ["firecrawl", "crawl4ai"]
        return ["crawl4ai", "firecrawl"]

    def is_enabled(self) -> bool:
        """是否启用增强（auto/crawl4ai 总是尝试；firecrawl 需要 key）"""
        p = (self.config.provider or "auto").strip().lower()
        if p == "firecrawl":
            return bool(self.config.api_key)
        return True
    
    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._client

    async def _crawl4ai_scrape(
        self,
        url: str,
        only_main_content: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        使用 Crawl4AI 抓取页面并返回与 Firecrawl scrape() 类似的 dict。
        兼容性说明：Crawl4AI 新版本要求 Python>=3.10；
        本仓库为了兼容 Python 3.9，会在 requirements 中 pin 到旧版本。
        """
        try:
            from crawl4ai import AsyncWebCrawler  # type: ignore
        except Exception:
            return None

        try:
            # verbose=False 避免污染日志；headless 默认 True
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(url=url)
                md = getattr(result, "markdown", "") or ""
                text = getattr(result, "text", "") or ""
                html = getattr(result, "cleaned_html", "") or getattr(result, "html", "") or ""
                meta = getattr(result, "metadata", None) or {}
                if not isinstance(meta, dict):
                    meta = {}
                # only_main_content：Crawl4AI 通常已做主内容抽取，这里仅保持参数一致
                return {
                    "markdown": md,
                    "text": text,
                    "html": html,
                    "metadata": meta,
                    "success": True,
                }
        except Exception:
            return None
    
    async def scrape(
        self,
        url: str,
        only_main_content: bool = True,
        formats: Optional[List[str]] = None,
        include_tags: Optional[List[str]] = None,
        exclude_tags: Optional[List[str]] = None,
        wait_for: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        智能抓取单个页面
        
        Args:
            url: 目标 URL
            only_main_content: 是否只提取主要内容（去除广告/导航）
            formats: 输出格式列表 ["markdown", "html", "text"]
            include_tags: 只包含的 HTML 标签
            exclude_tags: 排除的 HTML 标签
            wait_for: 等待毫秒数（用于 JS 渲染）
            
        Returns:
            抓取结果字典，失败返回 None
        """
        for provider in self._provider_order():
            if provider == "firecrawl":
                if not self.config.api_key:
                    continue
                client = await self._get_client()

                payload = {
                    "url": url,
                    "onlyMainContent": only_main_content,
                }

                if formats:
                    payload["formats"] = formats
                if include_tags:
                    payload["includeTags"] = include_tags
                if exclude_tags:
                    payload["excludeTags"] = exclude_tags
                if wait_for:
                    payload["waitFor"] = wait_for

                try:
                    response = await client.post(
                        f"{self.config.base_url}/scrape",
                        json=payload
                    )
                    response.raise_for_status()

                    data = response.json()
                    if data.get("success"):
                        return data.get("data", {})
                except Exception:
                    continue

            elif provider == "crawl4ai":
                r = await self._crawl4ai_scrape(url, only_main_content=only_main_content)
                if r:
                    return r

        return None
    
    async def batch_scrape(
        self,
        urls: List[str],
        only_main_content: bool = True,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        批量抓取多个页面
        
        Args:
            urls: URL 列表
            only_main_content: 是否只提取主要内容
            formats: 输出格式列表
            
        Returns:
            {url: result} 字典
        """
        results = {}
        
        # 并发抓取，但限制并发数
        semaphore = asyncio.Semaphore(3)
        
        async def fetch_one(url: str):
            async with semaphore:
                result = await self.scrape(url, only_main_content, formats)
                results[url] = result
        
        await asyncio.gather(*[fetch_one(url) for url in urls])
        
        return results
    
    async def search_and_crawl(
        self,
        query: str,
        limit: int = 5,
        lang: str = "zh",
    ) -> List[Dict[str, Any]]:
        """
        搜索并抓取（需要 Firecrawl 搜索功能）
        
        Args:
            query: 搜索关键词
            limit: 结果数量
            lang: 语言
            
        Returns:
            抓取结果列表
        """
        # search 功能仅 Firecrawl 支持；无 key 时直接返回空
        if not self.config.api_key:
            return []
        
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.config.base_url}/search",
                json={
                    "query": query,
                    "limit": limit,
                    "lang": lang,
                    "scrapeOptions": {
                        "formats": ["markdown"],
                        "onlyMainContent": True,
                    }
                }
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("success"):
                return data.get("data", [])
            else:
                return []
                
        except Exception:
            return []
    
    def extract_content(
        self,
        firecrawl_result: Dict[str, Any],
        max_length: int = 500,
    ) -> str:
        """
        从 Firecrawl 结果中提取纯文本内容
        
        Args:
            firecrawl_result: scrape() 返回的结果
            max_length: 最大长度
            
        Returns:
            清理后的文本
        """
        # 优先使用 markdown，其次是 html，最后是 text
        content = (
            firecrawl_result.get("markdown", "")
            or firecrawl_result.get("text", "")
            or self._strip_html(firecrawl_result.get("html", ""))
        )
        
        # 清理并截断
        content = content.strip()
        if len(content) > max_length:
            content = content[:max_length] + "…"
        
        return content
    
    def _strip_html(self, html: str) -> str:
        """简单的 HTML 标签去除"""
        import re
        # 移除 script 和 style
        html = re.sub(r'<(script|style)[^>]*>[^<]*</\1>', '', html, flags=re.DOTALL)
        # 移除所有标签
        html = re.sub(r'<[^>]+>', '', html)
        # 合并空白
        html = re.sub(r'\s+', ' ', html)
        return html.strip()
    
    async def enhance_article(
        self,
        title: str,
        link: str,
        existing_desc: str = "",
    ) -> Dict[str, str]:
        """
        使用 Firecrawl 增强文章内容
        
        Args:
            title: 文章标题
            link: 文章链接
            existing_desc: 现有描述
            
        Returns:
            增强后的内容 {"desc": "...", "tags": [...], "full_content": "..."}
        """
        result = await self.scrape(link, formats=["markdown", "text"])
        
        if not result:
            # Firecrawl 失败，返回原有内容
            return {
                "desc": existing_desc or title,
                "tags": [],
                "full_content": "",
            }
        
        # 提取内容
        full_content = self.extract_content(result, max_length=2000)
        desc = self.extract_content(result, max_length=320)
        
        # 提取标题和元数据
        metadata = result.get("metadata", {})
        page_title = metadata.get("title", title)
        
        # 简单标签提取（可以扩展为 LLM 提取）
        from ..utils.text import infer_tags
        tags = infer_tags(page_title + " " + desc)
        
        return {
            "desc": desc or existing_desc,
            "tags": tags,
            "full_content": full_content,
        }
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None


# 全局服务实例
_firecrawl_service: Optional[FirecrawlService] = None


def get_firecrawl_service() -> FirecrawlService:
    """获取全局 Firecrawl 服务实例"""
    global _firecrawl_service
    if _firecrawl_service is None:
        _firecrawl_service = FirecrawlService()
    return _firecrawl_service


def reset_firecrawl_service():
    """重置全局服务（测试用）"""
    global _firecrawl_service
    _firecrawl_service = None
