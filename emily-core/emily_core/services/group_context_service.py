"""GroupContextService —— @emily 拉起时从 DB 回溯群聊上下文。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.database.models import Message

logger = logging.getLogger("emily.service.group_context")

INITIAL_BATCH = 10       # 首批回溯条数
MAX_BATCHES = 5          # 最多回溯批次（10 * 5 = 50 条上限）


class GroupContextService:
    """群聊上下文回溯服务。

    在 @emily 拉起 Session 时从 DB 检索最近群聊记录，
    LLM 判断充分性，不足时继续回溯。
    """

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def build_group_context(
        self,
        group_id: str,
        current_message_id: str,
        user_question: str,
    ) -> str:
        """回溯群聊记录并格式化为 prompt 文本。

        Args:
            group_id: 群 ID
            current_message_id: 当前消息的 DB ID（回溯锚点）
            user_question: 当前用户问题（用于 LLM 充分性判断）

        Returns:
            str: 拼好的群聊上下文文本；无记录时返回空字符串
        """
        if not group_id:
            logger.info("GroupContextService: skip (no group_id)")
            return ""

        from ..repositories.message_repo import MessageRepository

        logger.info("GroupContextService: building context for group=%s anchor=%s",
                     group_id, current_message_id[:20])

        all_messages: list[Message] = []
        anchor_id = current_message_id

        for batch_idx in range(MAX_BATCHES):
            batch = MessageRepository.list_recent_by_group(
                group_id=group_id,
                limit=INITIAL_BATCH,
                before_id=anchor_id,
            )
            logger.info("GroupContextService: batch[%d] fetched %d messages (anchor=%s)",
                         batch_idx, len(batch), anchor_id[:20])
            if not batch:
                break
            # batch 是倒序；prepend 到 all_messages 前保持正序
            all_messages = list(reversed(batch)) + all_messages
            anchor_id = batch[-1].id

            # LLM 判断充分性
            try:
                if await self._is_sufficient(all_messages, user_question):
                    logger.info("GroupContextService: sufficient after batch %d (total %d msgs)",
                                 batch_idx, len(all_messages))
                    break
            except Exception as e:
                logger.info("GroupContextService: sufficiency check failed: %s", e)
                break

        if not all_messages:
            logger.info("GroupContextService: no messages found for group=%s", group_id)
            return ""

        result = self._format_for_prompt(all_messages)
        logger.info("GroupContextService: built %d chars from %d messages",
                     len(result), len(all_messages))
        return result

    async def _is_sufficient(self, messages: list[Message], question: str) -> bool:
        """LLM 判断已有群聊记录是否足以回答用户问题（fail-open）。"""
        if not self._llm:
            return True
        if len(messages) >= INITIAL_BATCH * MAX_BATCHES:
            return True

        try:
            prompt = self._build_sufficiency_prompt(messages, question)
            result = await self._llm.chat_messages(prompt, json_mode=True)
            data = result.get("data", {}) or {}
            return bool(data.get("sufficient", True))
        except Exception:
            return True  # fail-open

    def _build_sufficiency_prompt(self, messages: list[Message], question: str) -> list[dict]:
        """构造充分性判断 prompt。"""
        summary_lines = []
        for msg in messages[-20:]:  # 只看最近 20 条
            prefix = "Emy" if msg.direction == "agent_to_user" else (msg.sender_name or "?")
            summary_lines.append(f"[{msg.created_at[:16]}] {prefix}: {(msg.content or '')[:80]}")
        summary = "\n".join(summary_lines)
        return [
            {"role": "system", "content": (
                "你是一个上下文充分性判断器。根据提供的群聊记录摘要和用户问题，"
                "判断现有上下文是否足以回答用户问题。"
                "请输出 JSON: {\"sufficient\": true/false, \"reason\": \"...\"}"
            )},
            {"role": "user", "content": (
                f"用户问题: {question}\n\n群聊记录摘要:\n{summary}"
            )},
        ]

    @staticmethod
    def _format_for_prompt(messages: list[Message]) -> str:
        """格式化群聊记录为 prompt 文本（含附件元信息 + handle）。"""
        lines = ["## 群聊历史上下文（最近记录）"]
        for msg in messages:
            sender = msg.sender_name or "未知"
            direction = "Emily" if msg.direction == "agent_to_user" else sender
            content = (msg.content or "")[:500]
            created = (msg.created_at or "")[:19]
            line = f"[{created}] {direction}: {content}"
            # 附件元信息 + handle
            if msg.attachments:
                try:
                    import json
                    atts = json.loads(msg.attachments) if isinstance(msg.attachments, str) else msg.attachments
                    for att in (atts or [])[:3]:
                        file_name = att.get("file_name", "") or "未命名"
                        att_type = att.get("type", "?")
                        file_size = att.get("file_size", 0)
                        line += f"\n  📎 附件: {file_name} (type={att_type}, size={file_size})"
                        line += f"     handle: msg://{msg.id}/att/{att.get('id', '?')}"
                except Exception:
                    pass
            lines.append(line)
        return "\n".join(lines)
