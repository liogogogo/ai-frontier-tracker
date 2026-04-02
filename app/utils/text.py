"""
文本处理工具
"""
import re
import html
from typing import List

# 标签规则
TAG_RULES: List[tuple[str, List[str]]] = [
    ("llm", [r"\bllm\b", r"large language", r"language model", r"llama", r"gpt", r"transformer"]),
    ("inference", [r"\binferen", r"kv cache", r"vllm", r"speculative", r"quantiz", r"flash.?attn"]),
    ("rag", [r"\brag\b", r"retriev", r"embedding", r"vector store"]),
    ("agent", [r"\bagent\b", r"tool use", r"function call", r"react", r"multi.?agent"]),
    ("multimodal", [r"vision", r"multimodal", r"vlm", r"image", r"audio", r"speech"]),
    ("training", [r"train", r"pretrain", r"finetun", r"rlhf", r"scaling", r"moE\b", r"mixture of expert"]),
]


def strip_html(s: str) -> str:
    """去除HTML标签并解码实体"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def infer_tags(text: str) -> List[str]:
    """从文本推断标签"""
    lowered = text.lower()
    out: List[str] = []
    
    for tag, patterns in TAG_RULES:
        if any(re.search(p, lowered) for p in patterns):
            out.append(tag)
    
    if not out:
        out = ["llm"]
    
    return out[:4]
