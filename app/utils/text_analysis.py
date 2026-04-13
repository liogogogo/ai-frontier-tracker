"""
文本分析与词频统计

改进要点（相较于纯计数版本）：
1. 复合术语预识别  — 50+ AI 专有概念在分词前统一为规范 token，避免被拆散
2. 概念规范化     — 复数/变体归并（llms→llm, agents→agent 等）
3. TF-IDF × 热度 × 时效三重加权
     - 热度权：log(1+heat) / log(1+999)；体现条目传播影响力
     - 时效权：exp(-days / RECENCY_HALF_LIFE)；AI 领域默认 14 天半衰
     - 来源权：paper=1.5 > repo=1.2 > news=1.0
     - 标题字段权 3×，摘要字段权 1×（比旧版「重复2次」更精确）
4. PMI 短语筛选   — 只返回统计显著的共现 bigram，而非全部相邻 2-gram
5. 对数似然比趋势  — G² 检验替代简单比率，对小频次更稳健
6. 兼容性         — 与原调用方 (main.py / feed_insights.py) 保持相同函数签名与返回结构
"""
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# ─────────────────────────────────────────────
# 超参数
# ─────────────────────────────────────────────

RECENCY_HALF_LIFE = 14.0   # 热门 AI 话题半衰期（天）
TITLE_FIELD_WEIGHT = 3.0   # 标题字段相对摘要的权重
MIN_BIGRAM_COUNT = 3        # PMI bigram 最小共现次数
MIN_PMI = 0.8               # 保留 bigram 的最低 PMI 阈值（0~∞，实践中 >0.6 较安全）
TYPE_WEIGHT = {"paper": 1.5, "repo": 1.2, "news": 1.0}

# 高价值主题锚点词：用于过滤掉“PMI 但无语义价值”的通用词组
# 目标：更偏 LLM/Agent/RAG/推理优化/训练/多模态 的技术概念
HIGH_VALUE_ANCHORS: FrozenSet[str] = frozenset({
    # core
    "llm", "agent", "rag", "inference", "reasoning",
    # agents/tooling
    "tool_use", "function_calling", "multi_agent", "language_agent", "agentic_ai",
    # inference/serving
    "kv_cache", "speculative_decoding", "continuous_batching", "paged_attention", "flash_attention",
    "weight_quantization", "ptq", "qat", "model_compression",
    # training/alignment
    "rlhf", "dpo", "ppo", "grpo", "sft", "instruction_tuning", "lora", "moe",
    "reward_model", "constitutional_ai", "alignment",
    # multimodal
    "vlm", "lvm", "multimodal_llm", "text_to_image", "text_to_video",
    # retrieval
    "vector_database", "dense_retrieval", "cross_encoder", "bi_encoder", "embedding",
    # scaling
    "scaling_laws", "long_context", "context_window",
})

# ─────────────────────────────────────────────
# AI 领域复合术语预识别
# 在分词前将多词短语替换为下划线连接的单一 token，防止被切散
# 顺序敏感：长/精确短语放前面
# ─────────────────────────────────────────────

