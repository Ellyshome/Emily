# emily-core/emily_core/workitem/langgraph_engine/error_analysis.py
"""ErrorAnalyzer —— node3 失败后的错误分析器（Self-Reflection 模式）。

职责：
  1. 从 BusContext.work_item.step_results 找到失败的 step
  2. 代码预分类：权限失败 / L3 副作用已执行 → 直接 abort（不调 LLM，省钱+安全）
  3. LLM 分析：不确定的错误 → 加载 error_analysis.md prompt → chat_json → 结构化分析
  4. LLM 失败兜底：默认 transient_failure（允许重试一次）

错误分类 taxonomy（route_after_analysis 据此路由）：
  - param_error        → 重规划（replan_hint 指出参数问题）
  - tool_mismatch      → 重规划（replan_hint 建议换工具）
  - transient_failure  → 直接重试 node3（省 LLM 重新规划）
  - missing_info       → abort + 回复追问用户
  - permission_denied  → abort（重规划无意义）
  - permanent_failure  → abort（L3 副作用已发生，避免二次副作用）
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.langgraph.error_analysis")


# ════════════════════════════════════════════════════════════════════
# 错误分类
# ════════════════════════════════════════════════════════════════════


class ErrorType:
    """错误类型枚举（字符串常量，便于 JSON 序列化）。"""
    PARAM_ERROR = "param_error"              # 参数错误（缺字段/类型错/值非法）
    TOOL_MISMATCH = "tool_mismatch"          # 选错工具（该查询却录入/该录入却查询）
    TRANSIENT_FAILURE = "transient_failure"  # 瞬时故障（网络超时/服务暂时不可用）
    MISSING_INFO = "missing_info"            # 用户信息不足，需追问
    PERMISSION_DENIED = "permission_denied"  # 权限不足，不可恢复
    PERMANENT_FAILURE = "permanent_failure"  # 不可恢复（L3 副作用已执行）


# 路由分组（route_after_analysis 使用）
REPLAN_TYPES = {ErrorType.PARAM_ERROR, ErrorType.TOOL_MISMATCH}    # → node2 重规划
RETRY_TYPES = {ErrorType.TRANSIENT_FAILURE}                        # → node3 直接重试
ABORT_TYPES = {ErrorType.MISSING_INFO, ErrorType.PERMISSION_DENIED, ErrorType.PERMANENT_FAILURE}  # → END

# L3 高风险工具（失败即 permanent_failure，不重试不重规划）
L3_TOOLS = {"discard_nodes", "return_node_deliverable"}

# 权限失败关键词（代码预分类，不调 LLM）
_PERMISSION_KEYWORDS = ("权限", "无权", "permission", "forbidden", "未授权", "没有相应权限")


# ════════════════════════════════════════════════════════════════════
# prompt 惰性加载
# ════════════════════════════════════════════════════════════════════

_ERROR_ANALYSIS_PROMPT: str | None = None


def _load_error_analysis_prompt() -> str:
    """惰性加载 error_analysis.md prompt（参照 _load_planner_prompt 模式）。"""
    global _ERROR_ANALYSIS_PROMPT
    if _ERROR_ANALYSIS_PROMPT is None:
        from emily_core.infrastructure.llm.prompt_loader import load_prompt
        _ERROR_ANALYSIS_PROMPT = load_prompt("error_analysis")
    return _ERROR_ANALYSIS_PROMPT


# ════════════════════════════════════════════════════════════════════
# ErrorAnalyzer
# ════════════════════════════════════════════════════════════════════


class ErrorAnalyzer:
    """错误分析器 —— 分析 node3 失败原因，产出 error_type + replan_hint。"""

    def __init__(self, llm_client=None, config=None):
        self._llm = llm_client
        self._config = config

    # ── 入口 ──

    async def analyze(self, ctx) -> dict:
        """分析 node3 失败原因。

        Args:
            ctx: BusContext（读 ctx.work_item.step_results 找失败 step）

        Returns:
            分析结果 dict
        """
        wi = ctx.work_item
        failed_step = self._find_failed_step(wi)
        if failed_step is None:
            logger.warning("error_analysis: no failed step found, fallback to transient")
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

        # ① 代码预分类（省 LLM 调用）
        pre_type = self._code_pre_classify(failed_step)
        if pre_type == ErrorType.PERMISSION_DENIED:
            logger.info("error_analysis: code-classified as PERMISSION_DENIED (no LLM)")
            return self._build_result(
                ErrorType.PERMISSION_DENIED,
                root_cause="权限不足，重规划无法解决",
                should_abort=True,
            )
        if pre_type == ErrorType.PERMANENT_FAILURE:
            logger.info("error_analysis: code-classified as PERMANENT_FAILURE (L3 executed, no LLM)")
            return self._build_result(
                ErrorType.PERMANENT_FAILURE,
                root_cause="L3 高风险工具已执行，不可重试（避免二次副作用）",
                should_abort=True,
            )

        # ② LLM 分析（不确定的错误）
        if not self._llm:
            logger.info("error_analysis: no LLM, fallback to transient_failure")
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

        try:
            return await self._llm_analyze(ctx, failed_step)
        except Exception as e:
            logger.warning("error_analysis LLM failed: %s, fallback to transient_failure", e)
            return self._build_result(ErrorType.TRANSIENT_FAILURE, should_retry=True)

    # ── 内部方法 ──

    def _find_failed_step(self, wi) -> Any:
        """从 step_results 找第一个失败的 step。"""
        for sr in getattr(wi, "step_results", []) or []:
            if not getattr(sr, "success", True):
                return sr
        return None

    def _code_pre_classify(self, failed_step) -> str | None:
        """代码预分类：权限失败 / L3 副作用 → 直接返回，不调 LLM。"""
        output = getattr(failed_step, "output", "") or ""
        tool_name = self._get_step_tool_name(failed_step)

        if any(kw in output for kw in _PERMISSION_KEYWORDS):
            return ErrorType.PERMISSION_DENIED

        if tool_name and tool_name in L3_TOOLS:
            return ErrorType.PERMANENT_FAILURE

        return None

    def _get_step_tool_name(self, step_result) -> str:
        """从 StepResult 取工具名。"""
        for tc in getattr(step_result, "tool_calls", []) or []:
            tn = getattr(tc, "tool_name", "") or ""
            if tn:
                return tn
        return ""

    async def _llm_analyze(self, ctx, failed_step) -> dict:
        """LLM 分析错误根因。"""
        wi = ctx.work_item

        failed_step_id = getattr(failed_step, "step_id", "?")
        failed_tool_name = self._get_step_tool_name(failed_step) or "（无工具）"
        failed_output = (getattr(failed_step, "output", "") or "")[:500]
        failed_tool_params = "{}"
        for tc in getattr(failed_step, "tool_calls", []) or []:
            failed_tool_params = str(getattr(tc, "tool_input", "{}"))[:500]
            break

        plan_summary = self._summarize_plan(getattr(wi, "execution_plan", None))

        session_ctx = ctx.get_session_context() if ctx else None
        available_tools = self._list_available_tools(session_ctx)

        prompt_template = _load_error_analysis_prompt()
        system_prompt = prompt_template.format(
            failed_step_id=failed_step_id,
            failed_tool_name=failed_tool_name,
            failed_tool_params=failed_tool_params,
            error_output=failed_output,
            original_plan=plan_summary,
            user_input=(wi.user_input or "")[:300],
            available_tools=available_tools,
        )

        try:
            from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
            LLMInteractionLogger.set_context(
                pipeline_run_id=ctx.pipeline_run_id,
                conversation_id=ctx.message.conversation_id if ctx.message else "",
                user_id=ctx.user_id,
                call_category="error_analysis",
            )
        except Exception:
            pass

        try:
            result = await self._llm.chat_messages(
                [{"role": "system", "content": system_prompt},
                 {"role": "user", "content": "分析上述失败原因并返回 JSON。"}],
                json_mode=True,
            )
            data = result.get("data", {}) or {}
        finally:
            try:
                from emily_core.infrastructure.logging.llm_logger import LLMInteractionLogger
                LLMInteractionLogger.clear_context()
            except Exception:
                pass

        return self._parse_llm_result(data)

    def _summarize_plan(self, plan) -> str:
        """摘要原始计划（step_id + tool_name 列表）。"""
        if plan is None:
            return "（无计划）"
        steps = getattr(plan, "steps", []) or []
        lines = []
        for s in steps:
            sid = getattr(s, "step_id", "?")
            tn = getattr(s, "tool_name", "") or "（无工具）"
            lines.append(f"  - {sid}: {tn}")
        return "\n".join(lines) if lines else "（空计划）"

    def _list_available_tools(self, session_ctx) -> str:
        """列出用户有权限的工具。"""
        if session_ctx is None:
            return "（无 SessionContext）"
        tools = getattr(session_ctx, "available_tools", []) or []
        names = []
        for t in tools:
            if isinstance(t, dict):
                api_id = t.get("api_id", "")
                if api_id:
                    names.append(api_id)
            else:
                names.append(str(t))
        return ", ".join(sorted(names)) if names else "（无可用工具）"

    def _parse_llm_result(self, data: dict) -> dict:
        """解析 LLM 返回的 JSON，校验 error_type 合法性。"""
        error_type = data.get("error_type", "")
        valid_types = {
            ErrorType.PARAM_ERROR, ErrorType.TOOL_MISMATCH, ErrorType.TRANSIENT_FAILURE,
            ErrorType.MISSING_INFO, ErrorType.PERMISSION_DENIED, ErrorType.PERMANENT_FAILURE,
        }
        if error_type not in valid_types:
            logger.warning("error_analysis: LLM returned invalid error_type=%r, fallback to transient", error_type)
            error_type = ErrorType.TRANSIENT_FAILURE

        replan_hint = data.get("replan_hint", "") or ""
        should_replan = error_type in REPLAN_TYPES
        should_retry = error_type in RETRY_TYPES
        should_abort = error_type in ABORT_TYPES

        return self._build_result(
            error_type=error_type,
            root_cause=data.get("root_cause", ""),
            replan_hint=replan_hint,
            should_replan=should_replan,
            should_retry=should_retry,
            should_abort=should_abort,
            user_prompt=data.get("user_prompt", ""),
        )

    def _build_result(
        self,
        error_type: str,
        root_cause: str = "",
        replan_hint: str = "",
        should_replan: bool = False,
        should_retry: bool = False,
        should_abort: bool = False,
        user_prompt: str = "",
    ) -> dict:
        """构建标准化分析结果 dict。"""
        return {
            "error_type": error_type,
            "root_cause": root_cause,
            "replan_hint": replan_hint,
            "should_replan": should_replan,
            "should_retry": should_retry,
            "should_abort": should_abort,
            "user_prompt": user_prompt,
        }
