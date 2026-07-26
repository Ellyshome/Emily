"""GroupRegistryService —— 群列表注册服务。

接收插件同步的群列表，upsert 到 conversations 表，并支持查询。
"""

import logging

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import Conversation

logger = logging.getLogger("emily.services.group_registry")


class GroupRegistryService:
    """群列表注册服务 —— 接收插件同步的群列表，upsert 到 conversations 表。"""

    def upsert_groups(self, groups: list[dict]) -> int:
        """批量 upsert 群信息到 conversations 表。

        Args:
            groups: [{"group_id", "group_name", "member_count", "platform"}, ...]

        Returns:
            int: 处理的群数量
        """
        with get_session() as session:
            count = 0
            for g in groups:
                conv = session.query(Conversation).filter(
                    Conversation.im_platform == g["platform"],
                    Conversation.conversation_id == g["group_id"],
                ).first()
                if conv is None:
                    conv = Conversation(
                        im_platform=g["platform"],
                        conversation_type="group",
                        conversation_id=g["group_id"],
                        group_id=g["group_id"],
                        title=g.get("group_name", ""),
                        takeover_mode="monitor",
                    )
                    session.add(conv)
                else:
                    if g.get("group_name"):
                        conv.title = g["group_name"]
                count += 1
            session.commit()
            logger.info("upserted %d groups to conversations", count)
            return count

    def list_groups(self) -> list[dict]:
        """列出所有已知群（供启动通知用）。"""
        with get_session() as session:
            convs = session.query(Conversation).filter(
                Conversation.conversation_type == "group"
            ).all()
            return [{
                "group_id": c.group_id,
                "group_name": c.title or "(未命名)",
                "platform": c.im_platform,
                "last_active": c.updated_at,
            } for c in convs]
