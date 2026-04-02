"""
热度计算工具
"""
import math
import re
from datetime import datetime, timezone
from typing import Optional, Set, FrozenSet

# 渠道分类
BIG_TECH_RSS_VENUES: FrozenSet[str] = frozenset({
    "OpenAI Blog",
    "Google / DeepMind Blog",
    "DeepMind",
    "Google AI Blog",
    "Google Cloud — AI & ML",
    "Google Developers — AI",
    "Hugging Face Blog",
    "NVIDIA Blog",
    "AWS Machine Learning Blog",
    "Microsoft AI Blog",
    "Meta Engineering",
    "Anthropic News",
    "xAI News",
})

SILICON_VALLEY_MEDIA_VENUES: FrozenSet[str] = frozenset({"TechCrunch — AI"})

BIG_TECH_REPO = re.compile(
    r"^(meta-ai|facebookresearch|google|google-research|openai|microsoft|nvidia|apple|anthropic|x-ai|deepmind|langchain-ai|aws-samples|torch)/",
    re.I,
)


def days_since_iso(date_str: Optional[str]) -> float:
    """计算距离今天的天数"""
    if not date_str or len(str(date_str)) < 10:
        return 45.0
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return max(0.0, float((datetime.now(timezone.utc).date() - d).days))
    except ValueError:
        return 45.0


def recency_uplift(days: float) -> float:
    """越新越接近1，约90天降至~0.1"""
    return max(0.1, 1.0 - min(days, 120.0) / 130.0)


def engineering_topic_score(text: str) -> int:
    """工程/LLM关键词加分"""
    t = (text or "").lower()
    acc = 0
    
    patterns = [
        (r"\bllm\b", 20),
        (r"language model", 20),
        (r"inference", 18),
        (r"quantiz", 14),
        (r"\brag\b|retriev", 16),
        (r"\bmo[eE]\b|mixture of expert", 15),
        (r"distributed|training at scale", 14),
        (r"flash.?attn|kv.?cache|paged.?attention", 16),
        (r"\bgpu\b|cuda|triton", 12),
        (r"transformer", 10),
        (r"\bagent\b|tool use", 12),
    ]
    
    for pat, w in patterns:
        if re.search(pat, t):
            acc += w
    
    return min(100, acc)


def finalize_heat(item: dict) -> None:
    """计算最终热度"""
    v = item.get("venue") or ""
    typ = item.get("type") or ""
    title = item.get("title") or ""
    desc = item.get("desc") or ""
    blob = f"{title} {desc}"
    link_l = (item.get("link") or "").lower()
    
    days = days_since_iso(item.get("date"))
    rec = recency_uplift(days)
    topic = engineering_topic_score(blob)
    
    bd = {
        "days_old": round(days, 1),
        "recency_uplift": round(rec, 3),
        "topic_bonus": topic,
    }
    
    # 根据类型计算热度
    if typ == "paper" or "arxiv.org/abs" in link_l:
        base = 125
        rec_pts = int(185 * rec)
        total = base + topic + rec_pts
        bd.update({
            "channel": "academic_preprint",
            "base": base,
            "recency_points": rec_pts,
        })
    
    elif v == "Hacker News":
        pts = int(item.get("_hn_points", 0))
        cm = int(item.get("_hn_comments", 0))
        eng_raw = math.log1p(max(pts, 0)) * 46 + math.log1p(min(max(cm, 0), 450)) * 30
        eng = min(240, eng_raw)
        ch = 265
        total = int(ch + eng + int(138 * rec) + min(24, topic // 4))
        bd.update({
            "channel": "hackernews",
            "hn_points": pts,
            "hn_comments": cm,
            "engagement_capped": round(eng, 1),
            "engagement_raw_log": round(eng_raw, 1),
            "channel_floor": ch,
        })
    
    elif isinstance(v, str) and v.startswith("Reddit"):
        sc = int(item.get("_reddit_score", 0))
        cm = int(item.get("_reddit_comments", 0))
        eng_raw = math.log1p(max(sc, 0)) * 44 + math.log1p(min(max(cm, 0), 280)) * 27
        eng = min(236, eng_raw)
        ch = 228
        total = int(ch + eng + int(128 * rec) + min(22, topic // 4))
        bd.update({
            "channel": "reddit",
            "reddit_score": sc,
            "reddit_comments": cm,
            "engagement_capped": round(eng, 1),
            "engagement_raw_log": round(eng_raw, 1),
            "channel_floor": ch,
        })
    
    elif v == "Lobsters":
        sc = int(item.get("_lob_score", 0))
        cm = int(item.get("_lob_comments", 0))
        eng_raw = math.log1p(max(sc, 0)) * 50 + math.log1p(min(max(cm, 0), 160)) * 34
        eng = min(198, eng_raw)
        ch = 205
        total = int(ch + eng + int(118 * rec) + min(20, topic // 5))
        bd.update({
            "channel": "lobsters",
            "lob_score": sc,
            "lob_comments": cm,
            "engagement_capped": round(eng, 1),
            "channel_floor": ch,
        })
    
    elif v == "GitHub":
        stars = int(item.get("_gh_stars", 0))
        forks = int(item.get("_gh_forks", 0))
        issues = int(item.get("_gh_open_issues", 0))

        # Star：主量化指标（log10 压长尾，与原先尺度衔接）
        star_raw = math.log10(max(stars, 1) + 1.0) * 86
        star_pts = min(188, star_raw)

        # Fork：参与/二次开发热度
        fork_raw = math.log1p(max(forks, 0)) * 42
        fork_pts = min(112, fork_raw)

        # Open issues：讨论与维护活跃度（上限防异常仓库刷数）
        iss_capped = min(max(issues, 0), 8000)
        issue_raw = math.log1p(iss_capped) * 20
        issue_pts = min(58, issue_raw)

        ch = 198
        org_b = 88 if BIG_TECH_REPO.match(title.strip()) else 0
        total = int(
            ch
            + star_pts
            + fork_pts
            + issue_pts
            + org_b
            + int(100 * rec)
            + min(24, topic // 4)
        )
        bd.update({
            "channel": "github",
            "stars": stars,
            "forks": forks,
            "open_issues": issues,
            "star_signal": round(star_pts, 1),
            "fork_signal": round(fork_pts, 1),
            "issues_signal": round(issue_pts, 1),
            "org_repo_boost": org_b,
            "channel_floor": ch,
        })
    
    elif v in BIG_TECH_RSS_VENUES:
        ch = 415
        total = int(ch + int(205 * rec) + int(topic * 0.32))
        bd.update({
            "channel": "bigtech_official_rss",
            "channel_floor": ch,
            "recency_points": int(205 * rec),
        })
    
    elif v in SILICON_VALLEY_MEDIA_VENUES:
        ch = 295
        total = int(ch + int(175 * rec) + int(topic * 0.28))
        bd.update({
            "channel": "tech_press_ai",
            "channel_floor": ch,
        })
    
    else:
        ch = 175
        total = int(ch + int(130 * rec) + int(topic * 0.25))
        bd.update({
            "channel": "rss_general",
            "channel_floor": ch,
        })
    
    total = max(1, min(999, int(total)))
    item["heat"] = total
    bd["total"] = total
    item["heat_breakdown"] = bd
