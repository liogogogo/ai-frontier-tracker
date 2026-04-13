"""
AI 领域命名实体字典 NER

维护结构化词典，覆盖：
  model     — 大模型名称
  technique — 技术/方法
  tool      — 工具/框架
  org       — 机构/团队
  benchmark — 评测基准

用途：
  extract_entities(text) → List[str] entity_id 列表
  ENTITY_META[id] → {display, category}
"""
from __future__ import annotations

import re
from typing import Dict, List, Any, Tuple

# ─────────────────────────────────────────────────────────────────
# 实体词典：id → {display, category, forms}
# forms: 所有表面形式（纯文本；匹配时用 \b...\b 加 re.IGNORECASE）
# ─────────────────────────────────────────────────────────────────

_RAW: List[Tuple[str, str, str, List[str]]] = [
    # (entity_id, display, category, [surface_forms])

    # ── Models ───────────────────────────────────────────────────
    ("gpt4o",      "GPT-4o",        "model", ["GPT-4o", "gpt-4o", "GPT4o"]),
    ("gpt4",       "GPT-4",         "model", ["GPT-4", "gpt-4", "GPT4"]),
    ("gpt35",      "GPT-3.5",       "model", ["GPT-3.5", "gpt-3.5", "GPT3.5", "ChatGPT"]),
    ("o1_model",   "OpenAI o1",     "model", ["openai o1", "o1 model", "o1-mini", "o1-preview"]),
    ("o3_model",   "OpenAI o3",     "model", ["openai o3", "o3-mini", "o3 model"]),
    ("o4_model",   "OpenAI o4",     "model", ["openai o4", "o4-mini", "o4 model"]),
    ("claude3",    "Claude 3",      "model", ["claude 3", "claude-3", "claude3"]),
    ("claude35",   "Claude 3.5",    "model", ["claude 3.5", "claude-3.5", "claude 3.5 sonnet"]),
    ("claude37",   "Claude 3.7",    "model", ["claude 3.7", "claude-3.7", "claude 3.7 sonnet"]),
    ("llama3",     "Llama 3",       "model", ["llama 3", "llama-3", "llama3", "llama 3.1",
                                               "llama 3.2", "llama3.1", "llama3.2", "meta llama 3"]),
    ("llama2",     "Llama 2",       "model", ["llama 2", "llama-2", "llama2"]),
    ("gemini",     "Gemini",        "model", ["gemini pro", "gemini ultra", "gemini flash",
                                               "gemini 1.5", "gemini 2.0", "gemini 2", "gemini advanced"]),
    ("qwen",       "Qwen",          "model", ["qwen2", "qwen2.5", "qwen-2", "qwen 2", "qwen 2.5",
                                               "qwen-vl", "qwq", "qwen3", "qwen 3"]),
    ("deepseek",   "DeepSeek",      "model", ["deepseek-v2", "deepseek-v3", "deepseek v2", "deepseek v3",
                                               "deepseek-r1", "deepseek r1", "deepseek-coder", "deepseek"]),
    ("mistral",    "Mistral",       "model", ["mistral 7b", "mistral-7b", "mixtral", "mistral large",
                                               "mistral nemo", "mistral small"]),
    ("phi",        "Phi",           "model", ["phi-3", "phi-4", "phi 3", "phi 4", "microsoft phi"]),
    ("falcon",     "Falcon",        "model", ["falcon2", "falcon 2", "falcon 40b", "falcon 180b"]),
    ("command_r",  "Command R",     "model", ["command r+", "command r plus", "command-r"]),
    ("yi_model",   "Yi",            "model", ["yi-34b", "yi 34b", "yi-6b", "01-ai yi"]),
    ("grok",       "Grok",          "model", ["grok-2", "grok 2", "grok-3", "grok 3", "xai grok"]),
    ("gemma",      "Gemma",         "model", ["gemma 2", "gemma2", "gemma-2", "google gemma"]),
    ("internlm",   "InternLM",      "model", ["internlm2", "internlm 2", "internlm-2", "internlm"]),
    ("glm",        "GLM",           "model", ["glm-4", "chatglm", "chatglm3"]),
    ("claude2",    "Claude 2",      "model", ["claude 2", "claude-2", "claude2"]),
    ("palm",       "PaLM",          "model", ["palm 2", "palm2", "google palm"]),

    # ── Techniques / Methods ─────────────────────────────────────
    ("flash_attention",      "Flash Attention",       "technique",
        ["flash attention", "flash-attention", "flashattention", "flash attn"]),
    ("lora",                 "LoRA",                  "technique",
        ["lora", "low-rank adaptation", "low rank adaptation"]),
    ("qlora",                "QLoRA",                 "technique",
        ["qlora", "quantized lora", "quantized low-rank"]),
    ("dpo",                  "DPO",                   "technique",
        ["direct preference optimization", "DPO"]),
    ("ppo",                  "PPO",                   "technique",
        ["proximal policy optimization", "PPO"]),
    ("grpo",                 "GRPO",                  "technique",
        ["group relative policy optimization", "GRPO"]),
    ("rlhf",                 "RLHF",                  "technique",
        ["reinforcement learning from human feedback", "RLHF"]),
    ("rlaif",                "RLAIF",                 "technique",
        ["reinforcement learning from ai feedback", "RLAIF"]),
    ("sft",                  "SFT",                   "technique",
        ["supervised fine-tuning", "supervised finetuning", "SFT"]),
    ("moe",                  "MoE",                   "technique",
        ["mixture of experts", "mixture-of-experts", "MoE"]),
    ("speculative_decoding", "Speculative Decoding",  "technique",
        ["speculative decoding", "speculative execution", "draft model", "speculative sampling"]),
    ("kv_cache",             "KV Cache",              "technique",
        ["kv cache", "kv-cache", "key-value cache", "key value cache"]),
    ("rag",                  "RAG",                   "technique",
        ["retrieval augmented generation", "retrieval-augmented generation", "RAG",
         "agentic rag", "graph rag", "corrective rag", "modular rag"]),
    ("chain_of_thought",     "Chain-of-Thought",      "technique",
        ["chain of thought", "chain-of-thought", "CoT", "step-by-step reasoning"]),
    ("tree_of_thought",      "Tree-of-Thought",       "technique",
        ["tree of thought", "tree-of-thought", "ToT"]),
    ("quantization",         "Quantization",          "technique",
        ["quantization", "quantised", "int4", "int8", "fp8",
         "weight quantization", "post-training quantization", "PTQ", "QAT", "GPTQ", "AWQ"]),
    ("continuous_batching",  "Continuous Batching",   "technique",
        ["continuous batching", "dynamic batching"]),
    ("paged_attention",      "Paged Attention",       "technique",
        ["paged attention", "paged-attention", "pagedattention"]),
    ("function_calling",     "Function Calling",      "technique",
        ["function calling", "function call", "tool calling", "tool call", "tool use", "tool-use"]),
    ("instruction_tuning",   "Instruction Tuning",    "technique",
        ["instruction tuning", "instruction-tuning", "instruct fine-tuning", "instruction following"]),
    ("reward_model",         "Reward Model",          "technique",
        ["reward model", "reward modeling", "preference model", "reward function"]),
    ("constitutional_ai",    "Constitutional AI",     "technique",
        ["constitutional ai", "CAI"]),
    ("mamba",                "Mamba/SSM",             "technique",
        ["mamba", "state space model", "SSM", "selective state space", "mamba2"]),
    ("vision_language",      "VLM",                   "technique",
        ["vision language model", "vision-language model", "VLM", "LVM",
         "multimodal llm", "MLLM", "image-text model", "visual language model"]),
    ("text_to_image",        "Text-to-Image",         "technique",
        ["text to image", "text-to-image", "image generation", "diffusion model",
         "stable diffusion", "imagen", "DALL-E", "midjourney"]),
    ("text_to_video",        "Text-to-Video",         "technique",
        ["text to video", "text-to-video", "video generation", "video diffusion", "sora"]),
    ("scaling_laws",         "Scaling Laws",          "technique",
        ["scaling laws", "scaling law", "neural scaling"]),
    ("long_context",         "Long Context",          "technique",
        ["long context", "long-context", "extended context", "128k context",
         "1m context", "long sequence"]),
    ("embedding",            "Embedding",             "technique",
        ["embedding model", "sentence embedding", "text embedding", "dense retrieval",
         "vector embedding"]),
    ("pretraining",          "Pre-training",          "technique",
        ["pre-training", "pretraining", "continual pretraining", "continued pretraining"]),
    ("peft",                 "PEFT",                  "technique",
        ["PEFT", "parameter efficient fine-tuning", "parameter-efficient"]),
    ("grounding",            "Grounding",             "technique",
        ["visual grounding", "ui grounding", "screen grounding", "object grounding"]),

    # ── Tools / Frameworks ───────────────────────────────────────
    ("vllm",           "vLLM",            "tool", ["vllm", "vLLM"]),
    ("sglang",         "SGLang",          "tool", ["sglang", "SGLang"]),
    ("langchain",      "LangChain",       "tool", ["langchain", "LangChain"]),
    ("langgraph",      "LangGraph",       "tool", ["langgraph", "LangGraph"]),
    ("llamaindex",     "LlamaIndex",      "tool", ["llamaindex", "llama index", "llama-index"]),
    ("dspy",           "DSPy",            "tool", ["dspy", "DSPy", "declarative self-improving"]),
    ("autogen",        "AutoGen",         "tool", ["autogen", "AutoGen", "microsoft autogen"]),
    ("crewai",         "CrewAI",          "tool", ["crewai", "CrewAI"]),
    ("ollama",         "Ollama",          "tool", ["ollama"]),
    ("tgi",            "TGI",             "tool", ["text generation inference", "TGI", "huggingface tgi"]),
    ("trt_llm",        "TensorRT-LLM",    "tool", ["tensorrt-llm", "tensorrt llm", "trt-llm"]),
    ("transformers",   "Transformers",    "tool", ["huggingface transformers", "hf transformers"]),
    ("faiss",          "FAISS",           "tool", ["FAISS", "facebook ai similarity search"]),
    ("chroma",         "Chroma",          "tool", ["chroma db", "chromadb"]),
    ("weaviate",       "Weaviate",        "tool", ["weaviate"]),
    ("qdrant",         "Qdrant",          "tool", ["qdrant"]),
    ("milvus",         "Milvus",          "tool", ["milvus"]),
    ("openai_agents",  "OpenAI Agents SDK","tool", ["openai agents sdk", "agents sdk"]),
    ("semantic_kernel","Semantic Kernel", "tool", ["semantic kernel", "microsoft semantic kernel"]),
    ("llama_cpp",      "llama.cpp",       "tool", ["llama.cpp", "llamacpp", "llama cpp"]),
    ("lm_studio",      "LM Studio",       "tool", ["lm studio", "lmstudio"]),
    ("deepspeed",      "DeepSpeed",       "tool", ["deepspeed", "DeepSpeed"]),
    ("megatron",       "Megatron-LM",     "tool", ["megatron-lm", "megatron lm", "megatron"]),
    ("axolotl",        "Axolotl",         "tool", ["axolotl"]),
    ("unsloth",        "Unsloth",         "tool", ["unsloth"]),
    ("mcp",            "MCP",             "tool", ["model context protocol", "MCP"]),
    ("openrouter",     "OpenRouter",      "tool", ["openrouter", "open router"]),
    ("instructor",     "Instructor",      "tool", ["instructor library", "instructor python"]),
    ("guidance",       "Guidance",        "tool", ["guidance ai", "microsoft guidance"]),
    ("haystack",       "Haystack",        "tool", ["haystack", "deepset haystack"]),
    ("litellm",        "LiteLLM",         "tool", ["litellm", "LiteLLM"]),
    ("mlx",            "MLX",             "tool", ["mlx framework", "apple mlx"]),
    ("torchtune",      "TorchTune",       "tool", ["torchtune", "torch tune"]),
    ("langfuse",       "Langfuse",        "tool", ["langfuse"]),
    ("langsmith",      "LangSmith",       "tool", ["langsmith", "LangSmith"]),
    ("weave",          "Weave",           "tool", ["weave wandb", "wandb weave"]),

    # ── Organizations ─────────────────────────────────────────────
    ("openai",       "OpenAI",              "org", ["openai", "open ai"]),
    ("anthropic",    "Anthropic",           "org", ["anthropic"]),
    ("google_dm",    "Google DeepMind",     "org", ["google deepmind", "deepmind", "google brain",
                                                     "google research"]),
    ("meta_ai",      "Meta AI",             "org", ["meta ai", "meta llm", "meta research", "FAIR"]),
    ("microsoft",    "Microsoft Research",  "org", ["microsoft research", "microsoft ai", "MSRA"]),
    ("mistral_ai",   "Mistral AI",          "org", ["mistral ai", "mistral.ai"]),
    ("huggingface",  "Hugging Face",        "org", ["hugging face", "huggingface"]),
    ("cohere",       "Cohere",              "org", ["cohere", "cohere ai"]),
    ("nvidia_ai",    "NVIDIA Research",     "org", ["nvidia research", "nvidia ai", "nv labs"]),
    ("together_ai",  "Together AI",         "org", ["together ai", "togetherai"]),
    ("xai",          "xAI",                 "org", ["xAI", "x.ai", "elon musk ai"]),
    ("apple_ml",     "Apple ML",            "org", ["apple intelligence", "apple ml"]),
    ("01ai",         "01.AI",               "org", ["01.ai", "zero-one ai"]),
    ("zhipu",        "Zhipu AI",            "org", ["zhipu", "zhipu ai"]),
    ("moonshot",     "Moonshot AI",         "org", ["moonshot ai", "kimi"]),
    ("aleph_alpha",  "Aleph Alpha",         "org", ["aleph alpha"]),
    ("ai21",         "AI21 Labs",           "org", ["ai21", "ai21 labs", "jurassic"]),
    ("stability",    "Stability AI",        "org", ["stability ai", "stabilityai"]),

    # ── Benchmarks ────────────────────────────────────────────────
    ("mmlu",         "MMLU",          "benchmark", ["MMLU", "massive multitask language understanding"]),
    ("humaneval",    "HumanEval",     "benchmark", ["humaneval", "human eval", "HumanEval"]),
    ("mbpp",         "MBPP",          "benchmark", ["MBPP"]),
    ("math_bench",   "MATH",          "benchmark", ["MATH benchmark", "hendrycks math"]),
    ("gsm8k",        "GSM8K",         "benchmark", ["GSM8K", "grade school math"]),
    ("swebench",     "SWE-bench",     "benchmark", ["swe-bench", "swebench", "swe bench"]),
    ("gpqa",         "GPQA",          "benchmark", ["GPQA", "graduate-level google-proof"]),
    ("aime",         "AIME",          "benchmark", ["AIME"]),
    ("mmmu",         "MMMU",          "benchmark", ["MMMU"]),
    ("hellaswag",    "HellaSwag",     "benchmark", ["hellaswag", "hella swag"]),
    ("agentbench",   "AgentBench",    "benchmark", ["agentbench", "agent bench"]),
    ("webarena",     "WebArena",      "benchmark", ["webarena", "web arena"]),
    ("tau_bench",    "TAU-bench",     "benchmark", ["tau-bench", "tau bench"]),
    ("livecodebench","LiveCodeBench", "benchmark", ["livecodebench", "live code bench"]),
    ("bigbench",     "BIG-Bench",     "benchmark", ["big-bench", "bigbench", "big bench hard", "BBH"]),
    ("arc_bench",    "ARC",           "benchmark", ["ARC challenge", "ai2 reasoning challenge"]),
    ("ifeval",       "IFEval",        "benchmark", ["IFEval", "instruction following eval"]),
    ("lm_eval",      "LM Eval",       "benchmark", ["lm-eval", "lm eval harness", "eleutherai eval"]),
    ("mt_bench",     "MT-Bench",      "benchmark", ["mt-bench", "mt bench"]),
]

