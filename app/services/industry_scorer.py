"""
AI Agent 工程行业评分服务

参考 karpathy/jobs 的精髓：
  - 先定义覆盖完整的"行业 taxonomy"（子领域 + 关键词锚点 + 权重）
  - 对每条内容用统一 rubric 打分（规则先行；LLM 可选增强）
  - 输出强约束 JSON（score 0-10 + domain + evidence_spans + rationale）
  - 增量缓存到 DB（中断可续）；API 只读结果

行业聚焦：AI Agent 工程
  目标读者：做 Agent 系统的工程师/架构师/创业者
  核心问题：这篇内容对"搭建/优化/评测/运营一个 Agent 系统"有多大价值？
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────
# AI Agent 工程行业 Taxonomy
# 每个子领域：(label_cn, label_en, keywords, weight)
#   weight：该子领域对"Agent 工程落地"的相对重要程度（1.0 = 基线）
# ─────────────────────────────────────────────────────────────────

@dataclass
class SubDomain:
    id: str
    label_cn: str
    label_en: str
    keywords: List[str]          # 精确匹配词（小写，带 \b 边界）
    patterns: List[str]          # 额外正则 pattern（无需 \b）
    weight: float = 1.0          # 该子领域对行业的重要程度
    _compiled: List[Any] = field(default_factory=list, repr=False)

    def compile(self) -> "SubDomain":
        pats = [rf"\b{re.escape(k)}\b" for k in self.keywords] + self.patterns
        self._compiled = [re.compile(p, re.I) for p in pats]
        return self

    def match_count(self, text: str) -> int:
        return sum(1 for p in self._compiled if p.search(text))


AGENT_TAXONOMY: List[SubDomain] = [
    SubDomain(
        id="orchestration",
        label_cn="Agent 编排与框架",
        label_en="Agent Orchestration & Frameworks",
        keywords=[
            "agent framework", "langgraph", "langchain", "llamaindex", "autogen",
            "crewai", "semantic kernel", "swarm", "openai agents sdk",
            "multi-agent", "agent workflow", "agent orchestration",
            "agent pipeline", "agent loop", "react agent", "plan and execute",
            "reflection agent", "self-reflection", "agent scaffold",
        ],
        patterns=[r"agentic\s+\w+", r"agent[\s_-]+based", r"orchestrat\w+"],
        weight=2.0,
    ),
    SubDomain(
        id="tool_use",
        label_cn="工具调用与函数调用",
        label_en="Tool Use & Function Calling",
        keywords=[
            "tool use", "tool calling", "tool call", "function calling",
            "function call", "tool-use", "tool invocation", "tool selection",
            "tool reliability", "tool chaining", "tool integration",
            "api calling", "mcp", "model context protocol",
            "computer use", "browser use", "code execution",
        ],
        patterns=[r"tool[\s_-]+\w+\s+agent", r"call\w*\s+tool"],
        weight=2.0,
    ),
    SubDomain(
        id="memory",
        label_cn="记忆与上下文管理",
        label_en="Memory & Context Management",
        keywords=[
            "agent memory", "long-term memory", "short-term memory",
            "episodic memory", "working memory", "memory management",
            "context management", "context window", "long context",
            "memory retrieval", "memory compression", "memory store",
            "vector store", "vector database", "embedding store",
            "knowledge graph", "external memory",
        ],
        patterns=[r"memor\w+\s+\w+\s+agent", r"context[\s_-]+compress\w+"],
        weight=1.8,
    ),
    SubDomain(
        id="planning",
        label_cn="规划与推理",
        label_en="Planning & Reasoning",
        keywords=[
            "planning", "task planning", "goal decomposition", "task decomposition",
            "chain of thought", "tree of thought", "reasoning", "step-by-step",
            "subgoal", "self-ask", "scratchpad", "inner monologue",
            "world model", "test-time compute", "inference-time scaling",
            "o1", "o3", "thinking model", "r1",
        ],
        patterns=[r"plan[\s_-]+and[\s_-]+execut\w+", r"decompos\w+\s+task"],
        weight=1.8,
    ),
    SubDomain(
        id="rag_retrieval",
        label_cn="RAG 与检索增强",
        label_en="RAG & Retrieval-Augmented Generation",
        keywords=[
            "rag", "retrieval augmented", "retrieval-augmented",
            "retrieval augmentation", "agentic rag", "graph rag",
            "hybrid search", "reranking", "rerank", "dense retrieval",
            "knowledge retrieval", "document retrieval", "chunk",
            "embedding model", "sentence embedding", "semantic search",
        ],
        patterns=[r"retriev\w+\s+agent", r"rag[\s_-]+pipelin\w+"],
        weight=1.6,
    ),
    SubDomain(
        id="inference_serving",
        label_cn="推理服务与部署",
        label_en="Inference Serving & Deployment",
        keywords=[
            "vllm", "sglang", "llm serving", "inference server",
            "kv cache", "speculative decoding", "continuous batching",
            "paged attention", "flash attention", "tensor parallel",
            "model serving", "deployment", "latency", "throughput",
            "quantization", "int4", "int8", "gguf", "llama.cpp",
            "ollama", "lm studio", "localai",
        ],
        patterns=[r"serv\w+\s+llm", r"deploy\w+\s+agent"],
        weight=1.5,
    ),
    SubDomain(
        id="eval_reliability",
        label_cn="评测与可靠性",
        label_en="Evaluation & Reliability",
        keywords=[
            "agent eval", "agent benchmark", "agent evaluation",
            "reliability", "consistency", "faithfulness", "groundedness",
            "hallucination", "benchmark", "evaluation framework",
            "agent testing", "agent safety", "red teaming",
            "tau-bench", "agentbench", "webarena", "workarena",
            "swebench", "humaneval", "lm-eval", "harness",
        ],
        patterns=[r"evaluat\w+\s+agent", r"agent[\s_-]+reliabilit\w+"],
        weight=1.5,
    ),
    SubDomain(
        id="multimodal_agent",
        label_cn="多模态 Agent",
        label_en="Multimodal Agent",
        keywords=[
            "multimodal agent", "vision agent", "gui agent", "web agent",
            "browser agent", "computer use", "screenshot", "ui grounding",
            "ocr agent", "visual grounding", "vqa agent",
            "vlm agent", "gpt-4v", "claude computer use",
        ],
        patterns=[r"multimodal[\s_-]+\w*agent", r"agent[\s_-]+visual"],
        weight=1.4,
    ),
    SubDomain(
        id="training_alignment",
        label_cn="训练与对齐（Agent 相关）",
        label_en="Training & Alignment for Agents",
        keywords=[
            "rlhf", "rlaif", "dpo", "ppo", "grpo", "sft",
            "instruction tuning", "alignment", "reward model",
            "preference optimization", "agent training",
            "agent fine-tuning", "lora", "qlora", "peft",
            "tool learning", "agent learning", "self-play",
        ],
        patterns=[r"train\w+\s+agent", r"fine[\s_-]?tun\w+\s+agent"],
        weight=1.2,
    ),
    SubDomain(
        id="infra_observability",
        label_cn="基础设施与可观测性",
        label_en="Infrastructure & Observability",
        keywords=[
            "agent observability", "tracing", "logging", "monitoring",
            "langsmith", "agentops", "promptlayer", "langfuse",
            "arize", "opentelemetry", "trace", "span",
            "agent debugging", "agent logging", "agent monitoring",
            "cost tracking", "token budget", "rate limit",
        ],
        patterns=[r"observ\w+\s+agent", r"agent[\s_-]+debug\w+"],
        weight=1.2,
    ),
]

# 编译所有 pattern（module 加载时一次性）
for _sd in AGENT_TAXONOMY:
    _sd.compile()

# 全局集合：所有子领域 id
SUBDOMAIN_IDS = [sd.id for sd in AGENT_TAXONOMY]


# ─────────────────────────────────────────────────────────────────
# 规则打分（Rule-based scoring）
# ─────────────────────────────────────────────────────────────────

def _text_for_scoring(article: Dict[str, Any]) -> str:
    """拼接 title（×3）+ desc 作为评分文本（title 权重更高）"""
    title = (article.get("title") or "").strip()
    desc = (article.get("desc") or "").strip()
    return f"{title} {title} {title} {desc}"


def rule_score(article: Dict[str, Any]) -> Dict[str, Any]:
    """
    规则打分：
    - 遍历所有子领域，统计命中关键词/pattern 数量 × 子领域权重
    - 聚合成 0-10 的总分；识别主导子领域；记录命中词作为 evidence_spans
    - 不依赖外部 API，始终可用
    """
    text = _text_for_scoring(article)
    text_lower = text.lower()

    subdomain_hits: List[Dict[str, Any]] = []
    raw_score = 0.0
    all_spans: List[str] = []

    for sd in AGENT_TAXONOMY:
        hits = []
        for kw in sd.keywords:
            pat = re.compile(rf"\b{re.escape(kw)}\b", re.I)
            if pat.search(text):
                hits.append(kw)
        for p_str in sd.patterns:
            m = re.search(p_str, text, re.I)
            if m:
                hits.append(m.group(0))

        if hits:
            # 多命中有衰减（log 压长尾），避免一个子领域刷满
            contribution = math.log1p(len(hits)) * sd.weight * 3.0
            raw_score += contribution
            subdomain_hits.append({
                "id": sd.id,
                "label_cn": sd.label_cn,
                "hits": len(hits),
                "contribution": round(contribution, 2),
            })
            all_spans.extend(hits[:3])  # 最多取前 3 个作为证据

    # 归一到 0-10（raw_score 约 3~15 对应 3~9 分）
    # sigmoid 拉伸：score = 10 / (1 + exp(-(raw - 4.5) / 2.5))
    if raw_score > 0:
        score_float = 10.0 / (1.0 + math.exp(-(raw_score - 4.5) / 2.5))
    else:
        score_float = 0.0

    score = round(min(10.0, max(0.0, score_float)), 1)

    # 主导子领域（贡献最高的 top-2）
    subdomain_hits.sort(key=lambda x: -x["contribution"])
    top_domains = [s["id"] for s in subdomain_hits[:2]]

    # 去重 evidence_spans
    seen: set = set()
    deduped_spans: List[str] = []
    for s in all_spans:
        ls = s.lower()
        if ls not in seen:
            seen.add(ls)
            deduped_spans.append(s)

    return {
        "score": score,
        "method": "rule",
        "top_domains": top_domains,
        "subdomain_hits": subdomain_hits[:5],  # 最多 5 个
        "evidence_spans": deduped_spans[:6],
        "rationale": _auto_rationale(score, top_domains, deduped_spans),
    }


def _auto_rationale(score: float, top_domains: List[str], spans: List[str]) -> str:
    """自动生成可读的 rationale（规则版）"""
    if score < 1.0:
        return "与 AI Agent 工程无明显相关性。"
    sd_map = {sd.id: sd.label_cn for sd in AGENT_TAXONOMY}
    domains_str = "、".join(sd_map.get(d, d) for d in top_domains) if top_domains else "通用"
    spans_str = "、".join(f"「{s}」" for s in spans[:3]) if spans else ""
    level = (
        "强相关" if score >= 7 else
        "中度相关" if score >= 4 else
        "弱相关"
    )
    parts = [f"与 AI Agent 工程 {level}（score={score}）。"]
    if domains_str:
        parts.append(f"主要涉及：{domains_str}。")
    if spans_str:
        parts.append(f"命中关键词：{spans_str}。")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────
# LLM 评分增强（可选，仅当 api_key 可用时使用）
# 完全复用 karpathy/jobs 的思路：同一 rubric，强约束 JSON 输出
# ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_AGENT_SCORING = """\
You are an expert evaluating technical content for engineers and architects \
who build AI Agent systems.

