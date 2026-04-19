"""
信号聚合分析服务

提供三类高置信度信号：
  1. entities   — 实体词频 + 动量（velocity）+ 跨源分
  2. convergence — 同一 arXiv ID 被 paper/repo/news 三类来源同时覆盖的"收敛信号卡"
  3. emergence  — 近 N 天首次出现、动量加速的实体（早期信号）

全部为纯计算函数，不依赖外部 API。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..utils.entity_dict import ENTITY_META


# ─────────────────────────────────────────────────────────────────
# 工具：从 Article.raw_data 安全读取字段
# ─────────────────────────────────────────────────────────────────

def _read_raw(raw_data: Optional[str], key: str) -> Any:
    if not raw_data:
        return None
    try:
        d = json.loads(raw_data)
        return d.get(key)
    except Exception:
        return None


def _article_to_dict(a: Any) -> Dict[str, Any]:
    """ORM Article → plain dict（含 raw_data 中的 entities/arxiv_id）"""
    try:
        tags = json.loads(a.tags or "[]")
    except Exception:
        tags = []
    return {
        "id": a.id,
        "title": a.title or "",
        "link": a.link or "",
        "type": a.type or "news",
        "date": a.date or "",
        "venue": a.venue or "",
        "heat": int(a.heat or 0),
        "tags": tags,
        "entities": _read_raw(a.raw_data, "entities") or [],
        "arxiv_id": _read_raw(a.raw_data, "arxiv_id"),
    }


# ─────────────────────────────────────────────────────────────────
# 1. 实体聚合 + 动量
# ─────────────────────────────────────────────────────────────────

def _days_ago(date_str: str, now: datetime) -> float:
    """article.date (YYYY-MM-DD) 距今天数"""
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return max(0.0, (now - d).total_seconds() / 86400)
    except Exception:
        return 999.0


def aggregate_entities(
    recent_articles: List[Dict[str, Any]],
    historical_articles: List[Dict[str, Any]],
    top_k: int = 40,
    category_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    对两段时间窗内的文章分别统计实体出现情况，计算：
      - mention_count   : 近期文章命中次数
      - heat_sum        : 命中文章的热度加权和
      - velocity        : mention_count_recent / mention_count_historical（基线=1.0）
      - source_types    : 命中的 article type 集合（paper/repo/news）
      - top_articles    : 近期命中度最高的 3 篇文章

    velocity > 1.5 → 加速中；< 0.7 → 降温
    """
    now = datetime.utcnow()

    # 近期统计
    recent_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "mention_count": 0,
        "heat_sum": 0,
        "source_types": set(),
        "top_articles": [],
    })

    for art in recent_articles:
        for eid in (art.get("entities") or []):
            meta = ENTITY_META.get(eid)
            if not meta:
                continue
            if category_filter and meta["category"] != category_filter:
                continue
            s = recent_stats[eid]
            s["mention_count"] += 1
            s["heat_sum"] += art.get("heat", 0)
            s["source_types"].add(art.get("type") or "news")
            s["top_articles"].append({
                "title": art.get("title", ""),
                "link": art.get("link", ""),
                "heat": art.get("heat", 0),
                "date": art.get("date", ""),
                "type": art.get("type", ""),
            })

    # 历史统计（仅用于 velocity 分母）
    hist_counts: Dict[str, int] = defaultdict(int)
    for art in historical_articles:
        for eid in (art.get("entities") or []):
            meta = ENTITY_META.get(eid)
            # 如果指定了 category_filter，跳过不匹配的实体
            if category_filter:
                if not meta or meta["category"] != category_filter:
                    continue
            # 没有 meta 的实体不计入（避免未知实体污染统计）
            elif not meta:
                continue
            hist_counts[eid] += 1

    # 组装结果
    results: List[Dict[str, Any]] = []
    for eid, s in recent_stats.items():
        meta = ENTITY_META.get(eid)
        if not meta:
            continue
        hist = hist_counts.get(eid, 0)
        # velocity: 近期频率 / 历史频率（对历史做时间归一）
        if hist > 0:
            velocity = round(s["mention_count"] / hist, 2)
        else:
            velocity = round(min(5.0, s["mention_count"] * 1.5), 2)  # 历史无记录 → 涌现

        # 跨源分：命中类型数 (1/2/3) → 加权系数
        source_count = len(s["source_types"])
        cross_source_score = {1: 1.0, 2: 2.5, 3: 5.0}.get(source_count, 1.0)

        # 综合得分：以 heat_sum 为主体，velocity 和跨源加成
        composite = (
            math.log1p(s["heat_sum"]) * 1.0
            + math.log1p(s["mention_count"]) * 2.0
            + velocity * 1.5
            + cross_source_score * 1.2
        )

        # top_articles 按 heat 降序取 top-3
        top3 = sorted(s["top_articles"], key=lambda x: -x["heat"])[:3]

        results.append({
            "id": eid,
            "display": meta["display"],
            "category": meta["category"],
            "mention_count": s["mention_count"],
            "heat_sum": s["heat_sum"],
            "velocity": velocity,
            "source_types": sorted(s["source_types"]),
            "cross_source_score": cross_source_score,
            "composite_score": round(composite, 2),
            "top_articles": top3,
            "trend": (
                "rising" if velocity >= 1.5 else
                "cooling" if velocity < 0.7 else
                "stable"
            ),
        })

    results.sort(key=lambda x: -x["composite_score"])
    return results[:top_k]