# ─────────────────────────────────────────────────────────────────
# 构建查找结构
# ─────────────────────────────────────────────────────────────────

ENTITY_META: Dict[str, Dict[str, str]] = {}
_COMPILED: List[Tuple[str, re.Pattern]] = []


def _build() -> None:
    for eid, display, category, forms in _RAW:
        ENTITY_META[eid] = {"display": display, "category": category}
        for form in forms:
            try:
                pat = re.compile(r"(?<!\w)" + re.escape(form) + r"(?!\w)", re.IGNORECASE)
                _COMPILED.append((eid, pat))
            except re.error:
                pass


_build()

# 按实体 id 分组（用于 extract_entities 去重）
_EID_PATTERNS: Dict[str, List[re.Pattern]] = {}
for _eid, _pat in _COMPILED:
    _EID_PATTERNS.setdefault(_eid, []).append(_pat)


# ─────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────

def extract_entities(text: str) -> List[str]:
    """
    从文本中提取所有命中的实体 ID 列表（去重，按词典顺序）。
    text 通常是 title（加权 ×3）+ desc 拼接。
    """
    if not text:
        return []
    found: List[str] = []
    for eid, pats in _EID_PATTERNS.items():
        for pat in pats:
            if pat.search(text):
                found.append(eid)
                break
    return found


def entities_to_display(entity_ids: List[str]) -> List[Dict[str, str]]:
    """将 entity_id 列表转为带 display/category 的展示格式"""
    result = []
    for eid in entity_ids:
        meta = ENTITY_META.get(eid)
        if meta:
            result.append({"id": eid, "display": meta["display"], "category": meta["category"]})
    return result