Rate the content's **AI Agent Engineering Value** on a scale from 0 to 10.

This score measures: how useful is this content for someone building, \
optimizing, evaluating, or operating AI Agent systems in production?

Use these anchors to calibrate:

- **0–1**: Completely unrelated to AI Agent engineering (pure ML theory, \
  unrelated industry, non-technical).
- **2–3**: Tangentially related (general LLM capabilities without agent \
  context; generic AI news).
- **4–5**: Moderately relevant (touches agent-adjacent topics like RAG, \
  LLM inference serving, or multimodal models, but no direct agent angle).
- **6–7**: Directly relevant (covers a core agent engineering topic such as \
  tool calling, memory, planning, multi-agent systems, agent evaluation, \
  or production deployment of agents).
- **8–9**: Highly valuable (concrete implementation, benchmark, or framework \
  insight that significantly advances agent engineering practice).
- **10**: Must-read for any agent engineer (breakthrough technique, critical \
  eval result, or major framework update that reshapes how agents are built).

Respond ONLY with a JSON object, no other text:
{
  "score": <0-10>,
  "top_domains": ["<subdomain_id>", ...],
  "evidence_spans": ["<key phrase from content>", ...],
  "rationale": "<1-2 sentences in Chinese explaining why>"
}

Valid subdomain_ids: orchestration, tool_use, memory, planning, \
rag_retrieval, inference_serving, eval_reliability, multimodal_agent, \
training_alignment, infra_observability
"""


async def llm_score(
    article: Dict[str, Any],
    llm_service: Any,
) -> Optional[Dict[str, Any]]:
    """
    LLM 评分（可选增强）。
    llm_service 是已初始化的 LLMSummaryService 实例。
    失败时返回 None（调用方 fallback 到 rule_score）。
    """
    if llm_service is None or not getattr(llm_service.config, "api_key", ""):
        return None

    title = (article.get("title") or "").strip()
    desc = (article.get("desc") or "").strip()
    user_content = f"Title: {title}\n\nDescription: {desc[:800]}"

    try:
        raw = await llm_service.chat_completion(
            system=SYSTEM_PROMPT_AGENT_SCORING,
            user=user_content,
            max_tokens=300,
        )
        if not raw:
            return None
        # 去掉 markdown 代码块
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw.strip())
        score = float(result.get("score", 0))
        return {
            "score": round(min(10.0, max(0.0, score)), 1),
            "method": "llm",
            "top_domains": result.get("top_domains") or [],
            "evidence_spans": result.get("evidence_spans") or [],
            "rationale": result.get("rationale") or "",
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# 增量批量打分（Batch scoring with checkpoint）
# 对 DB 中的 Article 逐条打分，结果落回 raw_data JSON 字段
# 参考 karpathy/jobs score.py 的 checkpoint 思路
# ─────────────────────────────────────────────────────────────────

SCORE_FIELD = "agent_eng_score"   # 存入 Article.raw_data 的 key


def get_cached_score(article_raw_data: Optional[str]) -> Optional[Dict[str, Any]]:
    """从 Article.raw_data 读取已缓存的评分"""
    if not article_raw_data:
        return None
    try:
        d = json.loads(article_raw_data)
        return d.get(SCORE_FIELD)
    except Exception:
        return None


def set_cached_score(
    existing_raw: Optional[str],
    score_result: Dict[str, Any],
) -> str:
    """将评分写入 Article.raw_data（保留其他字段）"""
    try:
        d = json.loads(existing_raw) if existing_raw else {}
    except Exception:
        d = {}
    d[SCORE_FIELD] = score_result
    return json.dumps(d, ensure_ascii=False)


async def score_articles_batch(
    articles: List[Any],          # List[Article] ORM 对象
    llm_service: Any = None,
    force: bool = False,
    limit: int = 200,
) -> Tuple[int, int]:
    """
    对一批 Article 逐条打分并缓存结果。
    返回 (scored_count, skipped_count)。
    LLM 不可用时全部 fallback 到规则打分。
    """
    scored = 0
    skipped = 0

    for art in articles[:limit]:
        # 已有缓存且不强制重跑 → 跳过
        if not force and get_cached_score(art.raw_data) is not None:
            skipped += 1
            continue

        art_dict = {
            "title": art.title,
            "desc": art.desc,
            "type": art.type,
            "date": art.date,
            "venue": art.venue,
            "heat": art.heat,
            "link": art.link,
        }

        # 优先 LLM，失败 fallback 规则
        result = await llm_score(art_dict, llm_service) if llm_service else None
        if result is None:
            result = rule_score(art_dict)

        art.raw_data = set_cached_score(art.raw_data, result)
        scored += 1

    return scored, skipped


# ─────────────────────────────────────────────────────────────────
# 分布聚合（Distribution aggregation）
# 给定已评分的 Article 列表，产出"行业影响分布"
# ─────────────────────────────────────────────────────────────────

def aggregate_distribution(
    scored_articles: List[Dict[str, Any]],
    min_score: float = 3.0,
) -> Dict[str, Any]:
    """
    聚合 AI Agent 工程行业的内容分布。

    scored_articles: 每条包含 'agent_eng_score' 字段的 dict
    min_score: 低于此分的条目不计入分布（过滤噪音）

    返回：
      - score_distribution: {bucket: count}（0-2/3-4/5-6/7-8/9-10）
      - subdomain_distribution: {subdomain_id: {score_sum, count, top_evidence}}
      - top_items: 总分最高的前 N 条
      - type_distribution: paper/repo/news 各自的均分与数量
    """
    buckets = {"9-10": 0, "7-8": 0, "5-6": 0, "3-4": 0, "0-2": 0}
    subdomain_agg: Dict[str, Dict[str, Any]] = {
        sd.id: {"label_cn": sd.label_cn, "label_en": sd.label_en,
                "score_sum": 0.0, "count": 0, "top_evidence": []}
        for sd in AGENT_TAXONOMY
    }
    type_agg: Dict[str, Dict[str, Any]] = {
        "paper": {"score_sum": 0.0, "count": 0},
        "repo": {"score_sum": 0.0, "count": 0},
        "news": {"score_sum": 0.0, "count": 0},
    }
    top_items: List[Dict[str, Any]] = []

    for art in scored_articles:
        sc = art.get("agent_eng_score") or {}
        score = float(sc.get("score") or 0)
        if score < min_score:
            continue

        # 分桶
        if score >= 9:
            buckets["9-10"] += 1
        elif score >= 7:
            buckets["7-8"] += 1
        elif score >= 5:
            buckets["5-6"] += 1
        elif score >= 3:
            buckets["3-4"] += 1
        else:
            buckets["0-2"] += 1

        # 子领域
        for dom_id in (sc.get("top_domains") or []):
            if dom_id in subdomain_agg:
                subdomain_agg[dom_id]["score_sum"] += score
                subdomain_agg[dom_id]["count"] += 1
                ev = {
                    "title": art.get("title") or "",
                    "link": art.get("link") or "",
                    "score": score,
                    "type": art.get("type") or "",
                    "date": art.get("date") or "",
                    "heat": int(art.get("heat") or 0),
                }
                subdomain_agg[dom_id]["top_evidence"].append(ev)

        # 类型
        atype = art.get("type") or "news"
        if atype in type_agg:
            type_agg[atype]["score_sum"] += score
            type_agg[atype]["count"] += 1

        top_items.append({
            "title": art.get("title") or "",
            "link": art.get("link") or "",
            "venue": art.get("venue") or "",
            "date": art.get("date") or "",
            "type": art.get("type") or "",
            "heat": int(art.get("heat") or 0),
            "score": score,
            "top_domains": sc.get("top_domains") or [],
            "evidence_spans": sc.get("evidence_spans") or [],
            "rationale": sc.get("rationale") or "",
        })

    # 排序每个子领域的 top evidence（最多 3 条）
    for dom_id, agg in subdomain_agg.items():
        agg["top_evidence"].sort(key=lambda x: (-x["score"], -x["heat"]))
        agg["top_evidence"] = agg["top_evidence"][:3]
        agg["avg_score"] = round(
            agg["score_sum"] / agg["count"] if agg["count"] > 0 else 0.0, 1
        )

    # 类型均分
    for t, ta in type_agg.items():
        ta["avg_score"] = round(
            ta["score_sum"] / ta["count"] if ta["count"] > 0 else 0.0, 1
        )

    # top_items 按 score × heat 综合排序
    top_items.sort(key=lambda x: (-(x["score"] * 0.6 + math.log1p(x["heat"]) * 0.4)))

    # 过滤未命中任何子领域的子领域
    active_subdomains = {
        k: v for k, v in subdomain_agg.items() if v["count"] > 0
    }
    # 按 count 降序
    active_subdomains = dict(
        sorted(active_subdomains.items(), key=lambda x: -x[1]["count"])
    )

    total_scored = sum(buckets.values())

    return {
        "total_scored": total_scored,
        "score_distribution": buckets,
        "subdomain_distribution": active_subdomains,
        "type_distribution": type_agg,
        "top_items": top_items[:20],
    }
