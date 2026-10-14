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

        写入边界（《群信息保存与复用》 AC-US-01.6 / R1）：
          · `title` 由平台群名覆盖（平台改名即更新）；
          · `member_count` 落平台侧快照（名册不可得时的回退值）；
          · **`note` 属人工标注，本方法永不写入** —— 备注与平台群名分离留存。
        """
        with get_session() as session:
            count = 0
            for g in groups:
                member_count = int(g.get("member_count") or 0)
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
                        member_count=member_count,
                    )
                    session.add(conv)
                else:
                    if g.get("group_name"):
                        conv.title = g["group_name"]
                    if member_count:
                        conv.member_count = member_count
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
