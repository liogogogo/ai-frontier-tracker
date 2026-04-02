"""
数据模型定义 - Pydantic + SQLModel
"""
from datetime import datetime
from typing import Optional, List
from sqlmodel import SQLModel, Field, Index
from pydantic import BaseModel


class FeedItemBase(BaseModel):
    """Feed项基础模型"""
    type: str  # "paper", "news", "repo"
    title: str
    desc: str
    tags: List[str] = []
    date: str
    venue: str
    link: str
    heat: int = 0
    heat_breakdown: Optional[dict] = None


class FeedItem(FeedItemBase):
    """API响应模型"""
    pass


# ============== SQLModel 数据库模型 ==============

class Article(SQLModel, table=True):
    """文章数据表"""
    __tablename__ = "articles"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    link_hash: str = Field(index=True, unique=True)  # URL的MD5哈希
    link: str
    type: str  # "paper", "news", "repo"
    title: str
    desc: str
    tags: str = "[]"  # JSON字符串存储
    date: str = Field(index=True)
    venue: str = Field(index=True)
    heat: int = Field(default=0, index=True)
    
    # 元数据
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    fetch_count: int = Field(default=1)
    last_fetched_at: datetime = Field(default_factory=datetime.utcnow)
    
    # 原始数据（可选）
    raw_data: Optional[str] = None


class FetcherState(SQLModel, table=True):
    """抓取器状态表 - 用于增量抓取"""
    __tablename__ = "fetcher_states"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    fetcher_name: str = Field(index=True, unique=True)
    last_success_at: Optional[datetime] = None
    last_cursor: Optional[str] = None  # 分页游标或最后ID
    last_count: int = Field(default=0)
    total_fetches: int = Field(default=0)
    error_count: int = Field(default=0)
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None


class FetcherHealth(SQLModel, table=True):
    """抓取器健康状态"""
    __tablename__ = "fetcher_health"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    fetcher_name: str = Field(index=True, unique=True)
    status: str = "ok"  # "ok", "degraded", "down"
    last_check_at: datetime = Field(default_factory=datetime.utcnow)
    avg_response_ms: float = 0.0
    success_rate_24h: float = 1.0
    consecutive_failures: int = 0


class CollectionRun(SQLModel, table=True):
    """抓取运行记录"""
    __tablename__ = "collection_runs"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    total_items: int = 0
    new_items: int = 0
    updated_items: int = 0
    errors: str = "[]"  # JSON数组
    duration_ms: int = 0
