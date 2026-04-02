"""
Fetcher基类定义 - 所有抓取器继承此类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Any
import asyncio

from sqlmodel import select

from ..models import FeedItem, FetcherState, FetcherHealth
from ..config import FetcherConfig
from ..database import get_session


@dataclass
class FetchResult:
    """抓取结果"""
    items: List[Dict[str, Any]]
    cursor: Optional[str] = None  # 用于增量抓取的游标
    has_more: bool = False
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class FetcherStats:
    """抓取器统计信息"""
    name: str
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    total_fetches: int = 0
    total_items: int = 0
    error_count: int = 0
    avg_duration_ms: float = 0.0


class BaseFetcher(ABC):
    """所有抓取器的基类"""
    
    def __init__(self, config: Optional[FetcherConfig] = None):
        self.name = self.__class__.__name__.replace("Fetcher", "").lower()
        self.config = config or FetcherConfig()
        self._stats = FetcherStats(name=self.name)
    
    @abstractmethod
    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """
        执行抓取
        
        Args:
            cursor: 上次抓取的游标，用于增量抓取
            
        Returns:
            FetchResult: 抓取结果
        """
        pass
    
    async def fetch_with_state(self) -> FetchResult:
        """
        带状态管理的抓取（自动保存和恢复游标）
        """
        from ..models import FetcherState

        # 获取上次状态
        with get_session() as session:
            statement = select(FetcherState).where(FetcherState.fetcher_name == self.name)
            state = session.exec(statement).first()
            cursor = state.last_cursor if state else None
        
        # 执行抓取
        start_time = datetime.utcnow()
        try:
            result = await self.fetch(cursor=cursor)
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            # 更新状态
            with get_session() as session:
                statement = select(FetcherState).where(FetcherState.fetcher_name == self.name)
                state = session.exec(statement).first()
                
                if not state:
                    state = FetcherState(fetcher_name=self.name)
                    session.add(state)
                
                state.last_success_at = datetime.utcnow()
                state.last_cursor = result.cursor
                state.last_count = len(result.items)
                state.total_fetches += 1
                
                if result.error:
                    state.error_count += 1
                    state.last_error = result.error
                    state.last_error_at = datetime.utcnow()
                
                session.commit()
            
            # 更新统计
            self._stats.last_success = datetime.utcnow()
            self._stats.total_fetches += 1
            self._stats.total_items += len(result.items)
            self._stats.avg_duration_ms = (
                (self._stats.avg_duration_ms * (self._stats.total_fetches - 1) + duration_ms)
                / self._stats.total_fetches
            )
            
            result.duration_ms = duration_ms
            return result
            
        except Exception as e:
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            error_msg = str(e)
            
            # 更新错误状态
            with get_session() as session:
                statement = select(FetcherState).where(FetcherState.fetcher_name == self.name)
                state = session.exec(statement).first()
                
                if not state:
                    state = FetcherState(fetcher_name=self.name)
                    session.add(state)
                
                state.error_count += 1
                state.last_error = error_msg
                state.last_error_at = datetime.utcnow()
                session.commit()
            
            self._stats.last_error = error_msg
            self._stats.error_count += 1
            
            return FetchResult(items=[], error=error_msg, duration_ms=duration_ms)
    
    def get_stats(self) -> FetcherStats:
        """获取统计信息"""
        return self._stats
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        from ..models import FetcherHealth, FetcherState

        with get_session() as session:
            statement = select(FetcherHealth).where(FetcherHealth.fetcher_name == self.name)
            health = session.exec(statement).first()
            
            if not health:
                health = FetcherHealth(fetcher_name=self.name)
                session.add(health)
            
            # 根据最近状态判断
            stmt = select(FetcherState).where(FetcherState.fetcher_name == self.name)
            state = session.exec(stmt).first()
            
            if state and state.last_success_at:
                hours_since_success = (datetime.utcnow() - state.last_success_at).total_seconds() / 3600
                if hours_since_success > 24:
                    health.status = "down"
                elif hours_since_success > 4:
                    health.status = "degraded"
                else:
                    health.status = "ok"
            else:
                health.status = "unknown"
            
            health.last_check_at = datetime.utcnow()
            session.commit()
            
            return {
                "fetcher": self.name,
                "status": health.status,
                "last_check": health.last_check_at.isoformat(),
                "consecutive_failures": health.consecutive_failures,
            }
