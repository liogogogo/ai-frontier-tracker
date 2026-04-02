"""
文本分析与词频统计
"""
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

# AI领域停用词
STOP_WORDS = {
    # 通用停用词
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
    # 技术领域常见停用词
    "using", "based", "via", "based", "new", "novel", "proposed", "approach", "method",
    "model", "models", "system", "systems", "algorithm", "algorithms", "paper", "papers",
    "work", "works", "study", "studies", "research", "task", "tasks", "problem", "problems",
    "result", "results", "performance", "accuracy", "evaluation", "experiments", "experiment",
    "experimental", "comparison", "compared", "propose", "present", "demonstrate", "achieve",
    "achieves", "improve", "improvement", "improving", "show", "shows", "state", "art",
    "showing", "demonstrates", "proposed", "introduce", "introduced", "introducing", "make",
    "makes", "use", "used", "report", "reported", "existing", "recent", "previous", "prior",
    "first", "second", "one", "two", "three", "1", "2", "3", "et", "al", "e.g", "i.e",
    "fig", "figure", "table", "section", "appendix", "equation", "eq", "see", "also",
    # AI特定停用词
    "arxiv", "preprint", "github", "com", "http", "https", "www", "html", "url",
    "api", "app", "application", "code", "data", "dataset", "benchmark", "benchmarks",
    "implementation", "source", "available", "open", "access", "online", "version",
    "update", "updated", "release", "released", "announcing", "announced", "announcement",
    "blog", "post", "article", "news", "read", "more", "full", "details", "link",
    "year", "years", "month", "months", "day", "days", "today", "yesterday", "last",
}

