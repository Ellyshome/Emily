"""POST /api/v1/message/send —— 入站消息端点（蓝图 §2.6）。

薄插件将 StandardMessage（JSON）转发至此。短路回复同步返回，
异步处理结果通过 SSE 出站事件流推送。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from emily_core.adapters.standard.message import StandardMessage
from ..server import get_core

logger = logging.getLogger("emily.api.message")

router = APIRouter()


class MessageIn(BaseModel):
    """入站消息请求体（镜像 StandardMessage，用于 FastAPI 自动反序列化）。"""

    message_id: str = ""
    platform: str = ""
    conversation_type: str = ""
    conversation_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    group_id: str | None = None
    group_name: str | None = None
    content: str = ""
    is_at_bot: bool = False
    mentioned_user_ids: list[str] = Field(default_factory=list)
    reply_to_message_id: str | None = None
    msg_type: int = 1
    attachments: list[dict] = Field(default_factory=list)
    event_id: str = ""

    def to_standard(self) -> StandardMessage:
        """转换为内核 StandardMessage。"""
        return StandardMessage(
            message_id=self.message_id,
            platform=self.platform,
            conversation_type=self.conversation_type,
            conversation_id=self.conversation_id,
            sender_id=self.sender_id,
            sender_name=self.sender_name,
            group_id=self.group_id,
            group_name=self.group_name,
            content=self.content,
            is_at_bot=self.is_at_bot,
            mentioned_user_ids=self.mentioned_user_ids,
            reply_to_message_id=self.reply_to_message_id,
            msg_type=self.msg_type,
            attachments=self.attachments,
        )


class ReplyOut(BaseModel):
    """同步回复响应体（镜像 ReplyMessage）。"""

    conversation_id: str
    content: str
    reply_to_message_id: str | None = None


@router.post("/message/send")
async def handle_message(msg_in: MessageIn):
    """接收薄插件转发的入站消息。

    短路回复（如问候/确认）在此同步返回 ReplyMessage JSON；
    异步处理（Agent 多轮推理后回复）返回 204，结果通过 SSE 推送。
    """
    core = get_core()
    reply = await core.handle_message(msg_in.to_standard(), event_id=msg_in.event_id)
    if reply is None:
        return Response(status_code=204)  # 不接管 / 异步处理中
    return ReplyOut(
        conversation_id=reply.conversation_id,
        content=reply.content,
        reply_to_message_id=reply.reply_to_message_id,
    )