# ─────────────────────────────────────────────────────────────────
# 2. 收敛信号卡（Convergence Cards）
# ─────────────────────────────────────────────────────────────────

def build_convergence_cards(
    articles: List[Dict[str, Any]],
    min_source_types: int = 2,
    top_k: int = 20,
) -> List[Dict[str, Any]]:
    """
    以 arXiv ID 为 key，把 paper/repo/news 三类来源关联起来。
    当同一 arXiv ID 被 ≥ min_source_types 种来源覆盖时，生成收敛信号卡。

    三角关联分 = source_type 种类数 × max_heat_item.heat × log1p(total_heat)
    """
    by_arxiv: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "by_type": defaultdict(list),
        "total_heat": 0,
        "max_heat": 0,
        "dates": [],
    })

    for art in articles:
        aid = art.get("arxiv_id")
        if not aid:
            continue
        atype = art.get("type") or "news"
        heat = art.get("heat", 0)
        bucket = by_arxiv[aid]
        bucket["by_type"][atype].append(art)
        bucket["total_heat"] += heat
        bucket["max_heat"] = max(bucket["max_heat"], heat)
        if art.get("date"):
            bucket["dates"].append(art["date"])

    cards: List[Dict[str, Any]] = []
    for arxiv_id, bucket in by_arxiv.items():
        types_present = list(bucket["by_type"].keys())
        if len(types_present) < min_source_types:
            continue

        type_count = len(types_present)
        score = (
            type_count * 3.0
            + math.log1p(bucket["total_heat"]) * 0.8
            + (5.0 if type_count >= 3 else 0.0)   # 三角全命中额外加分
        )

        # 找最佳代表条目（热度最高的 paper 优先）
        paper_items = bucket["by_type"].get("paper", [])
        anchor = (
            max(paper_items, key=lambda x: x.get("heat", 0))
            if paper_items
            else max(
                (a for items in bucket["by_type"].values() for a in items),
                key=lambda x: x.get("heat", 0),
            )
        )

        # 每种类型取热度最高一条作为证据
        evidence: Dict[str, Any] = {}
        for atype, items in bucket["by_type"].items():
            best = max(items, key=lambda x: x.get("heat", 0))
            evidence[atype] = {
                "title": best.get("title", ""),
                "link": best.get("link", ""),
                "venue": best.get("venue", ""),
                "heat": best.get("heat", 0),
                "date": best.get("date", ""),
            }

        cards.append({
            "arxiv_id": arxiv_id,
            "arxiv_url": f"https://arxiv.org/abs/{arxiv_id}",
            "title": anchor.get("title", ""),
            "source_types": sorted(types_present),
            "type_count": type_count,
            "total_heat": bucket["total_heat"],
            "convergence_score": round(score, 2),
            "latest_date": max(bucket["dates"]) if bucket["dates"] else "",
            "evidence": evidence,
        })

    cards.sort(key=lambda x: -x["convergence_score"])
    return cards[:top_k]


# ─────────────────────────────────────────────────────────────────
# 3. 涌现检测（Emergence）
# ─────────────────────────────────────────────────────────────────

def detect_emergence(
    recent_articles: List[Dict[str, Any]],
    historical_articles: List[Dict[str, Any]],
    top_k: int = 20,
    min_recent_mentions: int = 2,
) -> List[Dict[str, Any]]:
    """
    检测近期首次出现（历史中未见）或加速度 > 2 的实体。

    emergence_score = mention_count_recent × heat_sum_recent（对历史=0时额外加分）
    """
    hist_eids: set = set()
    for art in historical_articles:
        for eid in (art.get("entities") or []):
            hist_eids.add(eid)

    recent_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "mention_count": 0, "heat_sum": 0, "articles": [],
    })
    for art in recent_articles:
        for eid in (art.get("entities") or []):
            s = recent_stats[eid]
            s["mention_count"] += 1
            s["heat_sum"] += art.get("heat", 0)
            s["articles"].append(art)

    results: List[Dict[str, Any]] = []
    for eid, s in recent_stats.items():
        if s["mention_count"] < min_recent_mentions:
            continue
        meta = ENTITY_META.get(eid)
        if not meta:
            continue
        is_new = eid not in hist_eids
        emergence_score = (
            s["mention_count"] * math.log1p(s["heat_sum"])
            * (2.0 if is_new else 1.0)
        )
        top3 = sorted(s["articles"], key=lambda x: -x.get("heat", 0))[:3]
        results.append({
            "id": eid,
            "display": meta["display"],
            "category": meta["category"],
            "is_new": is_new,
            "mention_count": s["mention_count"],
            "heat_sum": s["heat_sum"],
            "emergence_score": round(emergence_score, 2),
            "top_articles": [
                {"title": a.get("title", ""), "link": a.get("link", ""),
                 "heat": a.get("heat", 0), "date": a.get("date", ""),
                 "type": a.get("type", "")}
                for a in top3
            ],
        })

    results.sort(key=lambda x: -x["emergence_score"])
    return results[:top_k]
