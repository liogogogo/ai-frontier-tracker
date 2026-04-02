"""
手动验证 arXiv 抓取器（非 pytest 用例）。

从仓库根目录执行: python tests/test_arxiv.py
"""
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def main():
    from app.fetchers.arxiv import ArxivFetcher

    print("=" * 50)
    print("测试 arXiv 抓取器")
    print("=" * 50)

    fetcher = ArxivFetcher()
    print("\n1. 抓取器创建成功")
    print(f"   Base URL: {fetcher.base_url}")
    print(f"   缓存条目数: {len(fetcher._cached_items)}")

    print("\n2. 开始抓取...")
    result = await fetcher.fetch()

    print("\n3. 抓取完成")
    print(f"   返回条目数: {len(result.items)}")
    print(f"   错误信息: {result.error}")

    if result.items:
        print("\n4. 前3条论文:")
        for i, item in enumerate(result.items[:3], 1):
            print(f"   {i}. {item.get('title', 'N/A')[:50]}...")
            print(f"      Link: {item.get('link', 'N/A')[:60]}...")
            print(f"      Type: {item.get('type')}, Date: {item.get('date')}")
    else:
        print("\n4. 警告: 没有获取到任何论文!")
        print(f"   缓存条目数: {len(fetcher._cached_items)}")
        print(f"   304计数: {fetcher._not_modified_count}")


if __name__ == "__main__":
    asyncio.run(main())
