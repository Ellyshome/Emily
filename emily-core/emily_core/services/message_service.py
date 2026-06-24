"""消息持久化业务服务 —— Repository 的上层包装。

M2 职责：
- 记录接管消息（幂等，event_id 去重）
- 标记消息处理状态
- 回填 sender_user_id
"""

import logging
from typing import Optional

from ..repositories.message_repo import MessageRepository
from ..adapters.standard.message import StandardMessage
from ..adapters.standard.route_decision import RouteDecision
from ..infrastructure.database.models import Message

logger = logging.getLogger("emily.service.message")


class MessageService:
    """消息持久化业务服务。"""

    def __init__(self):
        self.repo = MessageRepository()

    def record_message(
        self,
        event_id: str,
        msg: StandardMessage,
        decision: RouteDecision,
    ) -> Message:
        """记录一条接管消息。

        幂等：如果 event_id 已存在，直接返回已有记录。

        Args:
            event_id: 消息指纹（main.py 去重逻辑生成）
            msg: 标准化消息
            decision: 接管决策

        Returns:
            Message: 已持久化的消息记录
        """
        # 幂等：已存在就直接返回
        existing = self.repo.get_by_event_id(event_id)
        if existing:
            logger.debug("Message already exists: %s", event_id)
            return existing

        return self.repo.create_from_standard(event_id, msg, decision)

    def mark_processed(self, message_id: str) -> None:
        """标记消息已处理完成。"""
        self.repo.mark_processed(message_id)

    def get_by_event_id(self, event_id: str) -> Optional[Message]:
        """按 event_id 查询消息。"""
        return self.repo.get_by_event_id(event_id)

    def bind_sender(self, message_id: str, user_id: str) -> None:
        """回填消息的 sender_user_id（用户绑定后调用）。"""
        self.repo.update_sender_user_id(message_id, user_id)

    # ── M3 新增：路由结果回填 ──

    def update_route_result(self, message_id: str, intent: str, project_id: Optional[str] = None) -> None:
        """路由完成后回填 intent 和 project_id。

        Args:
            message_id: 消息 UUID
            intent: Router 识别的意图
            project_id: 匹配到的项目 UUID（可能为空）
        """
        self.repo.update_intent(message_id, intent)
        if project_id:
            self.repo.update_project_id(message_id, project_id)
        logger.info("Route result saved: msg=%s, intent=%s, project=%s", message_id, intent, project_id)
