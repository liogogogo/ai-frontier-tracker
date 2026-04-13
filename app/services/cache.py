"""
内存缓存 + 数据库缓存管理
"""
import time
import hashlib
import json
from typing import Any, Dict, Optional
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import select

from ..database import get_session
from ..models import Article
from ..config import CONFIG
from ..utils.dedup_urls import canonicalize_link


@dataclass
class CacheEntry:
    """缓存条目"""
    data: Any
    expires_at: float
    created_at: float


class MemoryCache:
    """简单的内存缓存"""
    
    def __init__(self, ttl_seconds: int = 300, max_items: int = 1000):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._cache: Dict[str, CacheEntry] = {}
    
    def _make_key(self, *args) -> str:
        """生成缓存键"""
        key_data = json.dumps(args, sort_keys=True, default=str)
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def get(self, *args) -> Optional[Any]:
        """获取缓存值"""
        key = self._make_key(*args)
        entry = self._cache.get(key)
        
        if entry is None:
            return None
        
        if time.time() > entry.expires_at:
            del self._cache[key]
            return None
        
        return entry.data
    
    def set(self, *args, data: Any, ttl: Optional[int] = None) -> None:
        """设置缓存值"""
        # 清理过期项
        self._cleanup()
        
        # 如果满了，删除最旧的
        if len(self._cache) >= self.max_items:
            oldest_key = min(self._cache.keys(), 
                           key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]
        
        key = self._make_key(*args)
        now = time.time()
        self._cache[key] = CacheEntry(
            data=data,
            expires_at=now + (ttl or self.ttl),
            created_at=now
        )
    
    def _cleanup(self):
        """清理过期条目"""
        now = time.time()
        expired = [k for k, v in self._cache.items() if now > v.expires_at]
        for k in expired:
            del self._cache[k]
    
    def clear(self):
        """清空缓存"""
        self._cache.clear()
    
    @property
    def size(self) -> int:
        return len(self._cache)


class DatabaseCache:
    """数据库缓存层 - 用于持久化存储和去重"""
    
    @staticmethod
    def get_existing_links(links: list[str]) -> set[str]:
        """批量查询已存在的链接"""
        if not links:
            return set()
        
        with get_session() as session:
            import hashlib
            
            # 计算所有链接的哈希
            link_hashes = [hashlib.md5(link.encode()).hexdigest() for link in links]
            
            # 查询存在的
            statement = select(Article.link_hash).where(Article.link_hash.in_(link_hashes))
            results = session.exec(statement).all()
            
            # 转回原始链接
            hash_to_link = {h: l for h, l in zip(link_hashes, links)}
            return {hash_to_link[h] for h in results if h in hash_to_link}
    
    @staticmethod
    def _merge_raw_data(existing_raw: Optional[str], updates: dict) -> str:
        """将 updates 合并进现有 raw_data JSON，保留未覆盖的已有字段。"""
        try:
            d = json.loads(existing_raw) if existing_raw else {}
        except Exception:
            d = {}
        d.update(updates)
        return json.dumps(d, ensure_ascii=False)

    @staticmethod
    def save_or_update_article(item: dict) -> tuple[bool, bool]:
        """
        保存或更新文章
        返回: (是否是新文章, 是否更新了热度)
        """
        import hashlib

        link = canonicalize_link(item.get("link", "") or "")
        if not link:
            return False, False
        item["link"] = link

        link_hash = hashlib.md5(link.encode()).hexdigest()

        # 组装本次需写入 raw_data 的字段（增量合并，不覆盖其他字段）
        raw_updates: dict = {}
        if item.get("heat_breakdown"):
            raw_updates["heat_breakdown"] = item["heat_breakdown"]
        if item.get("entities") is not None:
            raw_updates["entities"] = item["entities"]
        if item.get("arxiv_id"):
            raw_updates["arxiv_id"] = item["arxiv_id"]

        with get_session() as session:
            # 查询是否存在
            statement = select(Article).where(Article.link_hash == link_hash)
            existing = session.exec(statement).first()

            if existing:
                is_new = False
                heat_changed = existing.heat != item.get("heat", 0)

                existing.type = item.get("type", existing.type)
                existing.title = item.get("title", existing.title)
                existing.desc = item.get("desc", existing.desc)
                existing.date = item.get("date", existing.date)
                existing.venue = item.get("venue", existing.venue)
                existing.heat = item.get("heat", 0)
                existing.updated_at = datetime.utcnow()
                existing.fetch_count += 1
                existing.last_fetched_at = datetime.utcnow()
                existing.tags = json.dumps(item.get("tags", []))

                if raw_updates:
                    existing.raw_data = DatabaseCache._merge_raw_data(existing.raw_data, raw_updates)

                return is_new, heat_changed
            else:
                article = Article(
                    link_hash=link_hash,
                    link=link,
                    type=item.get("type", "news"),
                    title=item.get("title", ""),
                    desc=item.get("desc", ""),
                    tags=json.dumps(item.get("tags", [])),
                    date=item.get("date", ""),
                    venue=item.get("venue", ""),
                    heat=item.get("heat", 0),
                    raw_data=json.dumps(raw_updates, ensure_ascii=False) if raw_updates else None,
                )
                session.add(article)
                return True, False


class CacheManager:
    """组合内存和数据库缓存"""
    
    def __init__(self, memory_ttl: Optional[int] = None):
        ttl = memory_ttl if memory_ttl is not None else CONFIG.cache.ttl_seconds
        self.memory = MemoryCache(ttl_seconds=ttl)
        self.db = DatabaseCache()
    
    def get_feed(self) -> Optional[list]:
        """获取缓存的Feed"""
        return self.memory.get("feed")
    
    def set_feed(self, items: list) -> None:
        """缓存Feed"""
        self.memory.set("feed", data=items, ttl=self.memory.ttl)
    
    def clear(self):
        """清空所有缓存"""
        self.memory.clear()


# 全局缓存实例
cache = CacheManager()
