"""OpenAI SDK 封装 —— 兼容 DeepSeek / OpenAI / 其他兼容 API。

所有方法都是 async，异步边界在 Application 层以上。
Repository / Service 层保持同步（只做 DB 操作）。

M7: 新增 chat_with_tools() 方法，支持 OpenAI function calling。
"""

import json
import logging
import time
from typing import Callable, Optional

from openai import AsyncOpenAI

logger = logging.getLogger("emily.llm")


class LLMClient:
    """异步 LLM 客户端，封装 OpenAI SDK。

    Args:
        api_key: API 密钥
        base_url: API 基础 URL（兼容 DeepSeek 等）
        model: 模型名称
        temperature: 采样温度（路由场景建议 0.1）
        max_tokens: 最大输出 token 数
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        # M11: LLM 交互追踪回调
        self._trace_callback: Callable | None = None
        logger.info(
            "LLMClient initialized: model=%s, base_url=%s, temperature=%.2f",
            model, base_url, temperature,
        )

    # ── M11: Trace callback ──

    def set_trace_callback(self, callback: Callable | None):
        """设置 LLM 交互追踪回调。callback(phase, data) 在调用前后各触发一次。

        phase="start": data = {call_type, model, message_count, tool_count}
        phase="end":   data = {response_type, response_summary, finish_reason, ...}
        """
        self._trace_callback = callback

    async def chat(self, system_prompt: str, user_message: str) -> str:
        """单轮对话，返回原始文本。

        Args:
            system_prompt: 系统提示词
            user_message: 用户消息

        Returns:
            str: 模型回复文本
        """
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            content = response.choices[0].message.content or ""
            logger.debug("LLM chat response: %d chars", len(content))
            return content
        except Exception as e:
            logger.error("LLM chat failed: %s", e)
            raise

    async def chat_json(self, system_prompt: str, user_message: str) -> dict:
        """单轮对话，强制 JSON 输出，返回解析后的 dict。

        使用 response_format={"type": "json_object"} 强制模型输出 JSON。
        如果模型不支持该参数，则回退到手动解析。

        Args:
            system_prompt: 系统提示词（应包含"请输出 JSON"的指示）
            user_message: 用户消息

        Returns:
            dict: 解析后的 JSON 对象

        Raises:
            ValueError: 无法解析为有效 JSON
        """
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or ""
        except Exception as e:
            # 回退：不支持 response_format 的模型
            logger.warning("LLM chat_json with response_format failed, fallback: %s", e)
            raw = await self.chat(system_prompt, user_message)

        # 解析 JSON
        try:
            # 尝试直接解析
            return json.loads(raw)
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass
            logger.error("Failed to parse LLM response as JSON: %s", raw[:500])
            raise ValueError(f"LLM response is not valid JSON: {raw[:200]}")

    # ── M7: Tool Calling ──

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """多轮对话，支持 function calling。

        发送消息历史 + 工具定义给 LLM，返回文本回复或工具调用指令。

        Args:
            messages: OpenAI 格式的消息列表
                [{"role": "system/user/assistant/tool", "content": "...", ...}, ...]
            tools: OpenAI 格式的工具定义列表
                [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}, ...]
            temperature: 采样温度（None 则用默认值）
            max_tokens: 最大输出 token（None 则用默认值）

        Returns:
            dict:
                - type="text": {"type": "text", "content": str, "finish_reason": str}
                - type="tool_call": {"type": "tool_call", "tool_name": str, "tool_arguments": dict, "tool_call_id": str, "finish_reason": str}
        """
        # M11: trace callback — start
        call_seq = getattr(self, "_trace_call_seq", 0) + 1
        self._trace_call_seq = call_seq
        if self._trace_callback:
            try:
                self._trace_callback({
                    "phase": "start",
                    "call_type": "chat_with_tools",
                    "call_sequence": call_seq,
                    "model": self.model,
                    "message_count": len(messages),
                    "tool_count": len(tools) if tools else 0,
                })
            except Exception:
                pass

        t0 = time.time()
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools if tools else None,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            )
        except Exception as e:
            # 如果 tools 参数不被支持（某些兼容 API），回退到无 tools 调用
            if tools and "tools" in str(e).lower():
                logger.warning("Tools not supported by model, falling back to text-only: %s", e)
                try:
                    response = await self._client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        temperature=temperature if temperature is not None else self.temperature,
                        max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
                    )
                except Exception as e2:
                    logger.error("LLM chat_with_tools fallback also failed: %s", e2)
                    raise
            else:
                logger.error("LLM chat_with_tools failed: %s", e)
                raise

        choice = response.choices[0]
        finish_reason = choice.finish_reason or ""

        # 提取 reasoning_content（DeepSeek thinking mode 返回的思考链）
        # 在 tool call 场景下，reasoning_content 必须在后续消息中原样回传，
        # 否则 API 返回 400: "Missing reasoning_content field in the assistant message"
        reasoning_content = getattr(choice.message, "reasoning_content", None) or ""

        # 检查是否有工具调用
        if choice.message.tool_calls:
            # 取第一个 tool_call（MVP 简化：每次只处理一个）
            tc = choice.message.tool_calls[0]
            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool arguments: %s", tc.function.arguments)
                arguments = {}

            logger.debug(
                "LLM tool call: %s(%s)",
                tc.function.name,
                str(arguments)[:200],
            )
            elapsed_ms = int((time.time() - t0) * 1000)

            # M11: trace callback — end
            if self._trace_callback:
                try:
                    self._trace_callback({
                        "phase": "end",
                        "call_type": "chat_with_tools",
                        "call_sequence": call_seq,
                        "response_type": "tool_call",
                        "response_summary": f"{tc.function.name}({str(arguments)[:200]})",
                        "finish_reason": finish_reason,
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                        "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                        "latency_ms": elapsed_ms,
                    })
                except Exception:
                    pass

            return {
                "type": "tool_call",
                "tool_name": tc.function.name,
                "tool_arguments": arguments,
                "tool_call_id": tc.id,
                "finish_reason": finish_reason,
                "reasoning_content": reasoning_content,
            }

        # 纯文本回复
        content = choice.message.content or ""
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.debug("LLM text response: %d chars", len(content))

        # M11: trace callback — end (text response)
        if self._trace_callback:
            try:
                self._trace_callback({
                    "phase": "end",
                    "call_type": "chat_with_tools",
                    "call_sequence": call_seq,
                    "response_type": "text",
                    "response_summary": (content or "")[:500],
                    "finish_reason": finish_reason,
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                    "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                    "latency_ms": elapsed_ms,
                })
            except Exception:
                pass

        return {
            "type": "text",
            "content": content,
            "finish_reason": finish_reason,
        }
