"""
GitHub 抓取器
"""
from typing import Optional

from .base import BaseFetcher, FetchResult
from .registry import register_fetcher
from ..services.http_client import RetryableHTTPClient
from ..config import CONFIG
from ..utils.text import infer_tags


GITHUB_QUERIES = [
    "llm inference stars:>2000",
    "vllm OR tensorrt-llm OR sglang stars:>1500",
    "langchain OR langgraph stars:>800",
    "llama.cpp OR ggml OR gguf stars:>1200",
    "PagedAttention OR continuous batching llm stars:>200",
    "torch.distributed OR FSDP OR DeepSpeed stars:>600",
    "unsloth OR axolotl OR qlora finetrain stars:>350",
]


@register_fetcher("github")
class GitHubFetcher(BaseFetcher):
    """GitHub仓库抓取器"""
    
    def __init__(self, config=None):
        super().__init__(config or CONFIG.github)
        self.base_url = "https://api.github.com/search/repositories"
    
    async def fetch(self, cursor: Optional[str] = None) -> FetchResult:
        """搜索热门AI工程仓库"""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        
        async with RetryableHTTPClient(
            base_headers=headers,
            max_retries=self.config.max_retries,
            retry_delay=self.config.retry_delay,
            timeout=self.config.timeout,
            rate_limit_delay=0.3,
        ) as client:
            
            all_items = []
            seen = set()
            
            for query in GITHUB_QUERIES:
                try:
                    items = await self._search_repos(client, query, seen)
                    all_items.extend(items)
                except Exception as e:
                    # 记录但继续
                    continue
            
            return FetchResult(items=all_items[:22])
    
    async def _search_repos(self, client: RetryableHTTPClient, query: str, seen: set) -> list:
        """搜索仓库"""
        params = {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": 4,
        }
        
        response = await client.get(self.base_url, params=params)
        data = response.json()
        
        items = []
        for repo in data.get("items", []):
            full = repo.get("full_name") or ""
            if full in seen:
                continue
            seen.add(full)
            
            desc = (repo.get("description") or "").strip()
            if not desc:
                desc = "GitHub 开源项目"
            desc = desc[:320]
            
            updated = repo.get("updated_at") or ""
            date_str = updated[:10] if updated else ""
            
            lang = repo.get("language") or ""
            text = f"{full} {desc} {lang}"
            tags = infer_tags(text)
            
            if "inference" not in tags and "vllm" in full.lower():
                tags = list(dict.fromkeys(tags + ["inference"]))
            
            stars = int(repo.get("stargazers_count") or 0)
            forks = int(repo.get("forks_count") or repo.get("forks") or 0)
            # watchers_count 在 github.com 上多为 stargazers 别名，不用；issue 作参与度补充
            open_issues = int(repo.get("open_issues_count") or 0)

            items.append({
                "type": "repo",
                "title": full,
                "desc": desc,
                "tags": tags,
                "date": date_str,
                "venue": "GitHub",
                "link": repo.get("html_url") or f"https://github.com/{full}",
                "heat": 0,
                "_gh_stars": stars,
                "_gh_forks": forks,
                "_gh_open_issues": open_issues,
            })
        
        return items
