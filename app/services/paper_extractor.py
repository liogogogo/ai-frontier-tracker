"""
论文摘要结构化抽取服务（可选 LLM）

参考 karpathy/jobs 的"可复现评分流水线"：
  - 同一 rubric，强约束 JSON 输出
  - 增量缓存到 Article.raw_data["paper_struct"]（中断可续）
  - 无 LLM Key 时静默跳过，不影响主流程

输出 schema:
  {
    "problem":    "...",   # 解决什么问题（1句话）
    "method":     "...",   # 核心技术方法
    "key_metric": "...",   # 主要评测结果（含指标名和数值）
    "impl_url":   "...",   # 代码链接（摘要中有则填，否则空字符串）
    "novelty":    1-5,     # 新颖度：1=增量改进，5=范式突破
    "one_liner":  "..."    # 给工程师的一句话价值总结（中文）
  }
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


STRUCT_FIELD = "paper_struct"   # 存入 Article.raw_data 的 key

SYSTEM_PROMPT_PAPER_STRUCT = """\
You are an expert AI/ML researcher reviewing papers for software engineers \
who want to quickly understand and implement new ideas.

Given a paper's title and abstract, extract the following information and \
respond ONLY with a JSON object (no other text, no markdown):

{
  "problem":    "<1-sentence description of the core problem being solved>",
  "method":     "<key technical approach or contribution, ≤20 words>",
  "key_metric": "<primary evaluation result with metric name and value, e.g. '+3.2% on MMLU'>",
  "impl_url":   "<code/repo URL if mentioned in abstract, else empty string>",
  "novelty":    <integer 1-5, where 1=incremental improvement, 5=paradigm shift>,
  "one_liner":  "<1-sentence value summary for an engineer in Chinese>"
}

If a field cannot be determined from the abstract, use an empty string for text \
fields and 3 for novelty.
"""


def get_cached_struct(raw_data: Optional[str]) -> Optional[Dict[str, Any]]:
    """从 Article.raw_data 读取已缓存的结构化抽取结果"""
    if not raw_data:
        return None
    try:
        return json.loads(raw_data).get(STRUCT_FIELD)
    except Exception:
        return None


def set_cached_struct(existing_raw: Optional[str], struct: Dict[str, Any]) -> str:
    """将结构化结果合并写入 Article.raw_data"""
    try:
        d = json.loads(existing_raw) if existing_raw else {}
    except Exception:
        d = {}
    d[STRUCT_FIELD] = struct
    return json.dumps(d, ensure_ascii=False)


async def extract_paper_struct(
    article: Dict[str, Any],
    llm_service: Any,
) -> Optional[Dict[str, Any]]:
    """
    用 LLM 对单篇论文做结构化抽取。
    失败/无 key 时返回 None（调用方跳过即可）。
    """
    if llm_service is None or not getattr(llm_service.config, "api_key", ""):
        return None

    title = (article.get("title") or "").strip()
    desc  = (article.get("desc")  or "").strip()
    if not title:
        return None

    user_content = f"Title: {title}\n\nAbstract: {desc[:1000]}"

    try:
        raw = await llm_service.chat_completion(
            system=SYSTEM_PROMPT_PAPER_STRUCT,
            user=user_content,
            max_tokens=400,
        )
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw.strip())
        return {
            "problem":    str(result.get("problem") or ""),
            "method":     str(result.get("method") or ""),
            "key_metric": str(result.get("key_metric") or ""),
            "impl_url":   str(result.get("impl_url") or ""),
            "novelty":    max(1, min(5, int(result.get("novelty") or 3))),
            "one_liner":  str(result.get("one_liner") or ""),
        }
    except Exception:
        return None


async def extract_batch(
    articles: List[Any],   # List[Article] ORM 对象，仅处理 type=="paper"
    llm_service: Any = None,
    force: bool = False,
    limit: int = 100,
) -> Tuple[int, int]:
    """
    批量抽取论文结构化信息，结果缓存到 Article.raw_data。
    返回 (extracted_count, skipped_count)。
    """
    extracted = 0
    skipped = 0

    for art in articles[:limit]:
        if art.type != "paper":
            skipped += 1
            continue
        if not force and get_cached_struct(art.raw_data) is not None:
            skipped += 1
            continue

        art_dict = {
            "title": art.title,
            "desc": art.desc,
        }
        result = await extract_paper_struct(art_dict, llm_service)
        if result is None:
            skipped += 1
            continue

        art.raw_data = set_cached_struct(art.raw_data, result)
        extracted += 1

    return extracted, skipped
