"""
LLM 摘要生成服务

支持 OpenAI 和 Claude API，自动为文章生成高质量摘要
"""
import os
import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import httpx


class LLMProvider(Enum):
    """LLM 提供商"""
    OPENAI = "openai"
    CLAUDE = "claude"
    MODELVERSE = "modelverse"  # 优云智算国内 API


@dataclass
class LLMConfig:
    """LLM 配置"""
    provider: LLMProvider = LLMProvider.OPENAI
    api_key: str = ""
    model: str = "gpt-3.5-turbo"  # 或 claude-3-haiku-20240307
    max_tokens: int = 300
    temperature: float = 0.3
    timeout: float = 30.0
    
    def __post_init__(self):
        if not self.api_key:
            if self.provider == LLMProvider.OPENAI:
                self.api_key = os.getenv("OPENAI_API_KEY", "")
            elif self.provider == LLMProvider.CLAUDE:
                self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
            else:  # MODELVERSE
                self.api_key = os.getenv("MODELVERSE_API_KEY", "")
                # 默认使用 qwen-turbo (性价比高)
                if self.model == "gpt-3.5-turbo":
                    self.model = "qwen-turbo"


class LLMSummaryService:
    """
    LLM 摘要生成服务
    
    为文章自动生成高质量中文摘要
    """
    
    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or LLMConfig()
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.config.timeout,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                }
            )
        return self._client
    
    async def generate_summary(
        self,
        title: str,
        content: str,
        max_length: int = 200,
    ) -> Optional[str]:
        """
        生成文章摘要
        
        Args:
            title: 文章标题
            content: 文章内容（Markdown 或纯文本）
            max_length: 摘要最大长度
            
        Returns:
            生成的摘要，失败返回 None
        """
        if not self.config.api_key:
            return None
        
        # 截断内容避免超出 token 限制
        content = content[:4000] + "..." if len(content) > 4000 else content
        
        prompt = f"""请为以下文章生成一个简洁的中文摘要，{max_length}字以内：

标题：{title}

内容：
{content}

请只输出摘要内容，不要添加任何其他说明。"""
        
        try:
            if self.config.provider == LLMProvider.OPENAI:
                return await self._call_openai(prompt, max_length)
            elif self.config.provider == LLMProvider.CLAUDE:
                return await self._call_claude(prompt, max_length)
            else:  # MODELVERSE
                return await self._call_modelverse(prompt, max_length)
        except Exception:
            return None
    
    async def _call_openai(self, prompt: str, max_length: int) -> Optional[str]:
        """调用 OpenAI API"""
        client = await self._get_client()
        
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": "你是一个专业的文章摘要生成助手。请生成简洁、准确、涵盖核心观点的中文摘要。"},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
            }
        )
        response.raise_for_status()
        
        data = response.json()
        summary = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return summary.strip() if summary else None

    async def chat_completion(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
    ) -> Optional[str]:
        """通用对话补全（用于周评等长文）。"""
        if not self.config.api_key:
            return None
        mt = max_tokens if max_tokens is not None else self.config.max_tokens
        try:
            if self.config.provider == LLMProvider.OPENAI:
                return await self._chat_openai(system, user, mt)
            if self.config.provider == LLMProvider.CLAUDE:
                return await self._chat_claude(system, user, mt)
            return await self._chat_modelverse_combined(system, user, mt)
        except Exception:
            return None

    async def _chat_openai(self, system: str, user: str, max_tokens: int) -> Optional[str]:
        client = await self._get_client()
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": self.config.temperature,
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return text.strip() if text else None

    async def _chat_claude(self, system: str, user: str, max_tokens: int) -> Optional[str]:
        client = await self._get_client()
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.config.model,
                "max_tokens": max_tokens,
                "temperature": self.config.temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("content") or []
        if not parts:
            return None
        text = parts[0].get("text", "")
        return text.strip() if text else None

    async def _chat_modelverse_combined(
        self, system: str, user: str, max_tokens: int
    ) -> Optional[str]:
        combined = f"{system}\n\n{user}"
        mv_client = httpx.AsyncClient(
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            response = await mv_client.post(
                "https://api.modelverse.cn/v1/responses",
                json={
                    "model": self.config.model,
                    "input": [{"type": "text", "text": combined}],
                },
            )
            response.raise_for_status()
            data = response.json()
            output = data.get("output", [])
            if output:
                text = output[0].get("text", "")
                return text.strip() if text else None
            return None
        finally:
            await mv_client.aclose()

    async def generate_weekly_digest_chinese(self, material_block: str) -> Optional[str]:
        """根据英文条目材料生成一段中文周评。"""
        system = "你是 AI 与机器学习领域的技术编辑，读者主要是工程师与研究者。请严格根据给定材料写作，不要编造材料中未出现的事实。"
        user = f"""下面是一条聚合阅读列表中的条目（标题与简介多为英文）。请据此写一段 **中文「本周前沿综述」**，字数约 450–750 字。

要求：
1. 归纳 3–6 个主题方向（例如：模型与算法、推理与效率、训练与数据、RAG/Agent、开源与工具、云厂商与产品动态等），不要逐条照抄标题。
2. 每个方向用 1–3 句中文概括「这周值得关注什么」，语气客观，偏工程与可落地。
3. 只根据材料推断；材料未提及的具体版本号、数字、发布日期不要捏造。
4. 不要使用 Markdown # 标题，用自然分段即可；开头可有一两句总起。

--- 材料 ---
{material_block}
--- 结束 ---

请直接输出正文。"""
        return await self.chat_completion(system, user, max_tokens=2048)
    
    async def _call_claude(self, prompt: str, max_length: int) -> Optional[str]:
        """调用 Claude API"""
        client = await self._get_client()
        
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.config.model,
                "max_tokens": self.config.max_tokens,
                "temperature": self.config.temperature,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
            }
        )
        response.raise_for_status()
        
        data = response.json()
        summary = data.get("content", [{}])[0].get("text", "")
        return summary.strip() if summary else None
    
    async def _call_modelverse(self, prompt: str, max_length: int) -> Optional[str]:
        """调用 Modelverse API (优云智算)"""
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"[Modelverse] 开始调用, model={self.config.model}")
        logger.info(f"[Modelverse] API Key 前10位: {self.config.api_key[:10]}...")
        
        # Modelverse 使用特殊的 base_url
        # 需要重新创建客户端以使用不同的 base_url
        modelverse_client = httpx.AsyncClient(
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            }
        )
        
        try:
            request_body = {
                "model": self.config.model,
                "input": [
                    {"type": "text", "text": prompt}
                ],
            }
            logger.info(f"[Modelverse] 请求体: {request_body}")
            
            response = await modelverse_client.post(
                "https://api.modelverse.cn/v1/responses",
                json=request_body
            )
            
            logger.info(f"[Modelverse] 响应状态: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"[Modelverse] 错误响应: {response.text[:500]}")
                response.raise_for_status()
            
            data = response.json()
            logger.info(f"[Modelverse] 响应数据: {str(data)[:500]}")
            
            # Modelverse 响应格式: { "output": [{"type": "text", "text": "..."}] }
            output = data.get("output", [])
            if output and len(output) > 0:
                summary = output[0].get("text", "")
                logger.info(f"[Modelverse] 成功获取摘要, 长度={len(summary)}")
                return summary.strip() if summary else None
            else:
                logger.warning(f"[Modelverse] 响应中无 output 字段: {data.keys()}")
            return None
            
        except Exception as e:
            logger.error(f"[Modelverse] 调用异常: {str(e)}")
            return None
        finally:
            await modelverse_client.aclose()
    
    async def generate_summary_for_article(
        self,
        title: str,
        content: str,
        existing_desc: str = "",
    ) -> Dict[str, Any]:
        """
        为文章生成完整摘要信息
        
        Returns:
            {"summary": "...", "tags": [...], "success": bool}
        """
        # 组合内容
        full_text = f"{title}\n\n{content}"
        if existing_desc and len(existing_desc) > 50:
            full_text += f"\n\n现有描述：{existing_desc}"
        
        summary = await self.generate_summary(title, full_text)
        
        if summary:
            # 提取关键词作为标签
            from ..utils.text import infer_tags
            tags = infer_tags(title + " " + summary)
            
            return {
                "summary": summary,
                "tags": tags,
                "success": True,
            }
        
        return {
            "summary": existing_desc or title,
            "tags": [],
            "success": False,
        }
    
    async def batch_generate_summaries(
        self,
        articles: List[Dict[str, Any]],
        max_concurrent: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        批量生成摘要
        
        Args:
            articles: [{"title": ..., "content": ..., "desc": ...}]
            max_concurrent: 最大并发数
            
        Returns:
            带摘要的文章列表
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_one(article: Dict[str, Any]) -> Dict[str, Any]:
            async with semaphore:
                result = await self.generate_summary_for_article(
                    title=article.get("title", ""),
                    content=article.get("full_content", article.get("desc", "")),
                    existing_desc=article.get("desc", ""),
                )
                
                article["llm_summary"] = result.get("summary", "")
                article["llm_tags"] = result.get("tags", [])
                article["llm_enhanced"] = result.get("success", False)
                return article
        
        tasks = [process_one(article) for article in articles]
        return await asyncio.gather(*tasks, return_exceptions=True)
    
    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None


# 全局服务实例
_llm_service: Optional[LLMSummaryService] = None


def get_llm_service() -> LLMSummaryService:
    """获取全局 LLM 服务实例"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMSummaryService()
    return _llm_service


def reset_llm_service():
    """重置全局服务（测试用）"""
    global _llm_service
    _llm_service = None
