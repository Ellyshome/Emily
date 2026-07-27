"""GroupMemoryService —— 群级记忆沉淀与注入服务。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.database.models import GroupMemory

logger = logging.getLogger("emily.service.group_memory")


def _format_message_history(message_history: list[dict]) -> str:
    """格式化 message_history 为 LLM 可读的对话文本。"""
    if not message_history:
        return ""
    lines = []
    for turn in message_history:
        role = turn.get("role", "")
        content = (turn.get("content", "") or "")[:300]
        prefix = "用户" if role == "user" else "Emily"
        lines.append(f"[{prefix}]: {content}")
    return "\n".join(lines)


class GroupMemoryService:
    """群级长期记忆服务。

    负责：
    - Session 归档时沉淀关键事实到群级记忆
    - 新 Session 拉起时注入群级记忆摘要到 prompt
    """

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def consolidate_on_archive(
        self,
        group_id: str,
        group_name: str,
        session_id: str,
        speaker_user_id: str,
        message_history: list[dict],
        existing_memory=None,
    ) -> None:
        """Session 归档时，整合本次对话到群级记忆。

        Args:
            group_id: 群 ID
            group_name: 群名
            session_id: Session ID
            speaker_user_id: 发言者用户 ID
            message_history: 当前 Session 的对话历史
            existing_memory: 已有的 GroupMemory 记录（可选，减少一次查询）
        """
        if not message_history:
            return
        if not self._llm:
            logger.debug("group memory consolidate skipped: no LLM client")
            return

        from ..repositories.group_memory_repo import GroupMemoryRepository

        # 已有记忆（未传入则查询）
        existing_summary = ""
        existing_facts: list = []
        if existing_memory is not None:
            existing_summary = existing_memory.summary or ""
            if existing_memory.key_facts:
                try:
                    existing_facts = json.loads(existing_memory.key_facts)
                except (json.JSONDecodeError, TypeError):
                    existing_facts = []

        current_text = _format_message_history(message_history)

        try:
            prompt = self._build_consolidate_prompt(
                existing_summary, existing_facts, current_text, group_name
            )
            result = await self._llm.chat_messages(prompt, json_mode=True)
            data = result.get("data", {}) or {}
            new_summary = data.get("summary", "")
            new_facts = data.get("key_facts", [])
            if new_summary and isinstance(new_facts, list):
                GroupMemoryRepository.upsert(
                    group_id=group_id,
                    group_name=group_name,
                    summary=new_summary,
                    key_facts=new_facts,
                    session_id=session_id,
                    speaker_user_id=speaker_user_id,
                )
                logger.info(
                    "group memory consolidated: group=%s facts=%d",
                    group_id, len(new_facts),
                )
        except Exception as e:
            logger.warning("group memory consolidate failed: %s", e)

    def build_injection(self, group_id: str) -> str:
        """新 Session 拉起时，生成群级记忆注入文本。

        Returns:
            str: 群级记忆段落文本，无记忆时返回空字符串
        """
        from ..repositories.group_memory_repo import GroupMemoryRepository

        mem = GroupMemoryRepository.get_by_group(group_id)
        if not mem or not mem.summary:
            return ""

        facts: list = []
        if mem.key_facts:
            try:
                facts = json.loads(mem.key_facts)
            except (json.JSONDecodeError, TypeError):
                pass

        lines = [f"## 群级长期记忆（{mem.group_name or mem.group_id}）"]
        if mem.summary:
            lines.append(f"摘要: {mem.summary}")
        if facts:
            lines.append("关键事实:")
            for f in facts[:20]:
                lines.append(f"  - {f}")
        return "\n".join(lines)

    def _build_consolidate_prompt(
        self,
        existing_summary: str,
        existing_facts: list,
        current_conversation: str,
        group_name: str,
    ) -> list[dict]:
        """构造记忆沉淀 prompt。"""
        return [
            {"role": "system", "content": (
                "你是一个群聊记忆管理助手。根据已有记忆和最新对话，整合更新群级长期记忆。\n"
                "要求：\n"
                "1. summary：群级记忆摘要，保留关键事实（人物、事件、决策、任务、时间），不超过 500 字\n"
                "2. key_facts：关键事实列表（每条一句），最多保留 20 条，合并重复\n"
                "3. 已有记忆中的事实如果仍然相关则保留，否则移除\n"
                "请输出 JSON: {\"summary\": \"...\", \"key_facts\": [\"事实1\", \"事实2\", ...]}"
            )},
            {"role": "user", "content": (
                f"群名: {group_name}\n\n"
                f"已有记忆摘要: {existing_summary or '（无）'}\n"
                f"已有关键事实: {json.dumps(existing_facts, ensure_ascii=False) if existing_facts else '（无）'}\n\n"
                f"最新对话:\n{current_conversation[:3000]}"
            )},
        ]
