"""SOP 能力执行模块（M3）—— 一个 SOP 一个能力。

定位（计划 M3）：
  - 把"一个启用中的 SOP"包装为一个可被会话主循环调用的能力；
  - 外部只暴露成果（结构化 + 可读文本），不暴露 WorkItem 实体与状态；
  - 内部复用现有执行引擎（SessionScheduler._run_one → core._workitem_graph），
    因此 SOP 全文、质量门、专家评审、分级兜底、审计全部保留。

命名契约（计划 v1.1 修订，实测三种 sop_id 形态后确定）：
  - 能力名 = 短形编号（SOP-002-REC）—— 与 SessionContext.sop_allow 同形，否则权限过滤恒为空；
  - 执行期 wi.sop_id = 文件 stem 全文（SOP-002-REC-event_record）—— 保持 experts 绑定
    与 _load_sop_text 的 glob 行为不变。

准入契约：sops/ 下的系统/元规范/图内触发/工具直调兜底类 SOP 不作为用户可调用能力，
由 SYSTEM_INTERNAL_SOPS 排除；旧路径仍需其 .md，**不得删除文件**。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("emily.session.capability_runner")


# ══════════════════════════════════════════════════════════════════════════════
# 能力准入与命名契约
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_INTERNAL_SOPS: frozenset = frozenset({
    "SOP-000-SYS",   # 标准/元规范，非业务诉求
    "SOP-012-SYS",   # 由执行图内 expert_review 节点自身触发，非用户诉求
    "SOP-999-SYS",   # 工具直调兜底；新主循环直接调工具，天然吸收其职责
})
"""非用户可调用能力（按短形编号匹配）。"""


def short_sop_id(stem: str) -> str:
    """SOP 文件 stem → 短形编号（取前 3 段）。

    'SOP-002-REC-event_record' → 'SOP-002-REC'
    'SOP-999-SYS-fallback'     → 'SOP-999-SYS'
    """
    parts = (stem or "").split("-")
    if len(parts) >= 3 and parts[0] == "SOP":
        return "-".join(parts[:3])
    return stem or ""


def is_capability_sop(stem: str) -> bool:
    """是否为用户可调用能力（准入判定）。"""
    if not stem or not stem.startswith("SOP-"):
        return False
    return short_sop_id(stem) not in SYSTEM_INTERNAL_SOPS


# ══════════════════════════════════════════════════════════════════════════════
# 能力成果
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CapabilityResult:
    """能力对外唯一成果形态。

    约束 B1：不含 WorkItem 实体字段（id/state 等）；readable_text 由 summary_facts
    拼装，不出现工单编号与状态机词汇。
    """

    status: str = "failed"                       # success | partial | failed
    summary: list = field(default_factory=list)   # 关键成果事实
    data: dict = field(default_factory=dict)      # 结构化成果数据
    business_object_no: str = ""                  # 业务编号（EVT-xxx 等）
    issues: list = field(default_factory=list)    # 问题/失败原因
    readable_text: str = ""                       # 面向对话的可读成果
    needs_input: bool = False                     # 是否缺参需用户补充（挂起）
    question: str = ""                            # 需要用户补充的问题
    elapsed_ms: int = 0

    @property
    def success(self) -> bool:
        return self.status == "success"

    def to_tool_dict(self) -> dict:
        """转现有工具 handler 返回约定（供 tool_result 回灌与日志）。"""
        return {
            "success": self.status == "success",
            "status": self.status,
            "reply": self.readable_text,
            "summary": list(self.summary),
            "data": self.data,
            "business_object_no": self.business_object_no,
            "issues": list(self.issues),
            "needs_input": self.needs_input,
            "question": self.question,
            "elapsed_ms": self.elapsed_ms,
        }

    @classmethod
    def from_structured_result(cls, sr, *, needs_input: bool = False,
                               question: str = "", elapsed_ms: int = 0) -> "CapabilityResult":
        summary = [str(s) for s in (getattr(sr, "summary_facts", None) or [])]
        issues = [str(i) for i in (getattr(sr, "issues", None) or [])]
        text = "；".join(summary) if summary else (issues[0] if issues else "")
        return cls(
            status=str(getattr(sr, "status", "") or "failed"),
            summary=summary,
            data=dict(getattr(sr, "data", None) or {}),
            business_object_no=str(getattr(sr, "business_object_no", "") or ""),
            issues=issues,
            readable_text=text,
            needs_input=needs_input,
            question=question,
            elapsed_ms=elapsed_ms,
        )

    @classmethod
    def failed(cls, reason: str, *, elapsed_ms: int = 0) -> "CapabilityResult":
        return cls(status="failed", issues=[reason], readable_text=reason,
                   elapsed_ms=elapsed_ms)


# ══════════════════════════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SopCapability:
    """一个 SOP 能力的注册条目。"""

    name: str                                # 短形编号，= 能力工具名，如 SOP-002-REC
    sop_id: str                              # 文件 stem，执行期传给 WorkItem
    display_name: str = ""                   # 业务名称（SOP 首行标题）
    description: str = ""                    # 能力描述（供 LLM 选择）
    tool: Any = None                         # BusinessFlowTool（承载 name/description/parameters）


class CapabilityRegistry:
    """SOP 能力注册表。

    独立于旧的 `core._business_flow_tools`：若把 SOP 能力混入旧注册表，旧路径的
    `build_tool_specs` 也会看到它们，可能形成"SOP 能力 → WorkItem → agent loop →
    再调 SOP 能力"的递归。此处作为对称于 `tools/registry.py` 的独立注册通道（C11）。
    """

    def __init__(self) -> None:
        self._caps: dict = {}

    def register(self, cap: SopCapability) -> None:
        if cap.name in self._caps:
            logger.warning("capability '%s' already registered — overwrite", cap.name)
        self._caps[cap.name] = cap

    def get(self, name: str):
        return self._caps.get(name)

    def has(self, name: str) -> bool:
        return name in self._caps

    def list_names(self) -> list:
        return sorted(self._caps.keys())

    def list_all(self) -> list:
        return [self._caps[k] for k in self.list_names()]

    def __len__(self) -> int:
        return len(self._caps)

    def __contains__(self, name: str) -> bool:
        return name in self._caps


# ══════════════════════════════════════════════════════════════════════════════
# 执行器
# ══════════════════════════════════════════════════════════════════════════════

_SOP_CAPABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "request": {
            "type": "string",
            "description": "用户的原始诉求原文（原样传入，不要改写、不要自行补全缺失信息）",
        },
        "additional_input": {
            "type": "string",
            "description": "上一轮用户补充的信息（缺参续接时填写），无则留空",
        },
    },
    "required": ["request"],
}


class SopCapabilityRunner:
    """SOP 能力执行器 —— 复用现有执行引擎，对外只回传成果。"""

    def __init__(self, core=None, config=None) -> None:
        self._core = core
        self._config = config if config is not None else getattr(core, "config", None)

    async def run(
        self,
        sop_id: str,
        user_input: str,
        *,
        name: str = "",
        message: Any = None,
        db_message_id: str = "",
        actor_snapshot: dict | None = None,
        session_context: Any = None,
    ) -> CapabilityResult:
        """执行单个 SOP 能力。

        契约（计划 M3）：**不抛异常**（异常/取消转结构化结果），保证 wi 落终态。
        """
        t0 = time.monotonic()
        if not sop_id:
            return CapabilityResult.failed("未指定业务流程")

        # 延迟导入，避免 session → workitem 的模块级循环依赖
        from ..workitem import WorkItem
        from ..workitem.scheduler import SessionScheduler
        from ..workitem.workitem_state import WorkItemState

        actor = actor_snapshot or {}
        user_id = actor.get("user_id") or (getattr(session_context, "user_id", "") or "")
        conversation_id = (
            getattr(session_context, "conversation_id", "")
            or (getattr(message, "conversation_id", "") if message else "")
        )
        short = name or short_sop_id(sop_id)

        wi = None
        try:
            wi = WorkItem(
                session_id=conversation_id,
                user_input=user_input or "",
                user_id=user_id,
                sop_id=sop_id,
                intent_type="sop",
                priority=0,
            )
            wi.output_spec = {
                "intent": short, "detail": "standard", "format": "natural",
                "cite_source": False, "max_length": 300, "data_fields": [],
            }
            wi.work_spec = {
                "objective": short, "sop_id": sop_id,
                "user_request": (user_input or "")[:500],
                "output_spec": wi.output_spec, "constraints": {}, "required_tools": [],
            }

            scheduler = SessionScheduler(
                session_id=conversation_id, session_context=session_context, core=self._core,
            )
            # 与 run_all_with_message 同法：注入当前操作者快照（权限 fail-closed 依赖它）
            scheduler._current_actor = actor
            await scheduler._run_one(wi, message=message, db_message_id=db_message_id)

            elapsed = int((time.monotonic() - t0) * 1000)

            # 缺参挂起 → 交由会话循环提问（US-06）
            if wi.state == WorkItemState.WAITING_FOR_INPUT:
                q = getattr(wi, "question", "") or "请补充信息"
                return CapabilityResult(
                    status="partial", readable_text=q,
                    needs_input=True, question=q, elapsed_ms=elapsed,
                )

            sr = getattr(wi, "structured_result", None)
            if sr is None:
                err = getattr(wi, "error_message", "") or ""
                reason = "未能产出业务成果" + (f"：{err}" if err else "")
                return CapabilityResult.failed(reason, elapsed_ms=elapsed)

            return CapabilityResult.from_structured_result(sr, elapsed_ms=elapsed)

        except asyncio.CancelledError:
            # 超时/取消（如 M1 的 wait_for）：保证 wi 落终态后原样上抛
            _mark_terminal(wi, WorkItemState, "能力调用超时或被取消")
            raise
        except Exception as e:  # noqa: BLE001 — 契约要求不抛异常
            logger.error("SOP capability %s failed: %s", sop_id, e, exc_info=True)
            _mark_terminal(wi, WorkItemState, str(e))
            return CapabilityResult.failed(
                f"业务流程执行异常：{e}",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )


def _mark_terminal(wi, WorkItemState, reason: str) -> None:
    """尽最大努力把 wi 置为终态（失败仅告警，不覆盖原始异常）。"""
    if wi is None:
        return
    try:
        if not wi.is_terminal:
            try:
                wi.transition_to(WorkItemState.FAILED)
            except ValueError:
                wi.state = WorkItemState.FAILED
        wi.error_message = wi.error_message or reason
    except Exception as e:  # noqa: BLE001
        logger.debug("mark terminal skipped: %s", e)


def make_handler(runner: SopCapabilityRunner, sop_id: str, name: str) -> Callable:
    """构造 BusinessFlowTool.handler（供工具契约完整性；M1 亦直接调 runner.run）。

    运行时上下文（message / session_context / actor）经 params 私有键注入，
    与现有 `_inject_runtime_params` 的约定一致；缺失时优雅降级。
    """

    async def _handler(params: dict, **kwargs) -> dict:
        p = params or {}
        request = str(p.get("request") or "")
        additional = str(p.get("additional_input") or "").strip()
        user_input = request if not additional else f"{request}\n\n[用户补充] {additional}"
        result = await runner.run(
            sop_id,
            user_input,
            name=name,
            message=p.get("_message"),
            db_message_id=str(p.get("_message_id") or ""),
            actor_snapshot=p.get("_actor_snapshot"),
            session_context=p.get("_session_context"),
        )
        return result.to_tool_dict()

    return _handler


# ══════════════════════════════════════════════════════════════════════════════
# 注册入口（C11：唯一注册通道）
# ══════════════════════════════════════════════════════════════════════════════

def register_capabilities(core) -> int:
    """将启用中的 SOP 注册为能力。返回注册数量。

    准入：sops/*.md 扫描结果 - SYSTEM_INTERNAL_SOPS（见 is_capability_sop）。
    """
    reg = getattr(core, "_capability_registry", None)
    if reg is None:
        logger.warning("register_capabilities: _capability_registry is None — skip")
        return 0

    skill_registry = getattr(core, "_skill_registry", None)
    if skill_registry is None:
        logger.warning("register_capabilities: _skill_registry is None — skip")
        return 0

    try:
        docs = skill_registry.list_skills()
    except Exception as e:  # noqa: BLE001
        logger.warning("register_capabilities: list_skills failed: %s", e)
        return 0

    from ..tools.business_flow_tools import BusinessFlowTool

    runner = SopCapabilityRunner(core=core, config=getattr(core, "config", None))
    registered, skipped = 0, 0

    for doc in docs:
        stem = getattr(doc, "sop_id", "") or ""
        if not is_capability_sop(stem):
            skipped += 1
            logger.debug("register_capabilities: skip non-capability SOP %s", stem)
            continue
        short = short_sop_id(stem)
        display = getattr(doc, "display_name", "") or short
        hint = getattr(doc, "instructions", "") or ""
        desc = f"{display}（业务流程 {short}）"
        if hint:
            desc = f"{desc}；{hint}"

        tool = BusinessFlowTool(
            name=short,
            description=desc,
            parameters=_SOP_CAPABILITY_SCHEMA,
            handler=make_handler(runner, stem, short),
            category="business",
            permission_flag="all",
            # 标准流程：既非自由读，也非"覆盖/删除"高危；可见性由 sop_allow 治理
            write_mode="transition",
        )
        reg.register(SopCapability(
            name=short, sop_id=stem, display_name=display, description=desc, tool=tool,
        ))
        registered += 1

    logger.info("register_capabilities: %d capabilities registered, %d skipped (system/internal)",
                registered, skipped)
    return registered


def build_runner(core=None) -> SopCapabilityRunner:
    """构造执行器（供 M1 注入）。"""
    return SopCapabilityRunner(core=core, config=getattr(core, "config", None))