# 社区讨论「壳子」：互动、站务、情绪口语、时间相对词等，往往随帖温高但与技术议题无关
DISCUSSION_META_WORDS: FrozenSet[str] = frozenset({
    # 平台 / 站务
    "hn", "ycombinator", "reddit", "subreddit", "lobsters", "hackernews",
    "karma", "upvote", "upvotes", "downvote", "downvotes",
    "discussion", "discussions", "thread", "threads",
    "repost", "reposts", "crosspost", "duplicate", "moderator", "mods", "mod", "meta",
    "removed", "deleted", "locked", "sticky", "sidebar",
    "permalink", "collapse", "expand", "submission", "submissions", "submit",
    "story", "stories", "edit", "edited", "eta", "tldr", "eli5", "askhn", "showhn",
    # 付费墙 / 镜像 / 存档（转贴常用套话）
    "paywall", "paywalled", "archive", "archived", "mirror", "mirrored", "cached", "snapshot",
    "subscription", "subscribe", "login", "signup", "outline", "amp",
    # 时间粒度（「x 小时前」类噪声）
    "hour", "hours", "minute", "minutes", "second", "seconds", "ago",
    # 情绪 / 套话 / 立场词（无明显领域信息）
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


def tokenize_text(text: str) -> List[str]:
    """
    英文分词（小写、去除标点、保留词组）
    """
    if not text:
        return []
    
    # 转为小写
    text = text.lower()
    
    # 保留常见连字符词组
    # 例如: "large-scale", "state-of-the-art", "pre-trained"
    text = re.sub(r'(\w+)-(\w+)', r'\1_\2', text)
    
    # 移除标点符号（保留下划线用于词组）
    text = re.sub(r'[^\w\s_]', ' ', text)
    
    # 分词并过滤
    tokens = []
    for token in text.split():
        # 还原词组
        token = token.replace('_', '-')
        # 长度过滤
        if len(token) < 2 or len(token) > 40:
            continue
        # 纯数字过滤
        if token.isdigit():
            continue
        # 停用词：通用 + 技术套话 + 讨论元信息（与内容议题无关的高频）
        if (
            token in STOP_WORDS
            or token.rstrip("s") in STOP_WORDS
            or token in DISCUSSION_META_WORDS
            or token.rstrip("s") in DISCUSSION_META_WORDS
        ):
            continue
        # 必须以字母开头
        if not token[0].isalpha():
            continue
        tokens.append(token)
    
    return tokens


def extract_keywords(text: str, top_k: int = 10) -> List[Tuple[str, int]]:
    """
    提取关键词及频率
    """
    tokens = tokenize_text(text)
    counter = Counter(tokens)
    return counter.most_common(top_k)


def analyze_articles(articles: List[Dict], top_k: int = 50) -> Dict:
    """
    分析文章列表，返回词频统计和元数据
    
    Args:
        articles: 文章列表，每个包含 title, desc, type, date 等字段
        top_k: 返回最多的词数
    
    Returns:
        {
            "total_articles": int,
            "date_range": {"start": str, "end": str},
            "type_distribution": {"paper": int, "news": int, ...},
            "word_frequency": [(word, count), ...],
            "ngram_frequency": [(phrase, count), ...],
            "tag_cooccurrence": {(tag1, tag2): count, ...}
        }
    """
    if not articles:
        return {
            "total_articles": 0,
            "date_range": None,
            "type_distribution": {},
            "word_frequency": [],
            "ngram_frequency": [],
            "tag_cooccurrence": {}
        }
    
    # 合并所有文本
    all_text = []
    type_dist = Counter()
    dates = []
    tag_sets = []
    
    for article in articles:
        # 标题权重更高，重复2次
        title = article.get("title", "")
        desc = article.get("desc", "")
        all_text.extend([title] * 2)
        all_text.append(desc)
        
        # 类型统计
        article_type = article.get("type", "unknown")
        type_dist[article_type] += 1
        
        # 日期收集
        date_str = article.get("date")
        if date_str:
            dates.append(date_str)
        
        # 标签共现
        tags = article.get("tags", [])
        if isinstance(tags, list) and len(tags) >= 2:
            tag_sets.append(set(tags))
    
    full_text = " ".join(all_text)
    
    # 词频统计
    word_freq = extract_keywords(full_text, top_k)
    
    # 2-gram短语统计
    ngram_freq = extract_ngrams(full_text, n=2, top_k=top_k // 2)
    
    # 标签共现统计
    tag_cooccur = Counter()
    for i, tags1 in enumerate(tag_sets):
        for tags2 in tag_sets[i+1:]:
            common = tags1 & tags2
            for tag in common:
                tag_cooccur[tag] += 1
    
    # 日期范围
    date_range = None
    if dates:
        dates_sorted = sorted(dates)
        date_range = {
            "start": dates_sorted[0],
            "end": dates_sorted[-1]
        }
    
    return {
        "total_articles": len(articles),
        "date_range": date_range,
        "type_distribution": dict(type_dist),
        "word_frequency": word_freq,
        "ngram_frequency": ngram_freq,
        "tag_cooccurrence": dict(tag_cooccur.most_common(20))
    }


def extract_ngrams(text: str, n: int = 2, top_k: int = 20) -> List[Tuple[str, int]]:
    """
    提取n-gram短语（主要用于提取复合词）
    """
    tokens = tokenize_text(text)
    if len(tokens) < n:
        return []
    
    # 构建n-gram
    ngrams = []
    for i in range(len(tokens) - n + 1):
        gram = " ".join(tokens[i:i+n])
        # 过滤不合格的n-gram
        if any(stop in gram for stop in ["the", "and", "of", "in", "to"]):
            continue
        ngrams.append(gram)
    
    counter = Counter(ngrams)
    return counter.most_common(top_k)


def filter_by_time_range(articles: List[Dict], days: Optional[int] = None) -> List[Dict]:
    """
    按时间范围过滤文章
    """
    if not days:
        return articles
    
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [
        article for article in articles
        if article.get("date", "") >= cutoff
    ]


def get_trending_words(
    articles_recent: List[Dict],
    articles_historical: List[Dict],
    top_k: int = 20
) -> List[Dict]:
    """
    对比近期和历史，找出趋势上升的词
    
    Returns:
        [{"word": str, "recent_count": int, "historical_count": int, "trend": float}, ...]
    """
    if not articles_recent or not articles_historical:
        return []
    
    # 合并文本
    recent_text = " ".join([
        (a.get("title", "") + " ") * 2 + a.get("desc", "")
        for a in articles_recent
    ])
    hist_text = " ".join([
        (a.get("title", "") + " ") * 2 + a.get("desc", "")
        for a in articles_historical
    ])
    
    # 统计词频
    recent_tokens = tokenize_text(recent_text)
    hist_tokens = tokenize_text(hist_text)
    
    recent_counter = Counter(recent_tokens)
    hist_counter = Counter(hist_tokens)
    
    # 计算趋势
    trending = []
    for word, recent_count in recent_counter.most_common(100):
        hist_count = hist_counter.get(word, 0)
        
        # 趋势分数：近期相对频率 / 历史相对频率
        recent_freq = recent_count / len(recent_tokens) if recent_tokens else 0
        hist_freq = hist_count / len(hist_tokens) if hist_tokens else 0.0001
        
        trend_score = recent_freq / hist_freq
        
        # 只保留趋势上升的
        if trend_score > 1.2 and recent_count >= 3:
            trending.append({
                "word": word,
                "recent_count": recent_count,
                "historical_count": hist_count,
                "trend": round(trend_score, 2)
            })
    
    # 按趋势分数排序
    trending.sort(key=lambda x: x["trend"], reverse=True)
    return trending[:top_k]
