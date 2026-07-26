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
    prefilled_params: dict = field(default_factory=dict)  # M2: 路由派生的预填参数
    confirm_callback: Any = None   # M3: SOP-999 写操作确认回调 async fn(tool_name, params) -> bool


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
                        # 单输出键：自动解包 dict（避免 {'title': '测试越权'} 被当 dict 传递）
                        if len(extracted) == 1 and step.output_key in extracted:
                            ctx.step_results[step.output_key] = extracted[step.output_key]
                        else:
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

            # M3: 动态工具选择分支 — step.tool_name == __DYNAMIC__ 时从 prev_step 取 LLM 选定的工具
            if step.tool_name == "__DYNAMIC__":
                sr = await self._execute_dynamic_step(step, ctx)
                results.append(sr)
                if not sr.success:
                    break
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
                    # M2: 先剥离路由预填的参数，剩余参数才走 ParamExtractor
                    prefilled = ctx.prefilled_params or {}
                    pending_mappings = {
                        pname: m for pname, m in step.tool_params.items()
                        if pname not in prefilled
                    }
                    tool_params = dict(prefilled)  # 预填参数直接注入
                    if pending_mappings:
                        resolved = await self._param_extractor.resolve_params(
                            pending_mappings, ctx.user_input, ctx.session_context, ctx.step_results,
                        )
                        tool_params.update(resolved)
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

        M3: SOP-999-SYS 的 step-01 注入派生工具白名单 + schema 到 LLM prompt。
        """
        if self._param_extractor is None:
            self._param_extractor = ParamExtractor(llm_client=ctx.llm_client)

        try:
            # M3: SOP-999-SYS 的 step-01 — 注入工具白名单增强提取
            if ctx.skill.sop_id == "SOP-999-SYS" and ctx.llm_client is not None:
                extracted = await self._extract_sop999_tool_and_params(ctx)
                if extracted:
                    logger.info(
                        "Step %s: SOP-999 tool selection produced: %s",
                        step.id, list(extracted.keys()),
                    )
                return extracted

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

    async def _extract_sop999_tool_and_params(self, ctx: SkillExecutionContext) -> dict:
        """M3: SOP-999 step-01 — 把派生白名单 + 各工具 schema 喂给 LLM，选工具 + 推参。

        Returns:
            {"tool_name": str, "params": dict}
        """
        import json

        # 构建工具白名单描述（含 schema）
        tool_descriptions: list[str] = []
        allowed_tools = {t.name for t in ctx.skill.tools}
        for tool_name in sorted(allowed_tools):
            tool = ctx.business_flow_tools.get(tool_name)
            if tool is None:
                continue
            tool_meta = next(
                (t for t in ctx.session_available_tools if t["api_id"] == tool_name), {}
            )
            desc = f"- {tool_name}: {tool.description}"
            if tool.parameters:
                desc += f"\n  参数: {json.dumps(tool.parameters, ensure_ascii=False)}"
            if tool_meta.get("permission_flag"):
                desc += f"\n  权限: {tool_meta['permission_flag']}"
            tool_descriptions.append(desc)

        tools_text = "\n".join(tool_descriptions) if tool_descriptions else "（无可用的工具）"

        prompt = (
            f"用户请求：「{ctx.user_input}」\n\n"
            f"以下是可以使用的工具白名单（只能从中选择一个最合适的）：\n"
            f"{tools_text}\n\n"
            f"Session 上下文：{json.dumps(ctx.session_context, ensure_ascii=False, default=str)}\n\n"
            f"请选择一个最匹配用户意图的工具，并推导所需参数。\n"
            f"注意：\n"
            f"1. 如果白名单中没有合适的工具，返回 {{\"tool_name\": \"\", \"params\": {{}}}}\n"
            f"2. 缺必填参数时不要猜测，把你能推导的参数填上，缺的用 null 表示\n"
            f"3. 只返回 JSON 对象，格式：{{\"tool_name\": \"工具名\", \"params\": {{...}}}}"
        )

        try:
            result = await ctx.llm_client.chat_messages([
                {"role": "user", "content": prompt},
            ])
            content = result.get("content", "{}") if isinstance(result, dict) else "{}"
            if content.startswith("```"):
                content = content.strip("`").strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            data = json.loads(content) if content else {}
            tool_name = data.get("tool_name", "")
            params = data.get("params") or {}

            if not tool_name:
                logger.info("SOP-999 step-01: LLM selected no tool for '%s'", ctx.user_input[:50])
                return {"tool_name": "", "params": {}}

            if tool_name not in allowed_tools:
                logger.warning("SOP-999 step-01: LLM selected tool '%s' not in whitelist", tool_name)
                return {"tool_name": "", "params": {}}

            # schema 强校验：检查必填字段
            tool = ctx.business_flow_tools.get(tool_name)
            if tool and tool.parameters:
                required_fields = tool.parameters.get("required", [])
                properties = tool.parameters.get("properties", {})
                missing = [f for f in required_fields if f not in params or params[f] is None]
                if missing:
                    logger.info(
                        "SOP-999 step-01: tool '%s' missing required params: %s",
                        tool_name, missing,
                    )
                    # 不猜测，返回空 tool_name 让上层向用户询问
                    return {"tool_name": "", "params": {}}

            return {"tool_name": tool_name, "params": params}

        except Exception as e:
            logger.warning("SOP-999 _extract_sop999_tool_and_params failed: %s", e)
            return {"tool_name": "", "params": {}}

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

    # ── M3: __DYNAMIC__ 动态工具执行 ──

    async def _execute_dynamic_step(self, step, ctx: SkillExecutionContext) -> StepResult:
        """M3: 执行 __DYNAMIC__ 步骤——从 prev_step 取 tool_name + params，校验后直调。

        prev_step 的 output_key（约定 "tool_call"）含 {tool_name, params}。
        写类工具（permission_flag != all）走 confirm_callback 拟执行确认。
        """
        import inspect

        # 1. 从 step_results 取 prev_step 输出
        tool_call = ctx.step_results.get("tool_call") or {}
        tool_name = tool_call.get("tool_name", "")
        params = tool_call.get("params") or {}

        if not tool_name:
            return StepResult(
                step_id=step.id, success=False,
                output="动态工具选择失败：未提供 tool_name",
            )

        # 2. 校验白名单（SOP-999 派生 tools）
        allowed = {t.name for t in ctx.skill.tools}
        if tool_name not in allowed:
            return StepResult(
                step_id=step.id, success=False,
                output=f"工具 '{tool_name}' 不在 SOP-999 派生白名单中",
            )

        # 校验 Session 可见 API
        session_api_ids = {t["api_id"] for t in ctx.session_available_tools}
        if tool_name not in session_api_ids:
            return StepResult(
                step_id=step.id, success=False,
                output=f"工具 '{tool_name}' 不在 Session 可见 API 中",
            )

        # 3. 取工具 handler
        tool = ctx.business_flow_tools.get(tool_name)
        if tool is None:
            return StepResult(
                step_id=step.id, success=False,
                output=f"工具 '{tool_name}' 未在 BusinessFlowToolRegistry 注册",
            )

        # 4. 写操作强制确认：permission_flag != all 先走 confirm_callback
        tool_meta = next(
            (t for t in ctx.session_available_tools if t["api_id"] == tool_name), {}
        )
        perm_flag = tool_meta.get("permission_flag", "all")
        if perm_flag != "all":
            if ctx.confirm_callback is not None:
                try:
                    confirmed = await ctx.confirm_callback(tool_name, params)
                    if not confirmed:
                        return StepResult(
                            step_id=step.id, success=False,
                            output="用户取消执行",
                        )
                except Exception as e:
                    logger.warning("confirm_callback failed for %s: %s", tool_name, e)
                    # 确认机制异常时默认拒绝（安全优先）
                    return StepResult(
                        step_id=step.id, success=False,
                        output=f"写操作确认失败，已拒绝执行: {e}",
                    )
            else:
                # 无 confirm_callback 时，写操作不允许（安全优先）
                return StepResult(
                    step_id=step.id, success=False,
                    output=f"工具 '{tool_name}' 为写操作（permission_flag={perm_flag}），需要确认回调但未提供",
                )

        try:
            t_start = _time.monotonic()

            # 5. 注入运行时上下文 + session_scope
            params["_user_id"] = ctx.user_id
            params["_message_id"] = ctx.message_id
            params["_conversation_id"] = ctx.conversation_id
            params["_session_scope"] = self._build_session_scope(ctx.session_context)

            # 6. 调用工具 handler
            sig = inspect.signature(tool.handler)
            handler_kwargs = {"params": params}
            if "user_id" in sig.parameters:
                handler_kwargs["user_id"] = ctx.user_id
            if "message_id" in sig.parameters:
                handler_kwargs["message_id"] = ctx.message_id

            handler_result = await tool.handler(**handler_kwargs)
            handler_dict = handler_result if isinstance(handler_result, dict) else {}

            # 7. 构建 StepResult
            elapsed_ms = int((_time.monotonic() - t_start) * 1000)
            tool_call_record = ToolCallRecord(
                tool_name=tool_name,
                tool_input=params,
                tool_output=handler_dict,
                success=handler_dict.get("success", True),
                elapsed_ms=elapsed_ms,
            )
            # 审计标记：SOP-999 LLM 自主直调
            setattr(tool_call_record, "trigger", "sop999")

            db_results = []
            object_id = handler_dict.get("object_id", "")
            if object_id:
                db_results.append(DbResult(
                    operation="insert",
                    table=tool_name.replace("record_", "") + "s",
                    affected_rows=1,
                    result_data=handler_dict,
                ))

            output = handler_dict.get("reply", step.description)
            success = handler_dict.get("success", True)

            sr = StepResult(
                step_id=step.id,
                success=success,
                output=str(output),
                tool_calls=[tool_call_record],
                db_results=db_results,
                business_data=handler_dict,
            )

            if step.output_key and handler_dict:
                ctx.step_results[step.output_key] = handler_dict

            return sr

        except Exception as e:
            logger.error("_execute_dynamic_step %s failed: %s", tool_name, e, exc_info=True)
            return StepResult(
                step_id=step.id,
                success=False,
                output=f"动态工具执行异常: {e}",
            )

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
