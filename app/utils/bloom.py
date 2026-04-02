"""
Bloom Filter 去重优化 - 内存高效的重复检测

用于抓取前快速去重，避免数据库查询开销
"""
import hashlib
import json
from typing import Set, Optional, List, Dict, Any
from pathlib import Path


class BloomFilter:
    """
    简化的 Bloom Filter 实现
    
    使用位数组和多个哈希函数进行概率性去重
    误判率低（<1%），无漏判，空间效率高
    """
    
    def __init__(self, capacity: int = 100000, error_rate: float = 0.001):
        """
        Args:
            capacity: 预期元素数量
            error_rate: 可接受的误判率
        """
        self.capacity = capacity
        self.error_rate = error_rate
        
        # 计算位数组大小和哈希函数数量
        # m = -n * ln(p) / (ln(2)^2)
        # k = m/n * ln(2)
        import math
        self.size = int(-capacity * math.log(error_rate) / (math.log(2) ** 2))
        self.hash_count = max(1, int(self.size / capacity * math.log(2)))
        
        # 使用 Python set 模拟位数组（简化实现）
        # 生产环境可用 bitarray 库
        self._bits: Set[int] = set()
        self._count = 0
    
    def _hashes(self, item: str) -> List[int]:
        """生成多个哈希值"""
        # 使用双哈希模拟 k 个哈希函数
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha256(item.encode()).hexdigest(), 16)
        
        result = []
        for i in range(self.hash_count):
            # gi(x) = h1(x) + i * h2(x) mod m
            hash_val = (h1 + i * h2) % self.size
            result.append(hash_val)
        return result
    
    def add(self, item: str):
        """添加元素到过滤器"""
        for hash_val in self._hashes(item):
            self._bits.add(hash_val)
        self._count += 1
    
    def __contains__(self, item: str) -> bool:
        """检查元素可能存在（True）或肯定不存在（False）"""
        return all(hash_val in self._bits for hash_val in self._hashes(item))
    
    def contains(self, item: str) -> bool:
        """检查元素是否存在"""
        return self.__contains__(item)
    
    @property
    def current_error_rate(self) -> float:
        """当前实际误判率估计"""
        if self._count == 0:
            return 0.0
        # (1 - e^(-kn/m))^k
        import math
        return (1 - math.exp(-self.hash_count * self._count / self.size)) ** self.hash_count
    
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        return {
            "capacity": self.capacity,
            "size_bits": self.size,
            "hash_functions": self.hash_count,
            "items_added": self._count,
            "current_error_rate": f"{self.current_error_rate:.4%}",
            "memory_estimate_kb": len(self._bits) * 8 / 1024,
        }


class LinkDeduplicator:
    """
    链接去重器 - 结合 Bloom Filter + 精确确认集合
    
    流程：
    1. Bloom Filter 快速预筛选（排除大部分重复）
    2. 精确集合确认（消除误判）
    3. 新链接加入过滤器
    """
    
    def __init__(self, capacity: int = 100000, error_rate: float = 0.001):
        self.bloom = BloomFilter(capacity=capacity, error_rate=error_rate)
        self._confirmed: Set[str] = set()  # 确认存在的链接
        self._false_positive_count = 0
        self._hit_count = 0
        self._miss_count = 0
    
    def is_duplicate(self, link: str) -> bool:
        """
        检查链接是否重复
        
        Returns:
            True: 链接已存在（或误判）
            False: 链接肯定不存在
        """
        if not link:
            return False
        
        # Bloom Filter 快速检查
        if link not in self.bloom:
            self._miss_count += 1
            return False
        
        # 可能在集合中，精确确认
        self._hit_count += 1
        if link in self._confirmed:
            return True
        
        # 误判
        self._false_positive_count += 1
        return False
    
    def add(self, link: str):
        """添加新链接到去重器"""
        if not link:
            return
        
        self.bloom.add(link)
        self._confirmed.add(link)
    
    def add_batch(self, links: List[str]):
        """批量添加"""
        for link in links:
            self.add(link)
    
    def deduplicate_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        对抓取结果去重
        
        Args:
            items: 抓取到的文章列表
            
        Returns:
            去重后的列表（保留首次出现的）
        """
        result = []
        seen_in_batch: Set[str] = set()
        
        for item in items:
            link = item.get("link", "")
            if not link:
                continue
            
            # 检查是否在历史记录中
            if self.is_duplicate(link):
                continue
            
            # 检查是否在当前批次中重复
            if link in seen_in_batch:
                continue
            
            seen_in_batch.add(link)
            result.append(item)
        
        # 将新链接加入过滤器
        for link in seen_in_batch:
            self.add(link)
        
        return result
    
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        total_checks = self._hit_count + self._miss_count
        bloom_stats = self.bloom.stats()
        
        return {
            **bloom_stats,
            "confirmed_items": len(self._confirmed),
            "total_checks": total_checks,
            "cache_hit_rate": f"{self._hit_count / total_checks:.2%}" if total_checks > 0 else "0%",
            "false_positives": self._false_positive_count,
            "false_positive_rate": f"{self._false_positive_count / self._hit_count:.2%}" if self._hit_count > 0 else "0%",
        }
    
    def clear(self):
        """清空去重器"""
        self.bloom = BloomFilter(capacity=self.bloom.capacity, error_rate=self.bloom.error_rate)
        self._confirmed.clear()
        self._false_positive_count = 0
        self._hit_count = 0
        self._miss_count = 0


# 全局去重器实例
_deduplicator: Optional[LinkDeduplicator] = None


def get_deduplicator() -> LinkDeduplicator:
    """获取全局链接去重器实例"""
    global _deduplicator
    if _deduplicator is None:
        _deduplicator = LinkDeduplicator(capacity=100000, error_rate=0.001)
    return _deduplicator


def reset_deduplicator():
    """重置全局去重器（测试用）"""
    global _deduplicator
    _deduplicator = None
