"""StandardMessage —— 统一 IM 消息对象。

所有入站消息经适配器转换后统一为此对象，Emily Core 只依赖此对象，
不依赖 AstrBot 原始事件。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StandardMessage:
    """跨平台统一消息对象。

    Attributes:
        message_id: 消息唯一标识
        platform: 平台标识，如 "napcat" / "qq"
        conversation_type: "private" / "group"
        conversation_id: 会话 ID（群号或私聊用户 ID）
        sender_id: 发送者 ID
        sender_name: 发送者昵称
        group_id: 群号（群聊时有值）
        group_name: 群名
        content: 消息纯文本（已去除 @机器人 部分）
        is_at_bot: 是否 @了机器人
        mentioned_user_ids: 被 @的用户 ID 列表
        reply_to_message_id: 引用的消息 ID
        timestamp: 消息时间戳
        raw_event: 原始事件透传（调试用）
    """

    message_id: str = ""
    platform: str = ""
    conversation_type: str = ""  # "private" / "group"
    conversation_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    group_id: str | None = None
    group_name: str | None = None
    content: str = ""
    is_at_bot: bool = False
    mentioned_user_ids: list[str] = field(default_factory=list)
    reply_to_message_id: str | None = None
    timestamp: datetime | None = None
    raw_event: dict | None = None

    # ── M11: 多媒体内容 ──
    msg_type: int = 1              # 1=text 2=image 3=file 4=voice 5=video 6=card
    attachments: list[dict] = field(default_factory=list)
        # [{"type": 2, "url": "...", "file_name": "...", "file_size": 0}, ...]
