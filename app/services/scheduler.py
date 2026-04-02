"""
自适应调度器 - 基于性能反馈的动态调度优化

根据抓取器的响应时间和成功率动态调整：
1. 调度优先级（成功率高的优先）
2. 请求延迟（根据限流响应自适应）
3. 并发控制（避免热点源过载）
"""
import asyncio
import time
from typing import Dict, List, Tuple, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime


@dataclass
class FetcherMetrics:
    """抓取器性能指标"""
    name: str
    response_times: List[float] = field(default_factory=list)  # 最近响应时间
    success_count: int = 0
    error_count: int = 0
    rate_limited_count: int = 0  # 429 次数
    last_rate_limited: Optional[float] = None  # 上次429时间戳
    last_success: Optional[float] = None
    consecutive_failures: int = 0
    
    def add_response_time(self, duration: float):
        """添加响应时间记录（保留最近20个）"""
        self.response_times.append(duration)
        if len(self.response_times) > 20:
            self.response_times.pop(0)
    
    @property
    def avg_response_time(self) -> float:
        """平均响应时间（最近5次）"""
        recent = self.response_times[-5:] if self.response_times else []
        return sum(recent) / len(recent) if recent else 0.0
    
    @property
    def success_rate(self) -> float:
        """成功率（最近10次）"""
        total = self.success_count + self.error_count
        if total == 0:
            return 1.0  # 默认乐观
        return self.success_count / min(total, 10)
    
    @property
    def priority(self) -> int:
        """计算调度优先级（数值越小优先级越高）"""
        # 成功率权重 70%，响应时间权重 30%
        success_score = self.success_rate * 100
        
        # 响应时间评分（<2s得满分，>10s得0分）
        avg = self.avg_response_time
        if avg < 2:
            speed_score = 100
        elif avg > 10:
            speed_score = 0
        else:
            speed_score = 100 - (avg - 2) * 12.5
        
        score = success_score * 0.7 + speed_score * 0.3
        return int(100 - score)  # 反转：分越低优先级越高
    
    @property
    def recommended_delay(self) -> float:
        """推荐的请求延迟（秒）"""
        base = 0.3
        
        # 根据连续失败增加延迟
        if self.consecutive_failures > 0:
            base += min(self.consecutive_failures * 0.5, 3.0)
        
        # 最近有429，增加延迟
        if self.last_rate_limited:
            seconds_since = time.time() - self.last_rate_limited
            if seconds_since < 60:  # 1分钟内发生过限流
                base += max(0, 2 - seconds_since / 30)  # 衰减
        
        # 响应时间慢的，增加一点延迟
        if self.avg_response_time > 5:
            base += 0.5
        
        return base


