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

# 工具调用依赖 agent_loop_model (v4-pro) 标准 function calling，不再使用 DSML 正则解析。
# text fallback 精准纠错兜底：agent 返回文本时诊断内容特征（DSML/JSON/纯文本）并给出针对性纠正。

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
        model: str = "deepseek-v4-flash",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        router_model: str = "",
        guardian_model: str = "",
        agent_loop_model: str = "",
    ):
        self.model = model
        self.router_model = router_model or model
        self.guardian_model = guardian_model or model
        self.agent_loop_model = agent_loop_model or self.router_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        # M11: LLM 交互追踪回调
        self._trace_callback: Callable | None = None
        logger.info(
            "LLMClient initialized: model=%s, router_model=%s, guardian_model=%s, base_url=%s, temperature=%.2f",
            model, self.router_model, self.guardian_model, base_url, temperature,
        )

    # ── M11: Trace callback ──

    def set_trace_callback(self, callback: Callable | None):
        """设置 LLM 交互追踪回调。callback(phase, data) 在调用前后各触发一次。

        phase="start": data = {call_type, model, message_count, tool_count}
        phase="end":   data = {response_type, response_summary, finish_reason, ...}
        """
        self._trace_callback = callback

    # ── 多轮对话主入口（统一 chat / chat_json / chat_with_tools）──

    async def chat_messages(
        self,
        messages: list[dict],
        *,
        json_mode: bool = False,
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> dict:
        """多轮对话主入口 —— 接受完整 OpenAI 格式 messages 列表。

        综合 chat_json（JSON 输出）+ chat_with_tools（多轮 + 工具）的能力。
        chat / chat_json / chat_with_tools 内部均转调此方法。

        Args:
            messages: OpenAI 格式消息列表 [{"role":..., "content":..., ...}]
            json_mode: 是否强制 JSON 输出（response_format）
            tools: 工具定义列表（可选）
            temperature: 采样温度（None 则用默认值）
            max_tokens: 最大输出 token（None 则用默认值）
            model: 按调用覆盖模型（None 则用 self.model）。用于路由/审核等
                   轻量节点切换到 chat 模型，合成/摘要保留 reasoner

        Returns:
            dict:
                - json_mode=True:  {"type": "json", "data": {...}, "finish_reason": str}
                - json_mode=False: {"type": "text", "content": str, "finish_reason": str}
                如果有 tool_call:     {"type": "tool_call", "tool_name": str, "tool_arguments": dict, ...}
        """
        # M11: trace callback — start
        call_seq = getattr(self, "_trace_call_seq", 0) + 1
        self._trace_call_seq = call_seq
        call_type = "chat_messages" + ("_json" if json_mode else "")
        effective_model = model or self.model
        if self._trace_callback:
            try:
                self._trace_callback({
                    "phase": "start",
                    "call_type": call_type,
                    "call_sequence": call_seq,
                    "model": effective_model,
                    "message_count": len(messages),
                    "tool_count": len(tools) if tools else 0,
                    "json_mode": json_mode,
                })
            except Exception as e:
                logger.debug("trace callback start failed: %s", e, exc_info=True)

        t0 = time.time()

        kwargs = dict(
            model=effective_model,
            messages=messages,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
        )
        # 推理类模型（deepseek-reasoner / deepseek-v4-pro）不支持 temperature 等采样参数
        # （传了会 400），仅 chat 类模型（deepseek-chat / deepseek-v4-flash）传
        _model_name = (effective_model or "").lower()
        if "reasoner" not in _model_name and "v4-pro" not in _model_name:
            kwargs["temperature"] = temperature if temperature is not None else self.temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            # tools 不被支持时的回退
            if tools and "tools " in str(e).lower() and "tool_calls" not in str(e).lower():
                logger.warning("Tools not supported, falling back: %s", e)
                del kwargs["tools"]
                try:
                    response = await self._client.chat.completions.create(**kwargs)
                except Exception as e2:
                    logger.error("LLM chat_messages fallback failed: %s", e2)
                    raise
            elif json_mode and "response_format" in str(e).lower():
                # json_mode 不被支持时的回退
                logger.warning("LLM json_mode not supported, falling back: %s", e)
                del kwargs["response_format"]
                response = await self._client.chat.completions.create(**kwargs)
            else:
                logger.error("LLM chat_messages failed: %s", e)
                raise

        choice = response.choices[0]
        finish_reason = choice.finish_reason or ""
        reasoning_content = getattr(choice.message, "reasoning_content", None) or ""
        elapsed_ms = int((time.time() - t0) * 1000)

        # tool_call 分支
        if choice.message.tool_calls:
            tc = choice.message.tool_calls[0]
            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool arguments: %s", tc.function.arguments)
                arguments = {}

            logger.debug("LLM tool call: %s(%s)", tc.function.name, str(arguments)[:200])

            if self._trace_callback:
                try:
                    self._trace_callback({
                        "phase": "end", "call_type": call_type, "call_sequence": call_seq,
                        "model": effective_model, "json_mode": json_mode,
                        "response_type": "tool_call",
                        "response_summary": f"{tc.function.name}({str(arguments)[:200]})",
                        "response_full": f"{tc.function.name}({arguments})",
                        "reasoning_content": reasoning_content,
                        "finish_reason": finish_reason,
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                        "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                        "prompt_cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", 0) if response.usage else 0,
                        "prompt_cache_miss_tokens": getattr(response.usage, "prompt_cache_miss_tokens", 0) if response.usage else 0,
                        "latency_ms": elapsed_ms,
                    })
                except Exception as e:
                    logger.debug("trace callback end failed: %s", e, exc_info=True)

            return {
                "type": "tool_call",
                "tool_name": tc.function.name,
                "tool_arguments": arguments,
                "tool_call_id": tc.id,
                "finish_reason": finish_reason,
                "reasoning_content": reasoning_content,
            }

        content = choice.message.content or ""

        logger.debug("LLM chat_messages: %d chars, finish=%s, elapsed=%dms",
                     len(content), finish_reason, elapsed_ms)

        # 防御性日志：json_mode 下 content 空白（reasoner 可能把输出放进 reasoning_content）
        if json_mode and not content.strip() and reasoning_content:
            logger.warning(
                "LLM json_mode returned empty content (model=%s, finish=%s, reasoning_len=%d) — "
                "output may be in reasoning_content",
                effective_model, finish_reason, len(reasoning_content),
            )

        # M11: trace callback — end
        if self._trace_callback:
            try:
                self._trace_callback({
                    "phase": "end", "call_type": call_type, "call_sequence": call_seq,
                    "model": effective_model, "json_mode": json_mode,
                    "response_type": "json" if json_mode else "text",
                    "response_summary": content[:500],
                    "response_full": content,
                    "reasoning_content": reasoning_content,
                    "finish_reason": finish_reason,
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                    "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                    "prompt_cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", 0) if response.usage else 0,
                    "prompt_cache_miss_tokens": getattr(response.usage, "prompt_cache_miss_tokens", 0) if response.usage else 0,
                    "latency_ms": elapsed_ms,
                })
            except Exception as e:
                logger.debug("trace callback end failed: %s", e, exc_info=True)

        if json_mode:
            data = self._parse_json_response(content)
            return {"type": "json", "data": data, "finish_reason": finish_reason}

        return {"type": "text", "content": content, "finish_reason": finish_reason}

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """解析 LLM 返回的 JSON 文本（含 markdown 代码块回退）。"""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass
            logger.error("Failed to parse LLM response as JSON: %s", raw[:500])
            raise ValueError(f"LLM response is not valid JSON: {raw[:200]}")

    # ── 便捷方法（thin wrappers，保持现有调用者兼容）──

    async def chat(self, system_prompt: str, user_message: str) -> str:
        """单轮对话，返回原始文本。"""
        result = await self.chat_messages([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])
        return result["content"]

    async def chat_json(self, system_prompt: str, user_message: str, model: str | None = None) -> dict:
        """单轮对话，强制 JSON 输出，返回解析后的 dict。"""
        result = await self.chat_messages([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ], json_mode=True, model=model)
        return result["data"]

    # ── M7: Tool Calling ──

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """多轮对话，支持 function calling（转调 chat_messages）。"""
        return await self.chat_messages(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )
