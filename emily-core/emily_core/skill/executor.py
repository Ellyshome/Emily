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
    session_available_tools: list[dict] = field(default_factory=list)


class SkillExecutor:
    """Skill 执行引擎。"""

    def __init__(self):
        self._param_extractor: ParamExtractor | None = None

    async def execute(self, ctx: SkillExecutionContext) -> list[StepResult]:
        """线性执行 Skill 步骤序列。"""
        results: list[StepResult] = []
        self._param_extractor = ParamExtractor(llm_client=ctx.llm_client)

        # 构建工具可见性集合
        allowed_tools = {t.name for t in ctx.skill.tools}
        session_api_ids = {t["api_id"] for t in ctx.session_available_tools}

        for step in ctx.skill.steps:
            t_start = _time.monotonic()

            # 0. 处理纯逻辑步骤（tool_name 为空/None）
            if not step.tool_name:
                # 即使无工具，若有 tool_params 且含 source:user_input，仍需 LLM 提取数据
                if step.tool_params:
                    extracted = await self._extract_null_step_data(step, ctx)
                    if step.output_key and extracted:
                        ctx.step_results[step.output_key] = extracted
                    results.append(StepResult(
                        step_id=step.id,
                        success=True,
                        output=str(extracted) if extracted else step.description,
                        business_data=extracted,
                    ))
                else:
                    logger.debug("Step %s: pure logic step, no extraction needed", step.id)
                    results.append(StepResult(
                        step_id=step.id,
                        success=True,
                        output=step.description,
                    ))
                continue

            # 1. 检查工具是否在 Session 可见 API 中
            if step.tool_name not in session_api_ids:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 不在 Session 可见 API 中",
                ))
                break

            # 2. 获取工具（在 BusinessFlowToolRegistry 中查找）
            tool = ctx.business_flow_tools.get(step.tool_name)
            if tool is None:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 未在 BusinessFlowToolRegistry 中注册",
                ))
                break

            try:
                # 3. 解析参数 —— 区分 Skill 推荐路径 vs 元能力路径
                if step.tool_name in allowed_tools:
                    # Skill 推荐路径：有 ParamMapping，走现有逻辑
                    tool_params = await self._param_extractor.resolve_params(
                        step.tool_params, ctx.user_input, ctx.session_context, ctx.step_results,
                    )
                else:
                    # 元能力路径：Skill YAML 没定义 ParamMapping，LLM 动态推导
                    tool_params = await self._llm_resolve_params(step.tool_name, tool, ctx)

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

    async def _extract_null_step_data(
        self, step, ctx: SkillExecutionContext,
    ) -> dict:
        """对 tool_name=null 但含 tool_params 的步骤，用 LLM 提取数据。

        这是 Bug #1-5 的根因修复：旧逻辑直接跳过 null-tool 步骤，
        导致 step_results 为空，后续 prev_step 引用全部失败。

        策略：遍历 step.tool_params，对 source=user_input 的参数调 LLM 提取，
        对 source=prev_step/context/fixed 的参数走原有逻辑，
        将提取结果合并为 dict 存入 step_results[output_key]。
        """
        if self._param_extractor is None:
            self._param_extractor = ParamExtractor(llm_client=ctx.llm_client)

        try:
            extracted = await self._param_extractor.resolve_params(
                step.tool_params, ctx.user_input, ctx.session_context, ctx.step_results,
            )
            if extracted:
                logger.info(
                    "Step %s: null-tool extraction produced %d fields: %s",
                    step.id, len(extracted), list(extracted.keys()),
                )
            return extracted
        except ValueError as e:
            # 必填参数提取失败——记录但不中断（空步骤不应因参数缺失阻断管道）
            logger.warning("Step %s: null-tool extraction failed: %s", step.id, e)
            return {}
        except Exception as e:
            logger.warning("Step %s: null-tool extraction error: %s", step.id, e)
            return {}

    async def _llm_resolve_params(self, tool_name: str, tool, ctx: SkillExecutionContext) -> dict:
        """元能力路径：LLM 动态推导工具参数（Skill YAML 未定义 ParamMapping 时使用）。

        从用户输入和 Session 上下文中推导参数，不依赖 ParamMapping。
        """
        if ctx.llm_client is None:
            return {"query": ctx.user_input}

        try:
            import json
            prompt = (
                f"用户请求：「{ctx.user_input}」\n"
                f"需要调用工具：{tool_name}\n"
                f"工具描述：{tool.description}\n"
                f"工具参数定义：{json.dumps(tool.parameters, ensure_ascii=False)}\n"
                f"Session 上下文：{json.dumps(ctx.session_context, ensure_ascii=False, default=str)}\n"
                f"\n请从用户消息和 Session 上下文中提取工具参数，只返回 JSON 对象。"
            )
            result = await ctx.llm_client.chat_messages([
                {"role": "user", "content": prompt},
            ])
            content = result.get("content", "{}") if isinstance(result, dict) else "{}"
            # 清理可能的 markdown 代码块包装
            if content.startswith("```"):
                content = content.strip("`").strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            return json.loads(content) if content else {}
        except Exception as e:
            logger.warning("_llm_resolve_params failed for %s: %s", tool_name, e)
            return {"query": ctx.user_input}

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
