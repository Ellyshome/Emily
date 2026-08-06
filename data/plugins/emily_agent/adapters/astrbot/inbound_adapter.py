""""AstrBot Inbound Adapter —— 将 AstrBot 事件转换为 StandardMessage。"""

import asyncio
import logging
from typing import TYPE_CHECKING

from ..standard.message import StandardMessage

if TYPE_CHECKING:
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

logger = logging.getLogger("emily.adapter.inbound")


class AstrBotInboundAdapter:
    """AstrBot 入站消息适配器。

    负责将 AstrBot 的 AstrMessageEvent 转换为 Emily 的 StandardMessage。
    """

    def __init__(self):
        # 群名缓存：避免每条群消息都调 get_group() API
        self._group_name_cache: dict[str, str] = {}

    async def to_standard_message(self, event: "AstrMessageEvent") -> StandardMessage:
        """转换 AstrBot 消息事件为标准消息。

        Args:
            event: AstrBot 消息事件对象。

        Returns:
            StandardMessage: 平台无关的统一消息对象。
        """
        # 消息类型：与 MessageType 枚举直接比较，避免大小写问题
        from astrbot.core.platform.message_type import MessageType

        msg_type = event.get_message_type()
        if msg_type == MessageType.GROUP_MESSAGE:
            conversation_type = "group"
        elif msg_type == MessageType.PRIVATE_MESSAGE:
            conversation_type = "private"
        else:
            conversation_type = "unknown"
            logger.warning("Unknown message type: %s, treating as unknown", msg_type)

        # 发送者
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()

        # 群信息
        group_id = None
        group_name = ""
        if conversation_type == "group":
            gid = event.get_group_id()
            if gid:
                group_id = gid
                # 群名提取：优先缓存，miss 时调 get_group() API（仅首次）
                if gid in self._group_name_cache:
                    group_name = self._group_name_cache[gid]
                else:
                    try:
                        group_obj = await event.get_group()
                        if group_obj is not None:
                            gn = getattr(group_obj, "group_name", "") or ""
                            self._group_name_cache[gid] = gn
                            group_name = gn
                    except Exception as e:
                        logger.debug("get_group failed (non-blocking): %s", e)

        # 消息文本
        content = event.message_str or ""

        # ── M11: 多模态内容提取 ──
        msg_type = 1       # 1=文本(默认)
        attachments: list[dict] = []
        receiver_id = ""

        self_id = event.get_self_id()
        # 群聊时设 receiver_id
        if conversation_type == "group":
            receiver_id = self_id or ""

        messages = event.get_messages()
        for comp in messages:
            # At 组件
            if hasattr(comp, "qq"):
                uid = str(comp.qq)
                mentioned_ids.append(uid)
                if uid == self_id:
                    is_at_bot = True
            # AtAll 组件
            if hasattr(comp, "type") and getattr(comp, "type", None) == "at_all":
                mentioned_ids.append("all")

            # ── M11: 多媒体组件类型检测 ──
            comp_type = getattr(comp, "type", None)
            comp_data = getattr(comp, "data", None) or {}

            if comp_type == "image" or (comp_data and comp_data.get("url")):
                if msg_type == 1:  # 首次非文本→设为图片
                    msg_type = 2
                url = comp_data.get("url") or getattr(comp, "url", "")
                attachments.append({
                    "type": 2,  # image
                    "url": url,
                    "file_name": comp_data.get("file", "") or comp_data.get("summary", ""),
                    "file_size": comp_data.get("file_size", 0),
                    "summary": comp_data.get("summary", ""),
                })
            elif comp_type == "record" or (comp_data and "time" in comp_data):
                if msg_type == 1:
                    msg_type = 4  # voice
                url = comp_data.get("url") or getattr(comp, "url", "")
                attachments.append({
                    "type": 4,
                    "url": url,
                    "file_name": comp_data.get("file", ""),
                    "file_size": comp_data.get("file_size", 0),
                })
            elif comp_type == "video" or (comp_data and comp_data.get("thumb")):
                msg_type = 5
                url = comp_data.get("url") or getattr(comp, "url", "")
                attachments.append({
                    "type": 5,
                    "url": url,
                    "file_name": comp_data.get("file", ""),
                    "file_size": comp_data.get("file_size", 0),
                    "thumb": comp_data.get("thumb", ""),
                })
            elif comp_type == "file" or (comp_data and comp_data.get("name")):
                if msg_type == 1:
                    msg_type = 3
                url = comp_data.get("url") or getattr(comp, "url", "")
                attachments.append({
                    "type": 3,
                    "url": url,
                    "file_name": comp_data.get("name", ""),
                    "file_size": comp_data.get("size", 0),
                })

        # 消息 ID
        message_id = ""
        msg_obj = event.message_obj
        if msg_obj:
            msg_id_attr = getattr(msg_obj, "message_id", None)
            if msg_id_attr:
                message_id = str(msg_id_attr)

        # 引用回复
        reply_to = None
        if msg_obj:
            raw = getattr(msg_obj, "raw_message", None)
            if isinstance(raw, dict):
                reply_to = str(raw.get("reply_message_id", "")) or None

        standard = StandardMessage(
            message_id=message_id,
            platform=event.get_platform_name() or "napcat",
            conversation_type=conversation_type,
            conversation_id=group_id or sender_id,
            sender_id=sender_id,
            sender_name=sender_name,
            group_id=group_id,
            group_name=group_name or None,
            content=content,
            is_at_bot=is_at_bot,
            mentioned_user_ids=mentioned_ids,
            reply_to_message_id=reply_to,
            msg_type=msg_type,
            attachments=attachments,
        )

        logger.debug(
            "inbound: type=%s, sender=%s, at_bot=%s, content_preview=%.50s",
            conversation_type, sender_name, is_at_bot, content,
        )

        return standard

    # ── 企业微信适配 ────────────────────────────────────────────────

    @staticmethod
    def convert_wecom(event: "AstrMessageEvent") -> "StandardMessage":
        """企业微信事件 → StandardMessage（静态方法，企微无群聊需缓存）。

        Args:
            event: AstrBot 的 wecom 平台事件

        Returns:
            填充完整的 StandardMessage
        """
        import uuid

        message_obj = event.message_obj

        # 提取附件信息
        attachments: list[dict] = []
        for comp in message_obj.message:
            comp_type = getattr(comp, "type", None)
            if comp_type == "Image":
                attachments.append({
                    "type": 2,
                    "url": getattr(comp, "url", ""),
                    "file_name": getattr(comp, "file", ""),
                })
            elif comp_type == "File":
                attachments.append({
                    "type": 3,
                    "url": getattr(comp, "url", ""),
                    "file_name": getattr(comp, "file", ""),
                })

        return StandardMessage(
            message_id=message_obj.message_id or "",
            platform="wecom",
            conversation_type="private",  # 企微客服/应用均为私聊
            conversation_id=event.unified_msg_origin,
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            content=event.message_str,
            msg_type=1 if not attachments else attachments[0]["type"],
            attachments=attachments,
            event_id=str(uuid.uuid4()),
        )
