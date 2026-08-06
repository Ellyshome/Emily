""""AstrBot Outbound Sender —— 将 ReplyMessage 发送到 IM。

M8b: 支持 send_progress() 前导消息（先发"处理中..."再发结果）。
     前导消息使用 event.send() 立即发送，不经过 result pipeline，
     避免被最终回复覆盖。
M13: 支持 send_files() 主动发送文件 + send() 携带附件回复。
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from ..standard.reply import ReplyMessage

if TYPE_CHECKING:
    from astrbot.core.platform.astr_message_event import AstrMessageEvent

logger = logging.getLogger("emily.adapter.outbound")


class AstrBotOutboundSender:
    """AstrBot 出站消息发送器。

    负责将 Emily 的 ReplyMessage 通过 AstrBot API 发送到 IM。
    M1 仅支持文本回复。
    M8b: 支持 send_progress() 前导消息。
    M13: 支持 send_files() 主动发送文件。

    支持两种发送模式（自动适配）：
      - QQ 模式：通过 event.set_result() / event.send()（NapCat 桥接）
      - 企微模式：通过 context.send_message()（AstrBot 4.x wecom 适配器）
        当 event 不可用时回退到 context 方式（在 main.py 初始化时注入）。
    """

    def __init__(self):
        self.context: Any = None  # AstrBot star.Context（企微发送用，由 main.py 注入）

    @staticmethod
    async def send(reply: ReplyMessage, event: "AstrMessageEvent" = None) -> None:
        """发送最终回复消息。

        发送模式：
          - 有 event → event.set_result()（QQ / 传统 AstrBot 路径）
          - 无 event → context.send_message()（企微路径）

        Args:
            reply: Emily 回复对象。
            event: 原始 AstrBot 消息事件（QQ 路径必需，企微路径可选）。
        """
        file_paths = getattr(reply, "file_paths", None) or []

        if event is not None:
            # QQ / 传统路径：通过 event set_result
            if file_paths:
                result = AstrBotOutboundSender._build_file_chain_result(
                    text=reply.content, file_paths=file_paths, event=event,
                )
            else:
                result = event.plain_result(reply.content)
            event.set_result(result)
            event.stop_event()
            logger.info(
                "outbound: content=%.50s, files=%d, conversation=%s",
                reply.content, len(file_paths), reply.conversation_id,
            )
        else:
            # 企微路径：无 event，通过 context 发送
            logger.info(
                "outbound (wecom): content=%.50s, files=%d, conv=%s",
                reply.content, len(file_paths), reply.conversation_id,
            )
            from astrbot.api.event import MessageChain
            text = reply.content
            if file_paths:
                names = [f.get("name", "unknown") for f in file_paths]
                suffix = f"\n📎 文件: {', '.join(names)}"
                text = text + suffix if text else suffix
            # 获取 context（实例方法需要 self，但这是静态方法 — 通过 _context 类属性回退）
            if text.strip():
                _ctx = getattr(AstrBotOutboundSender, '_context', None)
                if _ctx:
                    chain = MessageChain().message(text)
                    import asyncio as _asyncio
                    _asyncio.create_task(
                        _ctx.send_message(reply.conversation_id, chain)
                    )

    @staticmethod
    async def send_files(
        file_paths: list[dict],
        event: "AstrMessageEvent",
        caption: str = "",
    ) -> None:
        """主动发送文件（立即发送，不经过 set_result pipeline）。

        M13: 供 Agent 的 send_file 工具使用，使用 event.send() 直接发送，
        避免与最终 reply 的 set_result 冲突。

        Args:
            file_paths: [{"path": "/abs/path/to/file.dwg", "name": "图纸.dwg"}, ...]
            event: 原始 AstrBot 消息事件
            caption: 可选的文本说明
        """
        try:
            result = AstrBotOutboundSender._build_file_chain_result(
                text=caption, file_paths=file_paths, event=event,
            )
            await event.send(result)
            logger.info(
                "outbound send_files: %d file(s), caption=%.50s",
                len(file_paths), caption,
            )
        except Exception as e:
            logger.warning("M13 send_files failed: %s", e)

    @staticmethod
    def _build_file_chain_result(
        text: str,
        file_paths: list[dict],
        event: "AstrMessageEvent",
    ):
        """构建包含文本和文件的 MessageEventResult（chain_result）。

        Args:
            text: 文本内容
            file_paths: [{"path": ..., "name": ...}, ...]
            event: AstrBot 事件

        Returns:
            MessageEventResult
        """
        from astrbot.core.message.components import Plain, File, Image

        chain = []
        if text:
            chain.append(Plain(text))

        for fp in file_paths:
            path = fp.get("path", "")
            name = fp.get("name", "")
            file_type = fp.get("type", "file")  # "file" | "image"

            if not path:
                continue

            if file_type == "image":
                chain.append(Image(file=path))
            else:
                chain.append(File(name=name or path.rsplit("/", 1)[-1], file=path))

        return event.chain_result(chain)

    @staticmethod
    async def send_progress(text: str, event: "AstrMessageEvent") -> None:
        """发送前导消息（立即发送，不经过 result pipeline）。

        在深度操作（守护审计、多步 ReAct）之前调用，
        用于告知用户"已收到消息，正在处理中"。

        使用 event.send() 直接发送到 IM 平台，避免 set_result() 被
        后续 send() 覆盖的问题。

        Args:
            text: 前导消息文本（如 "收到，正在为你全面检查，请稍候..."）
            event: 原始 AstrBot 消息事件

        Note:
            send_progress() 不调用 stop_event()，允许后续 send() 继续发送最终结果。
        """
        try:
            # 使用 event.send() 立即发送，不经过 result pipeline
            msg = event.plain_result(text)
            await event.send(msg)
            logger.info(
                "outbound progress: text=%.50s",
                text,
            )
        except Exception as e:
            # send() 可能因为没有 await super().send() 在非 aiocqhttp 平台上失败
            # 作为 fallback，回退到 set_result 方式（已尽力）
            logger.warning("M8b progress direct send failed, falling back: %s", e)
            result = event.plain_result(text)
            event.set_result(result)