# (原始正则, 规范 token（用下划线连接，不含空格）)
_COMPOUND_RAW: List[Tuple[str, str]] = [
    # 方法名
    (r"chain[\s\-_]+of[\s\-_]+thought", "chain_of_thought"),
    (r"tree[\s\-_]+of[\s\-_]+thought", "tree_of_thought"),
    (r"retrieval[\s\-_]+augmented[\s\-_]+generation", "rag"),
    (r"retrieval[\s\-_]augmented", "rag"),
    (r"in[\s\-_]+context[\s\-_]+learning", "in_context_learning"),
    (r"reinforcement[\s\-_]+learning[\s\-_]+from[\s\-_]+human[\s\-_]+feedback", "rlhf"),
    (r"direct[\s\-_]+preference[\s\-_]+optim\w*", "dpo"),
    (r"proximal[\s\-_]+policy[\s\-_]+optim\w*", "ppo"),
    (r"group[\s\-_]+relative[\s\-_]+policy[\s\-_]+optim\w*", "grpo"),
    (r"supervised[\s\-_]+fine[\s\-_]?tun\w*", "sft"),
    (r"instruction[\s\-_]+tun\w*", "instruction_tuning"),
    (r"parameter[\s\-_]+efficient[\s\-_]+fine[\s\-_]?tun\w*", "peft"),
    (r"low[\s\-_]+rank[\s\-_]+adapt\w*", "lora"),
    (r"mixture[\s\-_]+of[\s\-_]+expert\w*", "moe"),
    (r"speculative[\s\-_]+decod\w*", "speculative_decoding"),
    (r"continuous[\s\-_]+batching", "continuous_batching"),
    (r"paged[\s\-_]+attention", "paged_attention"),
    (r"flash[\s\-_]+attention", "flash_attention"),
    (r"kv[\s\-_]+cache", "kv_cache"),
    (r"key[\s\-_]+value[\s\-_]+cache", "kv_cache"),
    (r"context[\s\-_]+window", "context_window"),
    (r"long[\s\-_]+context", "long_context"),
    (r"function[\s\-_]+calling", "function_calling"),
    (r"tool[\s\-_]+use", "tool_use"),
    (r"multi[\s\-_]+agent", "multi_agent"),
    (r"agentic[\s\-_]+ai", "agentic_ai"),
    (r"language[\s\-_]+agent\w*", "language_agent"),
    (r"vision[\s\-_]+language[\s\-_]+model\w*", "vlm"),
    (r"large[\s\-_]+vision[\s\-_]+model\w*", "lvm"),
    (r"multimodal[\s\-_]+llm\w*", "multimodal_llm"),
    (r"large[\s\-_]+language[\s\-_]+model\w*", "llm"),
    (r"language[\s\-_]+model\w*", "llm"),
    (r"foundation[\s\-_]+model\w*", "foundation_model"),
    (r"pre[\s\-_]?train\w*[\s\-_]+model\w*", "pretrained_model"),
    (r"text[\s\-_]+to[\s\-_]+image", "text_to_image"),
    (r"text[\s\-_]+to[\s\-_]+video", "text_to_video"),
    (r"vector[\s\-_]+database\w*", "vector_database"),
    (r"dense[\s\-_]+retrieval", "dense_retrieval"),
    (r"cross[\s\-_]+encoder", "cross_encoder"),
    (r"bi[\s\-_]+encoder", "bi_encoder"),
    (r"token[\s\-_]+budget", "token_budget"),
    (r"model[\s\-_]+compress\w*", "model_compression"),
    (r"weight[\s\-_]+quanti[sz]\w*", "weight_quantization"),
    (r"post[\s\-_]+training[\s\-_]+quanti[sz]\w*", "ptq"),
    (r"quantization[\s\-_]+aware[\s\-_]+training", "qat"),
    (r"knowledge[\s\-_]+distill\w*", "knowledge_distillation"),
    (r"neural[\s\-_]+scaling[\s\-_]+law\w*", "scaling_laws"),
    (r"scaling[\s\-_]+law\w*", "scaling_laws"),
    (r"emergent[\s\-_]+abilit\w*", "emergent_abilities"),
    (r"hallucin\w+", "hallucination"),
    (r"world[\s\-_]+model\w*", "world_model"),
    (r"reward[\s\-_]+model\w*", "reward_model"),
    (r"constitutional[\s\-_]+ai", "constitutional_ai"),
    (r"human[\s\-_]+alignment", "alignment"),
]

COMPOUND_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + pat + r"\b", re.I), tok)
    for pat, tok in _COMPOUND_RAW
]

# ─────────────────────────────────────────────
# 概念规范化：复数、缩写变体 → 规范形式
# ─────────────────────────────────────────────

CONCEPT_NORM: Dict[str, str] = {
    "llms": "llm",
    "agents": "agent",
    "transformers": "transformer",
    "embeddings": "embedding",
    "tokens": "token",
    "datasets": "dataset",
    "benchmarks": "benchmark",
    "hallucinations": "hallucination",
    "attentions": "attention",
    "parameters": "parameter",
    "prompts": "prompt",
    "prompting": "prompt",
    "prompts": "prompt",
    "finetune": "fine_tuning",
    "finetuning": "fine_tuning",
    "fine-tuning": "fine_tuning",
    "fine-tune": "fine_tuning",
    "reasoning": "reasoning",
    "reasonings": "reasoning",
    "aligning": "alignment",
    "aligned": "alignment",
    "evaluations": "evaluation",
    "evaluating": "evaluation",
    "pre-training": "pretraining",
    "pretrains": "pretraining",
    "qlora": "lora",
    "llama": "llama",
    "chatgpt": "chatgpt",
    "gpt-4": "gpt4",
    "gpt-3": "gpt3",
    "gpt4o": "gpt4",
}

# ─────────────────────────────────────────────
# 停用词
# ─────────────────────────────────────────────

