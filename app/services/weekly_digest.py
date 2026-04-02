"""
本周中文综述：基于当前 Feed 缓存条目，可选 LLM 生成；无 Key 时用统计兜底。
按「自然周 + 条目链接指纹」去重缓存，避免每次刷新都打模型。
"""
import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .llm_summary import LLMConfig, LLMProvider, LLMSummaryService

_digest_state: Dict[str, Any] = {
    "fingerprint": "",
    "text": "",
    "generated_at": None,
    "from_llm": False,
}


def _weekly_fingerprint(items: List[dict]) -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    week_key = f"{iso[0]}-W{iso[1]:02d}"
    links = sorted({(it.get("link") or "").strip() for it in items if it.get("link")})
    payload = "\n".join(links[:56])
    h = hashlib.md5(payload.encode("utf-8")).hexdigest()[:24]
    return f"{week_key}:{h}"


def _material_block(items: List[dict], max_n: int = 32) -> str:
    lines = []
    for i, it in enumerate(items[:max_n], 1):
        typ = it.get("type") or "?"
        title = (it.get("title") or "").replace("\n", " ")[:220]
        venue = (it.get("venue") or "").replace("\n", " ")[:80]
        date = it.get("date") or ""
        desc = (it.get("desc") or "").replace("\n", " ")[:180]
        lines.append(f"{i}. [{typ}] {title}\n   来源: {venue} | 日期: {date}\n   简介: {desc}")
    return "\n".join(lines)


def _fallback_zh(items: List[dict]) -> str:
    """无 LLM 时不重复词频区的长列表，只说明能力与配置方式。"""
    if not items:
        return "暂无条目。请先点击「抓取最新」。"
    return (
        f"当前共 {len(items)} 条线索。未配置 LLM API Key，此处不生成长文综述，避免与下方「高频词」重复。\n\n"
        "请查看下方英文关键词（来自标题/摘要词频）并点击筛选卡片；"
        "配置 OPENAI_API_KEY、ANTHROPIC_API_KEY 或 MODELVERSE_API_KEY 后，将在此处自动生成一段中文周评。"
    )


def _make_llm_service() -> Optional[LLMSummaryService]:
    cfg = LLMConfig()
    if cfg.api_key:
        return LLMSummaryService(
            LLMConfig(
                provider=cfg.provider,
                api_key=cfg.api_key,
                model=os.getenv("OPENAI_MODEL", cfg.model),
                max_tokens=2048,
                temperature=0.35,
            )
        )
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLMSummaryService(
            LLMConfig(
                provider=LLMProvider.CLAUDE,
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022"),
                max_tokens=2048,
                temperature=0.35,
            )
        )
    if os.getenv("MODELVERSE_API_KEY"):
        return LLMSummaryService(
            LLMConfig(
                provider=LLMProvider.MODELVERSE,
                api_key=os.getenv("MODELVERSE_API_KEY", ""),
                model=os.getenv("MODELVERSE_MODEL", "qwen-turbo"),
                max_tokens=2048,
                temperature=0.35,
            )
        )
    return None


async def get_or_create_weekly_zh_digest(items: List[dict]) -> Dict[str, Any]:
    """返回 { text, cached, from_llm, generated_at, llm_configured }。"""
    global _digest_state
    if not items:
        return {
            "text": None,
            "cached": False,
            "from_llm": False,
            "generated_at": None,
            "llm_configured": _make_llm_service() is not None,
            "error": "empty_feed",
        }

    fp = _weekly_fingerprint(items)
    if (
        _digest_state.get("fingerprint") == fp
        and _digest_state.get("text")
        and len(str(_digest_state.get("text"))) > 20
    ):
        return {
            "text": _digest_state["text"],
            "cached": True,
            "from_llm": bool(_digest_state.get("from_llm")),
            "generated_at": _digest_state.get("generated_at"),
            "llm_configured": bool(_digest_state.get("from_llm"))
            or _make_llm_service() is not None,
        }

    service = _make_llm_service()
    llm_ok = service is not None
    material = _material_block(items, 32)
    text: Optional[str] = None
    from_llm = False

    if service:
        try:
            text = await service.generate_weekly_digest_chinese(material)
        finally:
            await service.close()

    if text and len(text.strip()) > 80:
        from_llm = True
    else:
        text = _fallback_zh(items)
        from_llm = False

    now_iso = datetime.now(timezone.utc).isoformat()
    _digest_state = {
        "fingerprint": fp,
        "text": text,
        "generated_at": now_iso,
        "from_llm": from_llm,
    }

    return {
        "text": text,
        "cached": False,
        "from_llm": from_llm,
        "generated_at": now_iso,
        "llm_configured": llm_ok,
    }


def invalidate_weekly_digest_cache() -> None:
    """Feed 结构与周次无关时如需强制刷新可调用（一般靠指纹即可）。"""
    global _digest_state
    _digest_state = {
        "fingerprint": "",
        "text": "",
        "generated_at": None,
        "from_llm": False,
    }
