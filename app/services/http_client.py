"""
带重试、限流、缓存的HTTP客户端
"""
import asyncio
import time
from typing import Optional, Any, Dict, Tuple
import httpx


class NotModifiedError(Exception):
    """304 Not Modified - 内容未变化"""
    def __init__(self, url: str):
        self.url = url
        super().__init__(f"Content not modified: {url}")


class RetryableHTTPClient:
    """支持重试和指数退避的HTTP客户端"""
    
    def __init__(
        self,
        base_headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        timeout: float = 25.0,
        rate_limit_delay: float = 0.0,
        cache_condition: bool = True,  # 是否启用条件请求
    ):
        self.client = httpx.AsyncClient(
            headers=base_headers or {"User-Agent": "ai-frontier-tracker/2.0"},
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limit_delay = rate_limit_delay
        self.cache_condition = cache_condition
        self._last_request_time: Optional[float] = None
        self._response_times: list[float] = []
        self._not_modified_count = 0
    
    async def _rate_limit(self):
        """简单的速率限制"""
        if self.rate_limit_delay > 0 and self._last_request_time:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.rate_limit_delay:
                await asyncio.sleep(self.rate_limit_delay - elapsed)
    
    def _record_response_time(self, duration: float):
        """记录响应时间用于统计"""
        self._response_times.append(duration)
        # 只保留最近100个样本
        if len(self._response_times) > 100:
            self._response_times.pop(0)
    
    @property
    def avg_response_time(self) -> float:
        """平均响应时间"""
        if not self._response_times:
            return 0.0
        return sum(self._response_times) / len(self._response_times)
    
    @property
    def not_modified_count(self) -> int:
        """304 Not Modified 计数"""
        return self._not_modified_count
    
    async def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        etag_headers: Optional[Dict[str, str]] = None,  # 条件请求头
        **kwargs
    ) -> httpx.Response:
        """
        带重试的GET请求
        
        Args:
            url: 请求 URL
            params: 查询参数
            headers: 请求头
            etag_headers: If-None-Match / If-Modified-Since 条件请求头
            
        Returns:
            Response 对象
            
        Raises:
            NotModifiedError: 当返回 304 且启用了条件请求时
        """
        last_exception: Optional[Exception] = None
        
        for attempt in range(self.max_retries):
            await self._rate_limit()
            start_time = time.time()
            
            try:
                # 合并请求头
                merged_headers = {**dict(self.client.headers), **(headers or {})}
                if etag_headers and self.cache_condition:
                    merged_headers.update(etag_headers)
                
                response = await self.client.get(
                    url, params=params, headers=merged_headers, **kwargs
                )
                self._last_request_time = time.time()
                self._record_response_time(time.time() - start_time)
                
                # 304 Not Modified - 内容未变化
                if response.status_code == 304:
                    self._not_modified_count += 1
                    raise NotModifiedError(url)
                
                # 429 限流：arXiv 等常无 Retry-After，需至少等几秒再试
                if response.status_code == 429:
                    ra_raw = response.headers.get("Retry-After", "")
                    try:
                        retry_after = max(12, int(float(ra_raw)))
                    except (ValueError, TypeError):
                        retry_after = 14
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(retry_after)
                        continue
                
                response.raise_for_status()
                return response
                
            except NotModifiedError:
                raise  # 304 不重试，直接抛出
                
            except httpx.HTTPStatusError as e:
                last_exception = e
                # 5xx错误或429可以重试
                if e.response.status_code >= 500 or e.response.status_code == 429:
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                        continue
                raise
                
            except (httpx.NetworkError, httpx.TimeoutException) as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                raise
        
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected error in retry logic")
    
    async def close(self):
        """关闭客户端"""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
