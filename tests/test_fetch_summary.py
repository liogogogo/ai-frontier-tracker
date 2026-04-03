import unittest
from unittest.mock import AsyncMock, patch

from app.fetchers.arxiv import ArxivFetcher
from app.fetchers.rss import RSSFetcher
from app.fetchers.base import FetchResult
from app.services.collector import CollectorService
from app.services.http_client import NotModifiedError


class RSSFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_single_reuses_cached_items_on_not_modified(self):
        fetcher = RSSFetcher()
        url = "https://example.com/feed.xml"
        cached_items = [{"title": "cached", "link": "https://example.com/a"}]
        fetcher._cached_items_by_url[url] = list(cached_items)

        client = AsyncMock()
        client.get.side_effect = NotModifiedError(url)

        items, used_cache = await fetcher._fetch_single(
            client,
            "Example",
            url,
            max_items=5,
            item_type="news",
        )

        self.assertTrue(used_cache)
        self.assertEqual(items, cached_items)


class ArxivFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_returns_status_not_error_on_not_modified(self):
        fetcher = ArxivFetcher()
        fetcher._cached_items = [{"title": "paper", "link": "https://arxiv.org/abs/1"}]

        with patch.object(fetcher, "_fetch_one", side_effect=NotModifiedError(fetcher.base_url)):
            result = await fetcher.fetch()

        self.assertIsNone(result.error)
        self.assertEqual(result.items, fetcher._cached_items)
        self.assertEqual(result.source_status["outcome"], "unchanged")
        self.assertTrue(result.source_status["used_cache"])
        self.assertTrue(result.source_status["not_modified"])
        self.assertIn("未变化(304)", result.status)


class CollectorSummaryTests(unittest.TestCase):
    def test_build_fetch_summary_separates_unchanged_and_failed(self):
        collector = CollectorService()

        summary = collector._build_fetch_summary(
            ["rss", "arxiv", "github"],
            {
                "rss": {
                    "outcome": "unchanged",
                    "used_cache": True,
                    "not_modified": True,
                    "fetched_count": 12,
                },
                "arxiv": {
                    "outcome": "success",
                    "used_cache": False,
                    "fetched_count": 8,
                },
                "github": {
                    "outcome": "error",
                    "used_cache": False,
                    "fetched_count": 0,
                },
            },
            total_items=20,
        )

        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["sources"], {
            "total": 3,
            "success": 2,
            "unchanged": 1,
            "failed": 1,
        })
        self.assertTrue(summary["cache"]["used"])
        self.assertEqual(summary["cache"]["unchanged_fetchers"], ["rss"])
        self.assertIn("已更新 20 条", summary["message"])
        self.assertIn("1 个来源未变化并复用缓存", summary["message"])
        self.assertIn("1 个来源失败", summary["message"])

    def test_build_fetcher_summary_preserves_structured_status(self):
        collector = CollectorService()
        result = FetchResult(
            items=[{"link": "https://example.com/x"}],
            status="[1 个源未变化(304)]",
            source_status={
                "outcome": "unchanged",
                "used_cache": True,
                "fetched_count": 1,
                "not_modified": True,
            },
        )

        summary = collector._build_fetcher_summary("rss", result)

        self.assertEqual(summary["outcome"], "unchanged")
        self.assertTrue(summary["used_cache"])
        self.assertEqual(summary["fetched_count"], 1)
        self.assertEqual(summary["status"], "[1 个源未变化(304)]")
        self.assertNotIn("error", summary)


if __name__ == "__main__":
    unittest.main()
