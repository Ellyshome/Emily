"""ReplyMessage —— 统一回复对象。

Emily Core 处理完毕后返回此对象，由出站适配器发送到 IM。
"""

from dataclasses import dataclass, field


@dataclass
class ReplyMessage:
    """跨平台统一回复对象。

    Attributes:
        conversation_id: 目标会话 ID
        content: 回复文本
        reply_to_message_id: 引用的消息 ID（可选）
        references: 引用来源列表（M5 知识库查询时填充）
        metadata: 扩展元数据
        file_paths: 附加文件列表（M13）— [{"path": "/abs/path", "name": "图纸.dwg"}, ...]
    """

    conversation_id: str
    content: str
    reply_to_message_id: str | None = None
    references: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    file_paths: list[dict] = field(default_factory=list)