STOP_WORDS: FrozenSet[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "as", "is", "was", "are", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might", "must",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they",
    "my", "your", "his", "her", "its", "our", "their", "what", "which", "who", "when",
    "where", "why", "how", "all", "any", "both", "each", "few", "more", "most", "other",
    "some", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "just", "now", "then", "here", "there", "up", "down", "out", "off", "over",
    "under", "again", "further", "once", "during", "before", "after", "above", "below",
    "between", "through", "into", "onto", "upon", "about", "against", "until", "while",
    # 学术套话
    "using", "based", "via", "new", "novel", "proposed", "approach", "method",
    "model", "models", "system", "systems", "algorithm", "algorithms",
    "work", "works", "study", "studies", "research", "task", "tasks", "problem", "problems",
    "result", "results", "performance", "accuracy", "evaluation", "experiments", "experiment",
    "experimental", "comparison", "compared", "propose", "present", "demonstrate", "achieve",
    "achieves", "improve", "improvement", "improving", "show", "shows", "state", "art",
    "showing", "demonstrates", "proposed", "introduce", "introduced", "introducing", "make",
    "makes", "use", "used", "report", "reported", "existing", "recent", "previous", "prior",
    "first", "second", "one", "two", "three", "et", "al", "e.g", "i.e",
    "fig", "figure", "table", "section", "appendix", "equation", "eq", "see", "also",
    # AI 特定套话
    "arxiv", "preprint", "github", "com", "http", "https", "www", "html", "url",
    "api", "app", "application", "code", "data", "dataset", "benchmark", "benchmarks",
    "implementation", "source", "available", "open", "access", "online", "version",
    "update", "updated", "release", "released", "announcing", "announced", "announcement",
    "blog", "post", "article", "news", "read", "more", "full", "details", "link",
    "year", "years", "month", "months", "day", "days", "today", "yesterday", "last",
})

DISCUSSION_META_WORDS: FrozenSet[str] = frozenset({
    "hn", "ycombinator", "reddit", "subreddit", "lobsters", "hackernews",
    "karma", "upvote", "upvotes", "downvote", "downvotes",
    "discussion", "discussions", "thread", "threads",
    "repost", "reposts", "crosspost", "duplicate", "moderator", "mods", "mod", "meta",
    "removed", "deleted", "locked", "sticky", "sidebar",
    "permalink", "collapse", "expand", "submission", "submissions", "submit",
    "story", "stories", "edit", "edited", "eta", "tldr", "eli5", "askhn", "showhn",
    "paywall", "paywalled", "archive", "archived", "mirror", "mirrored", "cached", "snapshot",
    "subscription", "subscribe", "login", "signup", "outline", "amp",
    "hour", "hours", "minute", "minutes", "second", "seconds", "ago",
    "thanks", "thank", "thx", "please", "sorry", "wow", "lol", "imo", "fwiw", "ymmv", "tbh",
    "ngl", "afaik", "iirc", "yeah", "yep", "nope", "huh", "congrats", "congratulations",
    "awesome", "amazing", "brilliant", "terrible", "awful", "nice", "cool", "love", "hate",
    "interesting", "fascinating", "unfortunately", "hopefully", "honestly", "basically",
    "literally", "actually", "really", "totally", "quite", "pretty", "rather", "somewhat",
    "probably", "maybe", "perhaps", "certainly", "obviously", "clearly", "frankly", "sure",
    "agree", "agrees", "disagree", "wrong", "right", "true", "false", "yes", "shame",
    "thought", "thoughts", "opinion", "opinions", "believe", "feels", "feel", "wondering",
    "wonder", "guess", "idk", "dunno", "anyone", "everyone", "somebody", "someone", "people",
    "guys", "folks", "weird", "random", "insane", "crazy", "huge", "massive", "hell", "wtf",
    "stuff", "thing", "things", "lot", "bit", "kinda", "sorta", "pls", "fyi",
})


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _days_old(date_str: Optional[str]) -> float:
    if not date_str or len(str(date_str)) < 8:
        return 30.0
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        return max(0.0, float((datetime.utcnow().date() - d).days))
    except ValueError:
        return 30.0


def _article_weight(article: Dict) -> float:
    """热度 × 时效 × 来源类型的综合权重（0~1）"""
    heat = max(int(article.get("heat") or 0), 1)
    days = _days_old(article.get("date"))
    typ = article.get("type") or "news"
    heat_w = math.log1p(heat) / math.log1p(999)
    recency_w = math.exp(-days / RECENCY_HALF_LIFE)
    type_w = TYPE_WEIGHT.get(typ, 1.0)
    return heat_w * recency_w * type_w


