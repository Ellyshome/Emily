# emily-core/emily_core/skill/executor.py
"""SkillExecutor —— 线性执行 Skill 步骤序列。

核心流程：
  对每步 → 校验 tool_name 在白名单 → ParamExtractor.resolve_params() →
  注入 session_scope → 调 BusinessFlowTool.handler() → 存 step_results → 下一步
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

from .definition import SkillDefinition
from .param_extractor import ParamExtractor
from ..workitem.pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from ..tools.business_flow_tools import BusinessFlowToolRegistry

logger = logging.getLogger("emily.skill.executor")


@dataclass
class SkillExecutionContext:
    """Skill 执行上下文。"""

    skill: SkillDefinition
    user_input: str
    user_id: str
    message_id: str
    conversation_id: str
    session_context: dict          # 从 SessionContext 扁平化
    step_results: dict[str, dict]  # output_key → 前步 business_data
    business_flow_tools: BusinessFlowToolRegistry
    llm_client: Any = None         # ParamExtractor 用


class SkillExecutor:
    """Skill 执行引擎。"""

    def __init__(self):
        self._param_extractor: ParamExtractor | None = None

    async def execute(self, ctx: SkillExecutionContext) -> list[StepResult]:
        """线性执行 Skill 步骤序列。"""
        results: list[StepResult] = []
        self._param_extractor = ParamExtractor(llm_client=ctx.llm_client)

        # 构建 tool 白名单集合
        allowed_tools = {t.name for t in ctx.skill.tools}

        for step in ctx.skill.steps:
            t_start = _time.monotonic()

            # 0. 跳过纯逻辑步骤（tool_name 为空/None）
            if not step.tool_name:
                # 纯逻辑步骤：由 instructions 引导 LLM 自行处理，不调用工具
                logger.debug("Step %s: pure logic step, skipping tool call", step.id)
                results.append(StepResult(
                    step_id=step.id,
                    success=True,
                    output=step.description,
                ))
                continue

            # 1. 工具白名单校验
            if step.tool_name not in allowed_tools:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 不在 Skill 工具白名单中",
                ))
                break  # 失败即停止

            # 2. 获取工具
            tool = ctx.business_flow_tools.get(step.tool_name)
            if tool is None:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 未在 BusinessFlowToolRegistry 中注册",
                ))
                break

            try:
                # 3. 解析参数
                tool_params = await self._param_extractor.resolve_params(
                    step.tool_params, ctx.user_input, ctx.session_context, ctx.step_results,
                )

                # 4. 注入运行时上下文 + session_scope
                tool_params["_user_id"] = ctx.user_id
                tool_params["_message_id"] = ctx.message_id
                tool_params["_conversation_id"] = ctx.conversation_id
                tool_params["_session_scope"] = self._build_session_scope(ctx.session_context)

                # 5. 调用工具 handler
                import inspect
                sig = inspect.signature(tool.handler)
                handler_kwargs = {"params": tool_params}
                if "user_id" in sig.parameters:
                    handler_kwargs["user_id"] = ctx.user_id
                if "message_id" in sig.parameters:
                    handler_kwargs["message_id"] = ctx.message_id

                handler_result = await tool.handler(**handler_kwargs)
                handler_dict = handler_result if isinstance(handler_result, dict) else {}

                # 6. 构建 StepResult
                elapsed_ms = int((_time.monotonic() - t_start) * 1000)
                tool_call = ToolCallRecord(
                    tool_name=step.tool_name,
                    tool_input=tool_params,
                    tool_output=handler_dict,
                    success=handler_dict.get("success", True),
                    elapsed_ms=elapsed_ms,
                )

                db_results = []
                object_id = handler_dict.get("object_id", "")
                if object_id:
                    db_results.append(DbResult(
                        operation="insert",
                        table=step.tool_name.replace("record_", "") + "s",
                        affected_rows=1,
                        result_data=handler_dict,
                    ))

                output = handler_dict.get("reply", step.description)
                success = handler_dict.get("success", True)

                sr = StepResult(
                    step_id=step.id,
                    success=success,
                    output=str(output),
                    tool_calls=[tool_call],
                    db_results=db_results,
                    business_data=handler_dict,
                )

                # 7. 存储 output_key → business_data
                if step.output_key and handler_dict:
                    ctx.step_results[step.output_key] = handler_dict

            except Exception as e:
                logger.error("Step %s failed: %s", step.id, e, exc_info=True)
                sr = StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"步骤执行异常: {e}",
                )

            results.append(sr)
            if not sr.success:
                break

        return results

    @staticmethod
    def _build_session_scope(session_context: dict) -> dict:
        """从 session_context 提取数据范围字段。"""
        return {
            "project_ids": session_context.get("project_ids", []),
            "db_perms": session_context.get("db_perms", {}),
            "info_level": session_context.get("info_level", "public"),
            "company_type": session_context.get("company_type", ""),
            "department": session_context.get("department", []),
        }
