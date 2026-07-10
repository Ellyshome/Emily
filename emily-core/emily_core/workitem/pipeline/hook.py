"""Hook 基类 + 三态返回值 + 5 个具体 Hook 子类 — M12a 管道总线架构。

借鉴 Claude Code Harness 的 exit code 语义：
  - ALLOW (exit 0): 放行
  - WARN  (exit 1): 非致命警告，继续执行但追加提示
  - BLOCK (exit 2): 否决，立即终止整个管道

deny always wins — 任一 hook 返回 BLOCK，整个管道立即终止。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import BusContext as PipelineContext

logger = logging.getLogger("emily.pipeline.hook")


# ════════════════════════════════════════════════════════════════════════════════
# 三态决策
# ════════════════════════════════════════════════════════════════════════════════

class HookDecision(Enum):
    """Hook 执行决策 — 借鉴 Claude Code Harness exit code 语义。"""
    ALLOW = "allow"   # 放行，等价于 exit code 0
    WARN = "warn"     # 非致命警告，继续执行但追加提示，等价于 exit code 1
    BLOCK = "block"   # 否决，立即终止整个管道，等价于 exit code 2


@dataclass
class HookResult:
    """Hook 执行结果 — 三态返回值。"""
    decision: HookDecision = HookDecision.ALLOW
    message: str = ""          # 警告信息或阻断原因
    metadata: dict = field(default_factory=dict)  # 附加数据（审计日志条目等）

    @property
    def is_blocked(self) -> bool:
        return self.decision == HookDecision.BLOCK

    @staticmethod
    def allow() -> "HookResult":
        return HookResult(decision=HookDecision.ALLOW)

    @staticmethod
    def warn(msg: str) -> "HookResult":
        return HookResult(decision=HookDecision.WARN, message=msg)

    @staticmethod
    def block(msg: str) -> "HookResult":
        return HookResult(decision=HookDecision.BLOCK, message=msg)


# ════════════════════════════════════════════════════════════════════════════════
# Hook 基类
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class Hook:
    """Hook 基类 — 所有业务 hook 的父类。

    Attributes:
        name: 唯一标识，如 "auth.project_read"
        priority: 执行优先级，数字越小越先执行（before 钩子中 0=鉴权最先）
        enabled: 是否启用
    """
    name: str
    priority: int = 10  # 默认中等优先级
    enabled: bool = True

    async def execute(self, context: "PipelineContext") -> HookResult:
        """子类重写此方法实现具体逻辑。

        Args:
            context: 管道上下文，包含消息、用户、意图等全部阶段状态。

        Returns:
            HookResult: 三态决策结果。
        """
        raise NotImplementedError(f"Hook [{self.name}] must implement execute()")


# ════════════════════════════════════════════════════════════════════════════════
# 具体 Hook 子类
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class AuthHook(Hook):
    """鉴权钩子 — before 阶段，可阻断。

    检查用户对特定资源的访问权限。

    阶段二改造（需求 §4 + §14）：
    - 接入 SessionContext 三维鉴权（扁平化字段）
    - system.execute 检查 level >= 5
    - 有 SOP 绑定时检查 sop_allow 白名单
    - 密级/企业类型/部门维度校验委托 AuthEngine
    """
    resource_type: str = ""   # "system" | "project" | "event" | "task" | "file"
    action: str = ""           # "read" | "create" | "update" | "delete" | "execute"

    async def execute(self, context: "PipelineContext") -> HookResult:
        """执行鉴权检查（阶段二：三维鉴权 + SessionContext 扁平化字段）。"""
        user_id = context.user_id
        if not user_id:
            if self.action and self.action not in ("read",):
                logger.info(
                    "AuthHook[%s] blocking: no user_id for action=%s res=%s",
                    self.name, self.action, self.resource_type,
                )
                return HookResult.block(f"需要登录才能执行 {self.action} 操作")
            return HookResult.allow()

        # 获取 SessionContext
        session_ctx = context.get_session_context()

        # 管理员检查: system.execute 只允许 L5+
        if self.resource_type == "system" and self.action == "execute":
            if session_ctx is None:
                is_admin_flag = getattr(context, "is_admin", False) or context.get("is_admin", False)
                if not is_admin_flag:
                    logger.info("AuthHook[%s] blocking: user %s is not admin", self.name, user_id)
                    return HookResult.block("仅管理员可执行系统级操作")
            else:
                from ...permission.level import is_admin as _is_admin
                if not _is_admin(session_ctx.level):
                    logger.info(
                        "AuthHook[%s] blocking: user %s level=%d < L5",
                        self.name, user_id, session_ctx.level,
                    )
                    return HookResult.block("仅管理员（L5+）可执行系统级操作")

        # SOP 权限检查：有 SOP 绑定时验证白名单
        intent = context.intent
        sop_id = getattr(intent, "sop_id", None) if intent else None
        if sop_id and session_ctx is not None:
            if sop_id not in session_ctx.sop_allow and "all" not in session_ctx.sop_allow:
                logger.info(
                    "AuthHook[%s] blocking: user %s no access to SOP %s",
                    self.name, user_id, sop_id,
                )
                reason = f"无权访问 {sop_id}"
                if session_ctx.supervisor_id:
                    reason += f"，可联系主管 {session_ctx.supervisor_id} 申请权限"
                return HookResult.block(reason)

        logger.debug("AuthHook[%s] allow: user=%s res=%s action=%s",
                     self.name, user_id, self.resource_type, self.action)
        return HookResult.allow()


@dataclass
class AuditHook(Hook):
    """审计钩子 — after 阶段，fire-and-forget，不阻断。

    写入审计记录到 event_journal 和 hook_execution_logs 表。
    """
    event_type: str = ""  # "message_received" | "sop_invoked" | "sop_completed" | ...

    async def execute(self, context: "PipelineContext") -> HookResult:
        """异步写审计记录，失败只记日志不抛异常。增强版：写入 user_id/sop_id/block_reason/session_level。"""
        try:
            from ...infrastructure.database.session import get_session
            from ...infrastructure.database.models import HookExecutionLog
            from datetime import datetime, timezone
            import time as _time

            t0 = _time.time()
            run_id = context.pipeline_run_id

            # ── 增强字段采集 ──
            user_id = context.user_id or ""
            sop_id = ""
            session_level = None
            session_ctx = context.get_session_context()
            if session_ctx is not None:
                session_level = session_ctx.level

            intent = context.intent
            if intent:
                sop_id = getattr(intent, "sop_id", "") or ""

            # 判断当前 hook 是否为 BLOCK（从 before hook 已设的 abort_reason 推断）
            block_reason = ""
            if context.should_abort and context.abort_reason:
                block_reason = context.abort_reason[:500]

            log_entry = HookExecutionLog(
                hook_name=self.name,
                mount_point=f"after:{context.current_stage}",
                pipeline_run_id=run_id,
                phase="after",
                decision="block" if block_reason else "allow",
                message=f"audit: {self.event_type}",
                duration_ms=int((_time.time() - t0) * 1000),
                metadata_json="{}",
                # ── 增强字段 ──
                user_id=user_id,
                sop_id=sop_id,
                block_reason=block_reason,
                session_level=session_level,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            with get_session() as session:
                session.add(log_entry)
                session.commit()
        except Exception as e:
            logger.warning("AuditHook[%s] failed (non-blocking): %s", self.name, e)
        return HookResult.allow()


@dataclass
class TraceHook(Hook):
    """追踪钩子 — 记录 Agent 推理过程（M11 追踪逻辑迁移）。

    挂载点: before:execute（开始追踪）、after:execute（结束追踪）
    """
    trace_level: str = "summary"  # "summary" | "full"
    agent_trace_service: Any = None  # AgentTraceService 实例，由 EmilyCore 注入

    async def execute(self, context: "PipelineContext") -> HookResult:
        """记录 Agent 推理追踪。"""
        if self.agent_trace_service is None:
            return HookResult.allow()

        try:
            if self.name == "trace.reasoning_start":
                reasoning_id = self.agent_trace_service.create_reasoning_log(
                    message_id=context.db_message_id,
                    user_id=context.user_id,
                    conversation_id=context.message.conversation_id if context.message else "",
                    matched_sop_id=getattr(context.intent, "sop_id", None),
                    match_confidence=getattr(context.intent, "confidence", "none"),
                    is_compound=bool(context.sub_tasks),
                    fallback=getattr(context.intent, "fallback", False),
                )
                context.set("_trace_reasoning_id", reasoning_id)

            elif self.name == "trace.reasoning_end":
                reasoning_id = context.get("_trace_reasoning_id", "")
                if reasoning_id:
                    agent_result = context.agent_result
                    self.agent_trace_service.update_reasoning_log(
                        reasoning_log_id=reasoning_id,
                        iteration_count=context.get("trace_iteration_count", 0),
                        elapsed_ms=context.get("trace_elapsed_ms", 0),
                        execution_result="success" if agent_result and agent_result.success else "failed",
                        reply_preview=(context.agent_reply or "")[:500],
                        error_message=getattr(agent_result, "error_code", "") if agent_result else "",
                    )

            return HookResult.allow()
        except Exception as e:
            logger.warning("TraceHook[%s] failed (non-blocking): %s", self.name, e)
            return HookResult.allow()


@dataclass
class ProgressHook(Hook):
    """前导消息钩子 — 发送"正在处理..."前导消息（M8b 逻辑迁移）。

    挂载点: after:decompose
    """
    progress_sender: Any = None  # async fn(text: str) -> None
    progress_template: str = "收到，正在为你{action}，请稍候..."
    enable_progress: bool = True

    async def execute(self, context: "PipelineContext") -> HookResult:
        """发送前导消息。

        优先从 context.baggage 动态获取 progress_sender（支持每条消息的 event 闭包），
        回退到构建时注入的实例属性。
        """
        sender = context.baggage.get("progress_sender", self.progress_sender)
        if not self.enable_progress or sender is None:
            return HookResult.allow()

        template = context.baggage.get("progress_template", self.progress_template)

        try:
            intent = context.intent
            action = "处理"
            if intent and getattr(intent, "sop_id", None):
                sop_id = intent.sop_id
                action_map = {
                    "SOP-001": "整理会议纪要",
                    "SOP-002": "录入事件",
                    "SOP-003": "管理任务",
                    "SOP-004": "归档文件",
                    "SOP-005": "查询数据",
                    "SOP-006": "执行守护审计",
                    "SOP-007": "管理记忆",
                    "SOP-008": "处理待办问题",
                }
                action = action_map.get(sop_id, "处理")
            progress_text = template.format(action=action)
            await sender(progress_text)
            logger.info("ProgressHook[%s] sent: %s", self.name, progress_text)
        except Exception as e:
            logger.warning("ProgressHook[%s] failed (non-blocking): %s", self.name, e)
        return HookResult.allow()


# ════════════════════════════════════════════════════════════════════════════════
# Hook 类型字符串 → 类映射表
# ════════════════════════════════════════════════════════════════════════════════

HOOK_TYPE_MAP: dict[str, type[Hook]] = {
    "auth": AuthHook,
    "audit": AuditHook,
    "trace": TraceHook,
    "progress": ProgressHook,
}
