# emily-core/emily_core/skill/param_extractor.py
"""Parameter Extractor —— 从四种 source 解析参数值。

source 详解：
  user_input → 调 LLM chat_json 提取
  prev_step  → 从前步结果按 dot-path 取值
  fixed      → 使用固定值（today/now 特殊处理）
  context    → 从 session-context 字段取值
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .definition import ParamMapping

logger = logging.getLogger("emily.skill.param_extractor")


def _resolve_dot_path(data: dict, path: str) -> Any:
    """按 dot-path 从嵌套 dict 取值。如 'project_info.object_id' → data['project_info']['object_id']
    支持 list 索引：'project_ids.0' → data['project_ids'][0]"""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


class ParamExtractor:
    """参数提取引擎。"""

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def resolve_params(
        self,
        tool_params_mapping: dict[str, ParamMapping],
        user_input: str,
        session_context: dict,
        step_results: dict[str, dict],
    ) -> dict[str, Any]:
        """解析一步的全部参数。"""
        resolved: dict[str, Any] = {}

        for pname, mapping in tool_params_mapping.items():
            value = await self._resolve_one(mapping, user_input, session_context, step_results)
            if value is None:
                if mapping.required:
                    raise ValueError(
                        f"必填参数 '{pname}' 提取失败 (source={mapping.source})"
                    )
                value = mapping.default
            # enum 约束
            if mapping.enum and value is not None and value not in mapping.enum:
                logger.warning("参数 '%s' 值 '%s' 不在枚举 %s 中，使用 default=%s",
                               pname, value, mapping.enum, mapping.default)
                value = mapping.default
            # max_length 约束
            if mapping.max_length and isinstance(value, str) and len(value) > mapping.max_length:
                value = value[:mapping.max_length]

            if value is not None:
                resolved[pname] = value

        return resolved

    async def _resolve_one(
        self,
        mapping: ParamMapping,
        user_input: str,
        session_context: dict,
        step_results: dict[str, dict],
    ) -> Any:
        """解析单个参数。"""
        source = mapping.source

        if source == "fixed":
            return self._extract_from_fixed(mapping)
        elif source == "context":
            return self._extract_from_context(mapping, session_context)
        elif source == "prev_step":
            return self._extract_from_prev_step(mapping, step_results)
        elif source == "user_input":
            return await self._extract_from_user_input(mapping, user_input)
        else:
            logger.warning("未知 source: %s", source)
            return None

    def _extract_from_fixed(self, mapping: ParamMapping) -> Any:
        """解析固定值。today → 当前日期，now → 当前时间戳。"""
        value = mapping.value
        if isinstance(value, str):
            if value == "today":
                return datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if value == "now":
                return datetime.now(timezone.utc).isoformat()
        return value

    def _extract_from_context(self, mapping: ParamMapping, session_context: dict) -> Any:
        """从 session-context 取值。"""
        return _resolve_dot_path(session_context, mapping.path)

    def _extract_from_prev_step(self, mapping: ParamMapping, step_results: dict[str, dict]) -> Any:
        """从前步结果取值。"""
        return _resolve_dot_path(step_results, mapping.path)

    async def _extract_from_user_input(self, mapping: ParamMapping, user_input: str) -> Any:
        """调 LLM 从用户消息中提取值。"""
        if self._llm is None:
            logger.warning("LLM 不可用，无法提取 user_input 参数: %s", mapping.extraction)
            return mapping.default

        try:
            from ..infrastructure.llm.prompt_loader import load_prompt
            prompt_template = load_prompt("param_extraction")
        except Exception:
            prompt_template = (
                "请从用户消息中提取指定字段的值。\n\n"
                "字段：{extraction}\n"
                "约束：{constraints}\n\n"
                "用户消息：\n{user_input}\n\n"
                '仅输出 JSON：{{"value": "提取的值"}}\n'
                '若无法提取且非必填，输出：{{"value": null}}'
            )

        constraints_parts = []
        if mapping.required:
            constraints_parts.append("required=true")
        if mapping.max_length:
            constraints_parts.append(f"max_length={mapping.max_length}")
        if mapping.enum:
            constraints_parts.append(f"enum={mapping.enum}")

        prompt = prompt_template.format(
            extraction=mapping.extraction,
            constraints=", ".join(constraints_parts) if constraints_parts else "无特殊约束",
            user_input=user_input[:1000],
        )

        try:
            # chat_json(system_prompt, user_message) -> dict
            # system_prompt 含提取指令与约束，user_message 传入原始用户消息便于模型聚焦
            # 用 router_model（v4-flash）：参数提取是轻量任务，主模型（v4-pro）是 reasoner，
            # reasoning_content 可能占满 max_tokens 导致 content 被截断为空（finish=length）
            router_model = getattr(self._llm, "router_model", None)
            result = await self._llm.chat_json(prompt, user_input, model=router_model)
            value = result.get("value") if isinstance(result, dict) else None
            return value
        except Exception as e:
            logger.warning("LLM 参数提取失败: %s — %s", mapping.extraction, e)
            return mapping.default