class AdaptiveScheduler:
    """
    自适应调度器
    
    特性：
    - 动态优先级排序
    - 自适应延迟调整
    - 429限流响应
    - 失败退避
    """
    
    def __init__(self):
        self._metrics: Dict[str, FetcherMetrics] = {}
        self._fetch_start_times: Dict[str, float] = {}
    
    def _get_metrics(self, name: str) -> FetcherMetrics:
        """获取或创建指标对象"""
        if name not in self._metrics:
            self._metrics[name] = FetcherMetrics(name=name)
        return self._metrics[name]
    
    def start_fetch(self, name: str):
        """记录抓取开始时间"""
        self._fetch_start_times[name] = time.time()
    
    def record_success(self, name: str, item_count: int = 0):
        """记录成功抓取"""
        metrics = self._get_metrics(name)
        
        # 计算响应时间
        start = self._fetch_start_times.pop(name, None)
        if start:
            duration = time.time() - start
            metrics.add_response_time(duration)
        
        metrics.success_count += 1
        metrics.consecutive_failures = 0
        metrics.last_success = time.time()
    
    def record_error(self, name: str, error: str = ""):
        """记录失败"""
        metrics = self._get_metrics(name)
        metrics.error_count += 1
        metrics.consecutive_failures += 1
        
        # 检测是否被限流
        if "429" in error or "rate limit" in error.lower():
            metrics.rate_limited_count += 1
            metrics.last_rate_limited = time.time()
    
    def get_scheduling_order(self, fetcher_names: List[str]) -> List[Tuple[str, float]]:
        """
        获取调度顺序和延迟
        
        Returns:
            [(name, delay), ...] 按优先级排序
        """
        with_delays = []
        for name in fetcher_names:
            metrics = self._get_metrics(name)
            with_delays.append((name, metrics.priority, metrics.recommended_delay))
        
        # 按优先级排序（数字小的优先）
        with_delays.sort(key=lambda x: x[1])
        
        return [(name, delay) for name, _, delay in with_delays]
    
    async def run_with_scheduling(
        self,
        fetcher_coro: Callable[[str], Any],
        fetcher_names: List[str]
    ) -> Dict[str, Any]:
        """
        按调度顺序执行抓取器
        
        Args:
            fetcher_coro: 接收 fetcher_name 返回 awaitable 的函数
            fetcher_names: 抓取器名称列表
            
        Returns:
            结果字典 {name: result}
        """
        order = self.get_scheduling_order(fetcher_names)
        results = {}
        
        # 按优先级分批执行
        # 优先级 0-30: 第一批并发执行
        # 优先级 31-60: 第二批，延迟启动
        # 优先级 >60: 第三批，最大延迟
        
        batches = [[], [], []]
        for name, delay in order:
            metrics = self._get_metrics(name)
            p = metrics.priority
            if p < 30:
                batches[0].append((name, delay))
            elif p < 60:
                batches[1].append((name, delay))
            else:
                batches[2].append((name, delay))
        
        # 第一批：立即并发
        if batches[0]:
            tasks = []
            for name, delay in batches[0]:
                self.start_fetch(name)
                task = asyncio.create_task(self._run_with_delay(fetcher_coro, name, delay))
                tasks.append((name, task))
            
            for name, task in tasks:
                try:
                    results[name] = await task
                    self.record_success(name)
                except Exception as e:
                    results[name] = e
                    self.record_error(name, str(e))
        
        # 第二批：延迟1秒
        if batches[1]:
            await asyncio.sleep(1)
            tasks = []
            for name, delay in batches[1]:
                self.start_fetch(name)
                task = asyncio.create_task(self._run_with_delay(fetcher_coro, name, delay))
                tasks.append((name, task))
            
            for name, task in tasks:
                try:
                    results[name] = await task
                    self.record_success(name)
                except Exception as e:
                    results[name] = e
                    self.record_error(name, str(e))
        
        # 第三批：延迟3秒
        if batches[2]:
            await asyncio.sleep(2)
            for name, delay in batches[2]:
                self.start_fetch(name)
                try:
                    results[name] = await self._run_with_delay(fetcher_coro, name, delay)
                    self.record_success(name)
                except Exception as e:
                    results[name] = e
                    self.record_error(name, str(e))
        
        return results
    
    async def _run_with_delay(self, coro: Callable[[str], Any], name: str, delay: float):
        """带延迟执行"""
        if delay > 0:
            await asyncio.sleep(delay)
        return await coro(name)
    
    def get_stats(self) -> Dict[str, Any]:
        """获取调度器统计"""
        stats = {}
        for name, metrics in self._metrics.items():
            stats[name] = {
                "success_rate": f"{metrics.success_rate:.1%}",
                "avg_response_time": f"{metrics.avg_response_time:.2f}s",
                "priority": metrics.priority,
                "recommended_delay": f"{metrics.recommended_delay:.1f}s",
                "consecutive_failures": metrics.consecutive_failures,
                "rate_limited_count": metrics.rate_limited_count,
            }
        return stats
    
    def get_recommendations(self) -> List[str]:
        """获取优化建议"""
        recommendations = []
        
        for name, metrics in self._metrics.items():
            if metrics.success_rate < 0.5:
                recommendations.append(f"{name}: 成功率过低({metrics.success_rate:.1%})，建议检查API配额或增加延迟")
            
            if metrics.rate_limited_count > 3:
                recommendations.append(f"{name}: 频繁被限流({metrics.rate_limited_count}次)，建议增大 base delay")
            
            if metrics.avg_response_time > 8:
                recommendations.append(f"{name}: 响应慢({metrics.avg_response_time:.1f}s)，建议优化或降低超时")
        
        return recommendations


# 全局调度器实例
_scheduler: Optional[AdaptiveScheduler] = None


def get_scheduler() -> AdaptiveScheduler:
    """获取全局调度器实例"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AdaptiveScheduler()
    return _scheduler
