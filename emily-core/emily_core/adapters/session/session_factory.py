"""SessionFactory —— Session 创建 + 最小化知识灌注（蓝图 §3.4 / §4.3.1）。

未命中 Session 时，由工厂创建新 Session：
  ├── 灌入 Session-Agent（最小化知识灌注）
  ├── 创建 Session 状态机
  └── 绑定公共 Pipeline BUS

Phase B 升级（蓝图 §12.2）：
  · 传递 LLM 客户端 + SOPIntentRegistry 给 SessionAgent（意图识别）
  · 填充上下文摘要字段（SOP 目录、工具目录）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ...session.session_agent import SessionAgent
from ...session.session_context import SessionContext

if TYPE_CHECKING:
    from ...adapters.standard.message import StandardMessage
    from ...workitem.pipeline.bus import PipelineBUS

logger = logging.getLogger("emily.session_factory")


class SessionFactory:
    """Session 工厂 —— 创建 + 最小化知识灌注。"""

    def __init__(self, bus: "PipelineBUS", core=None):
        """
        Args:
            bus: 全局公共 Pipeline BUS（所有 Session 共享）。
            core: EmilyCore 实例（懒加载用户记忆/SOP 目录等服务），可为 None。
        """
        self._bus = bus
        self._core = core

    def create(self, message: "StandardMessage", user_id: str = "") -> SessionAgent:
        """创建一个新的 SessionAgent（含最小化知识灌注 + Phase B 意图识别依赖）。

        Args:
            message: 触发创建的入站消息。
            user_id: 已绑定的用户 UUID（Adapter 层完成绑定后传入）。

        Returns:
            SessionAgent: 已就绪（ACTIVE）的会话主脑。
        """
        conv_id = message.conversation_id
        context = self._build_context(message, user_id)

        # Phase B: 从 EmilyCore 获取意图识别依赖
        llm = None
        sop_registry = None
        if self._core is not None:
            llm = getattr(self._core, "_llm_client", None)
            sop_registry = getattr(self._core, "_sop_intent_registry", None)

        agent = SessionAgent(
            conversation_id=conv_id,
            context=context,
            bus=self._bus,
            # Phase B: 意图识别依赖
            llm_client=llm,
            sop_intent_registry=sop_registry,
        )
        logger.info(
            "SessionFactory created session: conv=%s user=%s llm=%s sop_registry=%s",
            conv_id, user_id or "?",
            "yes" if llm else "no",
            "yes" if sop_registry else "no",
        )
        return agent

    def _build_context(self, message: "StandardMessage", user_id: str) -> SessionContext:
        """组装最小化灌注上下文（蓝图 §4.3.1 + Phase B 摘要字段填充）。"""
        ctx = SessionContext(
            conversation_id=message.conversation_id,
            user_id=user_id,
            user_name=message.sender_name or "",
            current_datetime=datetime.now(timezone.utc).isoformat(),
        )

        core = self._core
        if core is None:
            return ctx

        # Phase B: 填充 SOP 目录摘要
        sop_registry = getattr(core, "_sop_intent_registry", None)
        if sop_registry is not None:
            try:
                sops = sop_registry.list_loaded_sops()
                if sops:
                    ctx.sop_catalog_summary = (
                        f"可用业务流程 ({len(sops)}): {', '.join(sops[:15])}"
                    )
            except Exception:
                pass

        # Phase B: 填充工具目录摘要
        tool_registry = getattr(core, "_tool_registry", None)
        if tool_registry is not None:
            try:
                tools = tool_registry.tool_names
                if tools:
                    ctx.tool_catalog_summary = (
                        f"可用工具 ({len(tools)}): {', '.join(tools[:20])}"
                    )
            except Exception:
                pass

        return ctx
