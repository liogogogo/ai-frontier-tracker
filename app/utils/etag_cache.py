"""
HTTP 缓存标记管理 - ETag/Last-Modified 增量抓取
"""
import json
from typing import Dict, Tuple, Optional
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class CacheEntry:
    """缓存条目"""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    cached_at: Optional[str] = None
    content_hash: Optional[str] = None  # 用于内容比对


class ETagCache:
    """
    ETag/Last-Modified 缓存管理器
    
    支持：
    - 内存缓存（运行时）
    - 持久化到 JSON 文件（重启后恢复）
    - 自动过期清理
    """
    
    def __init__(self, cache_file: Optional[Path] = None, ttl_hours: int = 168):
        """
        Args:
            cache_file: 缓存文件路径，默认 ~/.cache/ai_news/etag_cache.json
            ttl_hours: 缓存过期时间（默认7天）
        """
        self._cache: Dict[str, CacheEntry] = {}
        self.ttl_hours = ttl_hours
        
        if cache_file is None:
            cache_dir = Path.home() / ".cache" / "ai_news"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file = cache_dir / "etag_cache.json"
        else:
            self.cache_file = Path(cache_file)
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        
        self._load()
    
    def _load(self):
        """从文件加载缓存"""
        if not self.cache_file.exists():
            return
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for url, entry_data in data.items():
                entry = CacheEntry(**entry_data)
                # 检查过期
                if entry.cached_at:
                    try:
                        cached_time = datetime.fromisoformat(entry.cached_at)
                        hours_old = (datetime.utcnow() - cached_time).total_seconds() / 3600
                        if hours_old > self.ttl_hours:
                            continue  # 跳过过期条目
                    except:
                        pass
                self._cache[url] = entry
        except Exception:
            pass  # 加载失败则使用空缓存
    
    def _save(self):
        """保存缓存到文件"""
        try:
            data = {url: asdict(entry) for url, entry in self._cache.items()}
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 保存失败不影响运行
    
    def get_headers(self, url: str) -> Dict[str, str]:
        """
        获取条件请求头（If-None-Match / If-Modified-Since）
        
        Args:
            url: 请求 URL
            
        Returns:
            请求头字典
        """
        entry = self._cache.get(url)
        if not entry:
            return {}
        
        headers = {}
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        
        return headers
    
    def update(self, url: str, etag: Optional[str] = None, 
               last_modified: Optional[str] = None,
               content_hash: Optional[str] = None):
        """
        更新缓存标记
        
        Args:
            url: 请求 URL
            etag: ETag 值
            last_modified: Last-Modified 值
            content_hash: 内容哈希（用于本地校验）
        """
        self._cache[url] = CacheEntry(
            etag=etag,
            last_modified=last_modified,
            cached_at=datetime.utcnow().isoformat(),
            content_hash=content_hash
        )
        self._save()
    
    def is_not_modified(self, status_code: int) -> bool:
        """检查是否为 304 Not Modified"""
        return status_code == 304
    
    def get_cached_indicator(self, url: str) -> Optional[str]:
        """获取缓存标识（用于日志）"""
        entry = self._cache.get(url)
        if entry:
            return entry.etag or entry.last_modified[:20] if entry.last_modified else "cached"
        return None
    
    def clear(self):
        """清空缓存"""
        self._cache.clear()
        if self.cache_file.exists():
            self.cache_file.unlink()
    
    def stats(self) -> Dict:
        """获取缓存统计"""
        return {
            "total_entries": len(self._cache),
            "cache_file": str(self.cache_file),
            "with_etag": sum(1 for e in self._cache.values() if e.etag),
            "with_modified": sum(1 for e in self._cache.values() if e.last_modified),
        }


# 全局缓存实例
_etag_cache: Optional[ETagCache] = None


def get_etag_cache() -> ETagCache:
    """获取全局 ETag 缓存实例"""
    global _etag_cache
    if _etag_cache is None:
        _etag_cache = ETagCache()
    return _etag_cache