def preprocess_text(text: str) -> str:
    """在分词前识别并规范化 AI 复合术语（替换为下划线连接的 token）"""
    for pattern, canonical in COMPOUND_PATTERNS:
        text = pattern.sub(f" {canonical} ", text)
    return text


def tokenize_text(text: str) -> List[str]:
    """
    分词：小写 → 复合术语保护 → 去标点 → 过滤停用词 → 概念规范化
    支持下划线连接的复合术语 token（来自 preprocess_text）
    """
    if not text:
        return []
    text = text.lower()
    # 短横线词组保护（如 fine-tuning）
    text = re.sub(r"(\w+)-(\w+)", r"\1_\2", text)
    # 去除其余标点
    text = re.sub(r"[^\w\s]", " ", text)

    tokens: List[str] = []
    for token in text.split():
        # 还原下划线为横杠（仅用于过滤判断与显示，下划线 token 也通过）
        display = token.replace("_", "-")
        # 长度过滤
        if len(display) < 2 or len(display) > 60:
            continue
        # 纯数字
        if token.replace("_", "").isdigit():
            continue
        # 只保留 ASCII 字母/数字/连字符/下划线
        if not re.match(r"^[a-z][a-z0-9_-]*$", token):
            continue
        # 停用词
        bare = display.rstrip("s")
        if (
            display in STOP_WORDS or bare in STOP_WORDS
            or display in DISCUSSION_META_WORDS or bare in DISCUSSION_META_WORDS
        ):
            continue
        # 概念规范化
        normalized = CONCEPT_NORM.get(display, display)
        # 还原为下划线形式存储（保证复合词一致性）
        tokens.append(normalized.replace("-", "_"))

    return tokens


def _display_token(token: str) -> str:
    """将内部下划线 token 还原为可读的连字符形式"""
    return token.replace("_", "-")


# ─────────────────────────────────────────────
# TF-IDF 加权词频
# ─────────────────────────────────────────────

def _tokenize_article(article: Dict) -> List[str]:
    """将单篇文章 title×3 + desc×1 的字段合并为 token 列表（带字段权重）"""
    title = preprocess_text(article.get("title") or "")
    desc = preprocess_text(article.get("desc") or "")
    title_tokens = tokenize_text(title)
    desc_tokens = tokenize_text(desc)
    # title 字段权重 3：重复 int(TITLE_FIELD_WEIGHT) 次
    reps = max(1, int(TITLE_FIELD_WEIGHT))
    return title_tokens * reps + desc_tokens


def _compute_weighted_tfidf(
    articles: List[Dict],
    top_k: int = 50,
) -> List[Tuple[str, float]]:
    """
    TF-IDF × 文章权重 聚合评分

    per_article:
      TF(t, a) = count(t, a) / total_tokens(a)
      weight(a) = heat_w × recency_w × type_w

    corpus:
      IDF(t) = log((N+1) / (df(t)+1)) + 1   # 平滑 IDF

    final_score(t) = Σ_a  TF(t, a) × IDF(t) × weight(a)
    结果缩放到 [0, 100]
    """
    N = len(articles)
    if N == 0:
        return []

    # 第一遍：per-article tokens + document frequency
    article_token_counts: List[Dict[str, int]] = []
    article_token_totals: List[int] = []
    df: Counter = Counter()

    for art in articles:
        tokens = _tokenize_article(art)
        cnt: Counter = Counter(tokens)
        article_token_counts.append(dict(cnt))
        article_token_totals.append(max(len(tokens), 1))
        for t in cnt:
            df[t] += 1

    # IDF（平滑）
    idf: Dict[str, float] = {
        t: math.log((N + 1) / (freq + 1)) + 1.0
        for t, freq in df.items()
    }

    # 加权 TF-IDF 累加
    scores: Dict[str, float] = defaultdict(float)
    for art, cnt, total in zip(articles, article_token_counts, article_token_totals):
        w = _article_weight(art)
        for t, c in cnt.items():
            tf = c / total
            scores[t] += tf * idf[t] * w

    if not scores:
        return []

    # 缩放到 0-100
    max_s = max(scores.values())
    if max_s > 0:
        scaled = {
            _display_token(t): round(v / max_s * 100, 1)
            for t, v in scores.items()
        }
    else:
        scaled = {_display_token(t): 0.0 for t in scores}

    return sorted(scaled.items(), key=lambda x: -x[1])[:top_k]


# ─────────────────────────────────────────────
# PMI Bigram
# ─────────────────────────────────────────────

