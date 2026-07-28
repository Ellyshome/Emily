"""SessionFactory —— Session 创建 + 全量知识灌注（重构后）。

委托 SessionContext.create() 完成全量数据灌注。
SessionFactory 本身仅负责组装依赖并创建 SessionAgent。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...session.session_agent import SessionAgent
from ...session.session_context import SessionContext

if TYPE_CHECKING:
    from ...adapters.standard.message import StandardMessage
    from ...workitem.pipeline.bus import PipelineBUS

logger = logging.getLogger("emily.session_factory")


class SessionFactory:
    """Session 工厂 —— 创建 + 全量知识灌注。"""

    def __init__(self, bus: "PipelineBUS", core=None):
        """
        Args:
            bus: 全局公共 Pipeline BUS（所有 Session 共享）。
            core: EmilyCore 实例。
        """
        self._bus = bus
        self._core = core

    def create(self, message: "StandardMessage", user_id: str = "") -> SessionAgent:
        """创建一个新的 SessionAgent（含全量知识灌注 + 意图识别依赖）。

        Args:
            message: 触发创建的入站消息。
            user_id: 已绑定的用户 UUID（Adapter 层完成绑定后传入）。

        Returns:
            SessionAgent: 已就绪（ACTIVE）的会话主脑。
        """
        conv_id = message.conversation_id
        context = self._build_context(message, user_id)

        # 从 EmilyCore 获取依赖
        llm = None
        skill_registry = None
        journal = None
        archive_writer = None
        if self._core is not None:
            llm = getattr(self._core, "_llm_client", None)
            skill_registry = getattr(self._core, "_skill_registry", None)
            journal = getattr(self._core, "_event_journal", None)
            archive_writer = getattr(self._core, "_session_archive_writer", None)

        agent = SessionAgent(
            conversation_id=conv_id,
            context=context,
            bus=self._bus,
            llm_client=llm,
            skill_registry=skill_registry,
            journal=journal,
            archive_writer=archive_writer,
        )
        # 将 EmilyCore 注入 scheduler（graph 引擎通过 _core 获取 _workitem_graph）
        agent.scheduler._core = self._core
        logger.info(
            "SessionFactory created session: conv=%s user=%s llm=%s skill_registry=%s journal=%s archive_writer=%s",
            conv_id, user_id or "?",
            "yes" if llm else "no",
            "yes" if skill_registry else "no",
            "yes" if journal else "no",
            "yes" if archive_writer else "no",
        )
        return agent

    def _build_context(self, message: "StandardMessage", user_id: str) -> SessionContext:
        """委托 SessionContext.create() 完成全量数据灌注。"""
        return SessionContext.create(
            user_id=user_id,
            conversation_id=message.conversation_id,
            sender_name=message.sender_name or "",
            core=self._core,
        )
