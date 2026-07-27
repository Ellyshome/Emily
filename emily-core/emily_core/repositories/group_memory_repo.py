"""GroupMemoryRepository —— 群级长期记忆 CRUD。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import GroupMemory

logger = logging.getLogger("emily.repo.group_memory")


class GroupMemoryRepository:
    """群级长期记忆读写操作。"""

    @staticmethod
    def get_by_group(group_id: str) -> Optional[GroupMemory]:
        """按 group_id 查询群级记忆。"""
        with get_session() as session:
            return session.query(GroupMemory).filter(
                GroupMemory.group_id == group_id
            ).first()

    @staticmethod
    def upsert(
        group_id: str,
        group_name: str,
        summary: str,
        key_facts: list,
        session_id: str,
        speaker_user_id: str,
    ) -> GroupMemory:
        """插入或更新群级记忆。

        Args:
            group_id: 群 ID
            group_name: 群名
            summary: LLM 整合的群级记忆摘要
            key_facts: 关键事实列表（JSON 可序列化）
            session_id: 最后沉淀的 Session ID
            speaker_user_id: 最后发言者用户 ID

        Returns:
            GroupMemory: 更新后的记忆记录
        """
        with get_session() as session:
            mem = session.query(GroupMemory).filter(
                GroupMemory.group_id == group_id
            ).first()
            if mem is None:
                mem = GroupMemory(group_id=group_id, group_name=group_name)
                session.add(mem)
            mem.summary = summary
            mem.key_facts = json.dumps(key_facts, ensure_ascii=False)
            mem.last_session_id = session_id
            mem.last_speaker_user_id = speaker_user_id or None
            mem.fact_count = len(key_facts)
            session.commit()
            return mem
