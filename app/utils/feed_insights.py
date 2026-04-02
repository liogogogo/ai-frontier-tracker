"""
基于当前 Feed 条目的轻量提炼（词频 / 标签 / 来源），无需 LLM。
"""
from collections import Counter
from typing import Any, Dict, List, Optional

from .text_analysis import analyze_articles


def build_feed_insights(
    items: Optional[List[Dict[str, Any]]],
    top_keywords: int = 16,
    top_phrases: int = 8,
) -> Dict[str, Any]:
    """
    从已与用户见面的列表（排序后条目）生成结构化要点，供「本期提炼」展示。
    """
    if not items:
        return {
            "empty": True,
            "headline": "暂无条目，请先抓取或刷新。",
            "bullets": [],
            "keywords": [],
            "phrases": [],
            "type_mix": {},
            "venues_top": [],
            "tag_counts": [],
            "note": "更深层的综述可配合「分析」Tab（库内历史）或配置 LLM 后使用 /api/summarize。",
        }

    analysis = analyze_articles(items, top_k=max(36, top_keywords * 2))

    keywords = [
        {"word": w, "count": int(c)}
        for w, c in (analysis.get("word_frequency") or [])[:top_keywords]
    ]
    phrases = [
        {"phrase": p, "count": int(c)}
        for p, c in (analysis.get("ngram_frequency") or [])[:top_phrases]
    ]

    venues = Counter()
    tag_c = Counter()
    for it in items:
        v = it.get("venue") or ""
        if v:
            venues[v] += 1
        for t in it.get("tags") or []:
            if isinstance(t, str) and t:
                tag_c[t.lower()] += 1

    venues_top = [{"venue": v, "count": n} for v, n in venues.most_common(8)]
    tag_top = [{"tag": t, "count": n} for t, n in tag_c.most_common(10)]

    types = analysis.get("type_distribution") or {}
    kw_words = [x["word"] for x in keywords[:6]]
    kw_snip = "、".join(kw_words) if kw_words else "—"
    headline = (
        f"本批共 {len(items)} 条；标题/摘要里反复出现的技术向词汇包括：{kw_snip} 等。"
    )

    bullets: List[str] = []
    if phrases:
        bullets.append(
            "短语线索：" + " · ".join(p["phrase"] for p in phrases[:5])
        )
    if tag_top:
        bullets.append(
            "标签分布："
            + " · ".join(f"「{t['tag']}」×{t['count']}" for t in tag_top[:6])
        )
    if venues_top:
        bullets.append(
            "来源摘要："
            + " · ".join(
                f"「{v['venue']}」{v['count']}条" for v in venues_top[:5]
            )
        )

    return {
        "empty": False,
        "headline": headline,
        "bullets": bullets,
        "keywords": keywords,
        "phrases": phrases,
        "type_mix": types,
        "venues_top": venues_top,
        "tag_counts": tag_top,
        "date_range": analysis.get("date_range"),
        "note": "统计提炼基于英文标题/摘要词频；综述级摘要需 LLM 或对单篇使用「深入解读」。「分析」Tab 可按入库历史做大范围词频与趋势。",
    }
