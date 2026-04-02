"""
Firecrawl 集成 - 智能内容提取服务

Firecrawl 是一个专为 AI 应用设计的网页抓取工具，可以：
1. 自动渲染 JavaScript
2. 提取主内容（去除广告、导航等噪声）
3. 输出 Markdown 格式（LLM-ready）
4. 支持批量抓取和结构化数据提取

官网: https://firecrawl.dev
"""
import asyncio
import os
import json
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

import httpx


@dataclass
class FirecrawlConfig:
    """Firecrawl 配置"""
    api_key: str = ""
    base_url: str = "https://api.firecrawl.dev/v1"
    timeout: float = 30.0
    
    def __post_init__(self):
        if not self.api_key:
            self.api_key = os.getenv("FIRECRAWL_API_KEY", "")


class FirecrawlService:
    """
    Firecrawl 服务封装
    
    提供智能内容提取功能，替代传统的 HTML 清洗逻辑
    """
    
    def __init__(self, config: Optional[FirecrawlConfig] = None):
        self.config = config or FirecrawlConfig()
        self._client: Optional[httpx.AsyncClient] = None
    
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
        if not self.config.api_key:
            return None
        
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
            else:
                return None
                
        except Exception:
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
