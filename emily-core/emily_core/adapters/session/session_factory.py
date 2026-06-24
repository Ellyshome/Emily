"""SessionFactory —— Session 创建 + 最小化知识灌注（蓝图 §3.4 / §4.3.1）。

未命中 Session 时，由工厂创建新 Session：
  ├── 灌入 Session-Agent（最小化知识灌注）
  ├── 创建 Session 状态机
  └── 绑定公共 Pipeline BUS

最小化灌注原则（蓝图 §4.3.1）：只加载最近对话 + 用户摘要 + SOP/工具目录一级摘要，
其余懒加载。本期为骨架：组装 SessionContext 的最小字段，真实的摘要生成器
（UserMemoryService 历史摘要、SOPIntentRegistry.summary()、SchemaSummaryBuilder）
接线属 Phase B/C —— 这些服务已随包迁移备用。
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
        """创建一个新的 SessionAgent（含最小化知识灌注）。

        Args:
            message: 触发创建的入站消息。
            user_id: 已绑定的用户 UUID（Adapter 层完成绑定后传入）。

        Returns:
            SessionAgent: 已就绪（ACTIVE）的会话主脑。
        """
        conv_id = message.conversation_id
        context = self._build_context(message, user_id)
        agent = SessionAgent(conversation_id=conv_id, context=context, bus=self._bus)
        logger.info(
            "SessionFactory created session: conv=%s user=%s",
            conv_id, user_id or "?",
        )
        return agent

    def _build_context(self, message: "StandardMessage", user_id: str) -> SessionContext:
        """组装最小化灌注上下文（蓝图 §4.3.1）。

        本期仅填充可立即获得的字段；摘要类字段留待 Phase B/C 灌注。
        """
        return SessionContext(
            conversation_id=message.conversation_id,
            user_id=user_id,
            user_name=message.sender_name or "",
            current_datetime=datetime.now(timezone.utc).isoformat(),
            # 以下为占位，Phase B/C 由真实服务填充：
            recent_turns=[],
            user_preferences="",
            history_summary="",
            sop_catalog_summary="",
            tool_catalog_summary="",
            schema_summary="",
            perm_list=[],
        )
