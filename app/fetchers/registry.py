"""
抓取器注册表 - 管理所有Fetcher
"""
from typing import Dict, Type, List, Optional
from .base import BaseFetcher


class FetcherRegistry:
    """抓取器注册表 - 插件化管理"""
    
    _fetchers: Dict[str, Type[BaseFetcher]] = {}
    _instances: Dict[str, BaseFetcher] = {}
    
    @classmethod
    def register(cls, name: str, fetcher_class: Type[BaseFetcher]):
        """注册抓取器类"""
        cls._fetchers[name] = fetcher_class
    
    @classmethod
    def get(cls, name: str, config=None) -> Optional[BaseFetcher]:
        """获取或创建抓取器实例"""
        if name not in cls._instances:
            fetcher_class = cls._fetchers.get(name)
            if fetcher_class:
                cls._instances[name] = fetcher_class(config)
        return cls._instances.get(name)
    
    @classmethod
    def list_fetchers(cls) -> List[str]:
        """列出所有已注册的抓取器"""
        return list(cls._fetchers.keys())
    
    @classmethod
    def get_all(cls, enabled_only: bool = True) -> Dict[str, BaseFetcher]:
        """获取所有抓取器实例"""
        from ..config import CONFIG

        # firecrawl-only 模式：仅启用 firecrawl 聚合抓取器
        fetcher_names = list(cls._fetchers.keys())
        if getattr(CONFIG, "firecrawl_only", False):
            fetcher_names = [n for n in fetcher_names if n == "firecrawl"]

        result = {}
        for name in fetcher_names:
            fetcher = cls.get(name)
            if fetcher:
                if enabled_only:
                    # 检查配置是否启用
                    config = getattr(CONFIG, name, None)
                    if config and not config.enabled:
                        continue
                result[name] = fetcher
        return result
    
    @classmethod
    def clear_instances(cls):
        """清空所有实例（用于测试）"""
        cls._instances.clear()


# 装饰器用于自动注册
def register_fetcher(name: str):
    """装饰器：自动注册抓取器"""
    def decorator(cls: Type[BaseFetcher]):
        FetcherRegistry.register(name, cls)
        return cls
    return decorator