def _compute_pmi_bigrams(
    articles: List[Dict],
    top_k: int = 25,
) -> List[Tuple[str, float]]:
    """
    基于 PMI（点互信息）的显著 bigram 提取

    PMI(w1, w2) = log[ P(w1 w2) / (P(w1) × P(w2)) ]
    NPMI = PMI / -log P(w1 w2)  ∈ [-1, 1]

    过滤条件：共现次数 ≥ MIN_BIGRAM_COUNT 且 PMI ≥ MIN_PMI
    """
    unigram_count: Counter = Counter()
    bigram_count: Counter = Counter()

    for art in articles:
        tokens = _tokenize_article(art)
        unigram_count.update(tokens)
        for i in range(len(tokens) - 1):
            bigram_count[(tokens[i], tokens[i + 1])] += 1

    total_uni = max(sum(unigram_count.values()), 1)
    total_bi = max(sum(bigram_count.values()), 1)

    results: List[Tuple[str, float]] = []
    for (w1, w2), b_cnt in bigram_count.items():
        if b_cnt < MIN_BIGRAM_COUNT:
            continue
        p_w1 = unigram_count[w1] / total_uni
        p_w2 = unigram_count[w2] / total_uni
        p_b = b_cnt / total_bi
        if p_w1 <= 0 or p_w2 <= 0 or p_b <= 0:
            continue
        pmi = math.log(p_b / (p_w1 * p_w2))
        if pmi < MIN_PMI:
            continue
        # NPMI 归一化（越接近1越紧密）
        npmi = pmi / (-math.log(p_b))
        phrase = f"{_display_token(w1)} {_display_token(w2)}"
        results.append((phrase, round(npmi * 100, 1)))

    results.sort(key=lambda x: -x[1])
    return results[:top_k]


# ─────────────────────────────────────────────
# 标签共现
# ─────────────────────────────────────────────

def _compute_tag_cooccurrence(articles: List[Dict]) -> Dict[str, int]:
    tag_cooccur: Counter = Counter()
    for art in articles:
        tags = art.get("tags") or []
        if not isinstance(tags, list) or len(tags) < 2:
            continue
        tag_set = set(str(t).lower() for t in tags if t)
        for t in tag_set:
            other = tag_set - {t}
            if other:
                tag_cooccur[t] += len(other)
    return dict(tag_cooccur.most_common(20))


# ─────────────────────────────────────────────
# 公开 API（与原调用方兼容）
# ─────────────────────────────────────────────

def extract_keywords(text: str, top_k: int = 10) -> List[Tuple[str, int]]:
    """保持向后兼容；返回 (token, 原始计数)"""
    tokens = tokenize_text(preprocess_text(text))
    return [(
        _display_token(w), c)
        for w, c in Counter(tokens).most_common(top_k)
    ]


