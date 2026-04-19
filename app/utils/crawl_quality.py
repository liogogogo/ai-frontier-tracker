"""
Crawl4AI / 正文提取后的质量辅助：清洗噪声、按启发式评分选最优文本。

不引入新外部依赖；供 app/services/firecrawl.py 的 Crawl4AI 路径使用。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# 常见导航/页脚/合规噪声（小写）
_NOISE_SUBSTRINGS = (
    "cookie",
    "cookies",
    "privacy policy",
    "terms of service",
    "terms of use",
    "subscribe",
    "newsletter",
    "sign in",
    "sign up",
    "log in",
    "follow us",
    "share on",
    "related articles",
    "you may also like",
    "advertisement",
    "我们使用 cookie",
    "隐私政策",
    "服务条款",
    "订阅",
    "邮件订阅",
)

# 过短且像导航的单行（小写整行匹配）
_NOISE_EXACT = frozenset({
    "home", "about", "contact", "blog", "careers", "pricing", "docs",
    "documentation", "search", "menu", "skip to content",
})


def clean_markdown_noise(text: str) -> str:
    """去掉常见模板噪声行，合并多余空行。"""
    if not text:
        return ""
    s = text.replace("\r", "\n")
    lines = s.split("\n")
    out: List[str] = []
    for ln in lines:
        t = ln.strip()
        if not t:
            continue
        low = t.lower()
        # 整行过短且像导航
        if len(low) <= 32 and low in _NOISE_EXACT:
            continue
        # 典型噪声子串（整行较短时）
        if len(low) < 140:
            if any(ns in low for ns in _NOISE_SUBSTRINGS):
                continue
        # 纯链接行「Read more →」类
        if len(t) < 50 and re.match(r"^(read more|continue|下一页|更多).*$", low):
            continue
        out.append(t)
    merged = "\n".join(out)
    while "\n\n\n" in merged:
        merged = merged.replace("\n\n\n", "\n\n")
    return merged.strip()


def score_text_quality(text: str) -> float:
    """
    启发式质量分：偏好「像正文」的长段落，惩罚过短行堆叠与低字母占比。
    """
    if not text:
        return 0.0
    t = text.strip()
    n = len(t)
    if n < 80:
        return float(n) * 0.2
    # 字母数字占比
    alnum = sum(1 for c in t if c.isalnum())
    ratio = alnum / max(n, 1)
    # 长行比例（>60 字符的行更有可能是正文）
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    long_lines = sum(1 for ln in lines if len(ln) >= 60)
    long_ratio = long_lines / max(len(lines), 1)
    # 惩罚：过多极短行
    short_ratio = sum(1 for ln in lines if len(ln) < 20) / max(len(lines), 1)
    score = (
        n * 0.35
        + ratio * 400.0
        + long_ratio * 500.0
        - short_ratio * 300.0
    )
    return score


def domain_crawl_hints(url: str) -> Dict[str, Any]:
    """
    按域名给出优先 css_selector、可选 wait_for、建议 delay。
    Crawl4AI 的 smart_wait 支持 CSS 选择器字符串。
    """
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        host = ""

    # 默认：先主内容区域，再退回 body
    hints: Dict[str, Any] = {
        "selectors_priority": [
            "main article",
            "article",
            "main",
            '[role="main"]',
            "#content",
            ".post-content",
            ".article-content",
            ".entry-content",
            ".markdown-body",
        ],
        "wait_for": None,
        "extra_delay": 0.0,
    }

    if "github.com" in host:
        hints["selectors_priority"] = [
            "article.markdown-body",
            ".markdown-body",
            "#readme",
            "turbo-frame#readme",
            "article",
            "main",
        ]
        hints["wait_for"] = ".markdown-body, article.markdown-body, #readme"
        hints["extra_delay"] = 0.5

    elif "arxiv.org" in host:
        hints["selectors_priority"] = [
            "blockquote.abstract",
            ".abstract",
            "article",
            "main",
        ]
        hints["wait_for"] = "blockquote.abstract, .abstract, main"
        hints["extra_delay"] = 0.2

    elif "medium.com" in host or "towardsdatascience.com" in host:
        hints["selectors_priority"] = [
            "article",
            "section",
            "main",
        ]
        hints["wait_for"] = "article"
        hints["extra_delay"] = 0.8

    elif "substack.com" in host:
        hints["selectors_priority"] = [
            ".post-content",
            "article",
            "main",
        ]
        hints["wait_for"] = ".post-content, article"
        hints["extra_delay"] = 0.7

    return hints


def pick_best_candidate(candidates: List[Tuple[str, str, float]]) -> Optional[Dict[str, Any]]:
    """
    candidates: (label, markdown, score)
    返回 { markdown, label, score } 最优项。
    """
    if not candidates:
        return None
    candidates = [(a, b, float(c)) for a, b, c in candidates if b and b.strip()]
    if not candidates:
        return None
    best = max(candidates, key=lambda x: x[2])
    return {"markdown": best[1], "label": best[0], "score": best[2]}