def analyze_articles(articles: List[Dict], top_k: int = 50) -> Dict:
    """
    分析文章列表，返回词频与短语洞察。

    返回结构（兼容旧版）：
      total_articles, date_range, type_distribution,
      word_frequency  → [(word, score_0_100), ...]  (原为计数，现为加权 TF-IDF 分)
      ngram_frequency → [(phrase, npmi_0_100), ...]  (原为计数，现为 PMI 分)
      tag_cooccurrence
      scoring_method  (新增：说明计分方式)
    """
    if not articles:
        return {
            "total_articles": 0,
            "date_range": None,
            "type_distribution": {},
            "word_frequency": [],
            "ngram_frequency": [],
            "tag_cooccurrence": {},
            "scoring_method": "tfidf_heat_recency",
        }

    type_dist: Counter = Counter()
    dates: List[str] = []
    for art in articles:
        type_dist[art.get("type") or "unknown"] += 1
        d = art.get("date")
        if d:
            dates.append(d)

    date_range = None
    if dates:
        ds = sorted(dates)
        date_range = {"start": ds[0], "end": ds[-1]}

    word_freq = _compute_weighted_tfidf(articles, top_k=top_k)
    ngram_freq = _compute_pmi_bigrams(articles, top_k=top_k // 2)
    tag_cooccur = _compute_tag_cooccurrence(articles)

    return {
        "total_articles": len(articles),
        "date_range": date_range,
        "type_distribution": dict(type_dist),
        "word_frequency": word_freq,
        "ngram_frequency": ngram_freq,
        "tag_cooccurrence": tag_cooccur,
        "scoring_method": "tfidf_heat_recency",
    }


def filter_by_time_range(articles: List[Dict], days: Optional[int] = None) -> List[Dict]:
    if not days:
        return articles
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [a for a in articles if (a.get("date") or "") >= cutoff]


# ─────────────────────────────────────────────
# 趋势检测：对数似然比 G² 检验
# 比简单频率比率对小频次更稳健
# ─────────────────────────────────────────────

def _build_weighted_counter(articles: List[Dict]) -> Tuple[Counter, float]:
    """构建加权词频计数器（weight × token_count）"""
    counter: Counter = Counter()
    total_weight = 0.0
    for art in articles:
        w = max(_article_weight(art), 1e-6)
        tokens = _tokenize_article(art)
        total_weight += w
        for t in tokens:
            counter[t] += w
    return counter, max(total_weight, 1e-6)


def _g2_score(o11: float, o12: float, o21: float, o22: float) -> float:
    """二阶列联表对数似然比 G²，用于检验词汇在近期是否显著上升"""
    total = o11 + o12 + o21 + o22
    if total <= 0:
        return 0.0

    def _cell(o: float, e: float) -> float:
        if o <= 0 or e <= 0:
            return 0.0
        return o * math.log(o / e)

    e11 = (o11 + o21) * (o11 + o12) / total
    e12 = (o11 + o12) * (o12 + o22) / total
    e21 = (o21 + o22) * (o11 + o21) / total
    e22 = (o21 + o22) * (o12 + o22) / total

    return 2.0 * (_cell(o11, e11) + _cell(o12, e12) + _cell(o21, e21) + _cell(o22, e22))


def get_trending_words(
    articles_recent: List[Dict],
    articles_historical: List[Dict],
    top_k: int = 20,
) -> List[Dict]:
    """
    对比近期 vs 历史，找出显著上升的词汇。

    使用 G²（对数似然比）检验，比旧版简单比率对小频次更稳健。
    只返回：近期频率 > 历史频率 且 G² > 3.84（p<0.05）的词。
    """
    if not articles_recent or not articles_historical:
        return []

    recent_cnt, recent_total = _build_weighted_counter(articles_recent)
    hist_cnt, hist_total = _build_weighted_counter(articles_historical)

    all_terms = set(recent_cnt) | set(hist_cnt)
    trending: List[Dict] = []

    for term in all_terms:
        o11 = recent_cnt.get(term, 0.0)
        o21 = hist_cnt.get(term, 0.0)

        # 只考虑近期比历史多的词
        recent_freq = o11 / recent_total
        hist_freq = o21 / hist_total
        if recent_freq <= hist_freq:
            continue

        o12 = recent_total - o11
        o22 = hist_total - o21
        g2 = _g2_score(o11, o12, o21, o22)

        # p<0.05 对应 G² > 3.84（自由度=1）
        if g2 < 3.84:
            continue

        trend_ratio = recent_freq / max(hist_freq, 1e-9)
        trending.append({
            "word": _display_token(term),
            "recent_count": round(o11, 2),
            "historical_count": round(o21, 2),
            "trend": round(trend_ratio, 2),
            "g2": round(g2, 1),
        })

    trending.sort(key=lambda x: -x["g2"])
    return trending[:top_k]


# ─────────────────────────────────────────────
# Topic Cards：跨 source 的“高价值主题”输出
# ─────────────────────────────────────────────

def _normalize_term_to_tokens(term: str) -> List[str]:
    """
    将一个候选主题（词/短语）规范化为内部 token 列表。
    - 单词：返回 [token]
    - 短语：返回前 2 个 token（用于 bigram 证据匹配）
    """
    toks = tokenize_text(preprocess_text(term or ""))
    return toks[:2]


def _article_evidence_for_term(
    articles: List[Dict],
    term_tokens: List[str],
    k: int,
) -> List[Dict]:
    """返回包含 term 的 top-k 文章证据（按 heat/新鲜度粗排）"""
    if not term_tokens or not articles:
        return []

    is_bigram = len(term_tokens) >= 2
    t1 = term_tokens[0]
    t2 = term_tokens[1] if is_bigram else ""

    hits: List[Dict] = []
    for a in articles:
        toks = _tokenize_article(a)
        ok = False
        if is_bigram:
            # 连续 bigram 匹配
            for i in range(len(toks) - 1):
                if toks[i] == t1 and toks[i + 1] == t2:
                    ok = True
                    break
        else:
            ok = t1 in toks
        if not ok:
            continue

        hits.append({
            "title": a.get("title") or "",
            "link": a.get("link") or "",
            "venue": a.get("venue") or "",
            "date": a.get("date") or "",
            "type": a.get("type") or "",
            "heat": int(a.get("heat") or 0),
        })

    hits.sort(key=lambda x: (x.get("heat", 0), x.get("date", "")), reverse=True)
    return hits[: max(0, int(k or 0))]


def build_topic_cards(
    recent_articles: List[Dict],
    historical_articles: List[Dict],
    top_k: int = 15,
    per_type_top_k: int = 30,
    evidence_k: int = 3,
) -> List[Dict]:
    """
    生成 Topic Cards（主题卡片）。

    思路：
    - 先按 type（paper/repo/news）分别做趋势（G²）与短语（PMI bigram）提名
    - 候选主题合并后，按“跨源一致性 + 趋势强度”重排
    - 每个主题返回三类证据（paper/repo/news 各 top-k 代表条目）
    """
    if not recent_articles:
        return []

    allowed_types = ("paper", "repo", "news")

    recent_by_type = {t: [a for a in recent_articles if (a.get("type") or "") == t] for t in allowed_types}
    hist_by_type = {t: [a for a in historical_articles if (a.get("type") or "") == t] for t in allowed_types}

    # 1) per-type 提名：趋势词 + PMI 短语
    nominations: Dict[str, Dict[str, Any]] = {}

    def _is_high_value_tokens(tokens: List[str]) -> bool:
        if not tokens:
            return False
        if any(t in HIGH_VALUE_ANCHORS for t in tokens):
            return True
        # 子串兜底（按 token 维度，避免 exploration 命中 lora 这种误伤）
        for t in tokens:
            if "llm" in t and (t.startswith("llm") or t.endswith("llm") or "_llm" in t or "llm_" in t):
                return True
            if "mllm" in t and (t.startswith("mllm") or t.endswith("mllm") or "_mllm" in t or "mllm_" in t):
                return True
            if "rag" in t and (t == "rag" or t.startswith("rag_") or t.endswith("_rag")):
                return True
            if "agent" in t and (t == "agent" or t.startswith("agent") or t.endswith("agent") or "_agent" in t):
                return True
            if "vlm" in t and (t == "vlm" or t.startswith("vlm") or t.endswith("vlm") or "_vlm" in t):
                return True
            if t.startswith("kv_") or t.endswith("_kv") or t == "kv":
                return True
            if t == "lora" or t.endswith("_lora") or t.startswith("lora_"):
                return True
            if t == "dpo" or t.endswith("_dpo") or t.startswith("dpo_"):
                return True
            if t == "rlhf" or t.endswith("_rlhf") or t.startswith("rlhf_"):
                return True
        return False

    def _add_nom(term: str, kind: str, typ: str, score: float):
        term = (term or "").strip()
        if not term:
            return
        toks = _normalize_term_to_tokens(term)
        # 强过滤：只保留能被“LLM/Agent/RAG/推理/训练/多模态”锚点解释的主题
        if not _is_high_value_tokens(toks):
            return
        if term not in nominations:
            nominations[term] = {
                "term": term,
                "kind": kind,  # "token" | "phrase"
                "signals": {t: {"g2": 0.0, "trend": 1.0, "recent": 0.0, "hist": 0.0} for t in allowed_types},
            }
        # kind 以更信息密度的为准（phrase > token）
        if kind == "phrase":
            nominations[term]["kind"] = "phrase"
        nominations[term]["signals"][typ]["g2"] = max(nominations[term]["signals"][typ]["g2"], float(score or 0.0))

    for typ in allowed_types:
        if recent_by_type[typ] and hist_by_type[typ]:
            for row in get_trending_words(recent_by_type[typ], hist_by_type[typ], top_k=per_type_top_k):
                _add_nom(row.get("word") or "", "token", typ, float(row.get("g2") or 0.0))
                # 同时保留可解释数据（用于前端展示/调参）
                term = (row.get("word") or "").strip()
                if term in nominations:
                    nominations[term]["signals"][typ].update({
                        "trend": float(row.get("trend") or 1.0),
                        "recent": float(row.get("recent_count") or 0.0),
                        "hist": float(row.get("historical_count") or 0.0),
                    })

        # bigram 只依赖 recent（历史 bigram 统计成本高，先用近期开启“新搭配”）
        if recent_by_type[typ]:
            try:
                ana = analyze_articles(recent_by_type[typ], top_k=max(20, per_type_top_k))
                # 词频提名：补齐“历史为空导致无趋势”的情况（例如论文突增）
                for word, score in (ana.get("word_frequency") or [])[: max(12, per_type_top_k // 2)]:
                    _add_nom(str(word), "token", typ, float(score or 0.0))
                for phrase, npmi in (ana.get("ngram_frequency") or [])[: max(10, per_type_top_k // 3)]:
                    # PMI 短语很容易出现“统计相关但语义无价值”的搭配，这里仍走锚点过滤
                    _add_nom(str(phrase), "phrase", typ, float(npmi or 0.0))
            except Exception:
                # 词频体系不应因短语失败而整体失败
                pass

    if not nominations:
        return []

    # 2) 重排：跨源一致性优先（paper+repo 最强），其次趋势强度
    def _cross_source_bonus(sig: Dict[str, Dict[str, float]]) -> float:
        paper = sig["paper"]["g2"]
        repo = sig["repo"]["g2"]
        news = sig["news"]["g2"]
        bonus = 0.0
        if paper > 0 and repo > 0:
            bonus += 80.0
        if paper > 0 and news > 0:
            bonus += 35.0
        if repo > 0 and news > 0:
            bonus += 20.0
        # 三源都活跃再加一点
        if paper > 0 and repo > 0 and news > 0:
            bonus += 25.0
        return bonus

    def _score(term_obj: Dict[str, Any]) -> float:
        sig = term_obj["signals"]
        # 趋势强度（G²）为主，短语 npmi 也记在 g2 槽里作为弱趋势信号
        base = sig["paper"]["g2"] * 1.8 + sig["repo"]["g2"] * 1.2 + sig["news"]["g2"] * 0.8
        return base + _cross_source_bonus(sig)

    ranked = sorted(nominations.values(), key=_score, reverse=True)

    # 3) 组装 cards：补充证据回溯
    cards: List[Dict] = []
    for obj in ranked[: max(1, int(top_k or 1)) * 3]:
        term = obj["term"]
        term_tokens = _normalize_term_to_tokens(term)
        if not term_tokens:
            continue

        evidence = {
            "paper": _article_evidence_for_term(recent_by_type["paper"], term_tokens, evidence_k),
            "repo": _article_evidence_for_term(recent_by_type["repo"], term_tokens, evidence_k),
            "news": _article_evidence_for_term(recent_by_type["news"], term_tokens, evidence_k),
        }

        # 过滤掉完全没有证据的提名（避免 tokenize 误提名）
        if not (evidence["paper"] or evidence["repo"] or evidence["news"]):
            continue

        signals = obj["signals"]
        cards.append({
            "term": term,
            "kind": obj["kind"],
            "score": round(_score(obj), 1),
            "signals": {
                "paper": {k: round(float(v), 2) for k, v in signals["paper"].items()},
                "repo": {k: round(float(v), 2) for k, v in signals["repo"].items()},
                "news": {k: round(float(v), 2) for k, v in signals["news"].items()},
            },
            "evidence": evidence,
        })

        if len(cards) >= max(1, int(top_k or 1)):
            break

    return cards


# ─────────────────────────────────────────────
# Topic Cards：跨 source 的“高价值主题”输出
# ─────────────────────────────────────────────

def _normalize_term_to_tokens(term: str) -> List[str]:
    """
    将一个候选主题（词/短语）规范化为内部 token 列表。
    - 单词：返回 [token]
    - 短语：返回前 2 个 token（用于 bigram 证据匹配）
    """
    toks = tokenize_text(preprocess_text(term or ""))
    return toks[:2]


def _article_evidence_for_term(
    articles: List[Dict],
    term_tokens: List[str],
    k: int,
) -> List[Dict]:
    """返回包含 term 的 top-k 文章证据（按 heat/新鲜度粗排）"""
    if not term_tokens or not articles:
        return []

    is_bigram = len(term_tokens) >= 2
    t1 = term_tokens[0]
    t2 = term_tokens[1] if is_bigram else ""

    hits: List[Dict] = []
    for a in articles:
        toks = _tokenize_article(a)
        ok = False
        if is_bigram:
            # 连续 bigram 匹配
            for i in range(len(toks) - 1):
                if toks[i] == t1 and toks[i + 1] == t2:
                    ok = True
                    break
        else:
            ok = t1 in toks
        if not ok:
            continue

        hits.append({
            "title": a.get("title") or "",
            "link": a.get("link") or "",
            "venue": a.get("venue") or "",
            "date": a.get("date") or "",
            "type": a.get("type") or "",
            "heat": int(a.get("heat") or 0),
        })

    hits.sort(key=lambda x: (x.get("heat", 0), x.get("date", "")), reverse=True)
    return hits[: max(0, int(k or 0))]
