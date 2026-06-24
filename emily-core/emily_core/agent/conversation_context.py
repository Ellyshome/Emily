"""ConversationContext —— 短期对话记忆。

按 conversation_id 索引，维护最近 N 轮对话的滑动窗口。
支持过期自动清理，避免内存无限增长。
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class ConversationTurn:
    """对话中的一轮（一条消息或一个工具调用结果）。

    Attributes:
        role: "user" | "assistant" | "tool"
        content: 消息文本或工具结果 JSON 字符串
        tool_name: 工具名（role="tool" 时设置）
        tool_call_id: OpenAI tool_call_id（role="tool" 时设置）
        reasoning_content: DeepSeek thinking mode 思考链内容
            （role="assistant" + tool_calls 时需回传给 API，否则 400）
    """

    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None


class ConversationContext:
    """短期滑动窗口对话记忆 + 长期记忆上下文加载。

    按 conversation_id 索引，每条 conversation 保留最近 max_turns 轮。
    超过 ttl_seconds 未活动的上下文自动标记为过期。

    M8c: 支持加载用户长期记忆作为 system prompt 上下文。

    Args:
        conversation_id: IM 会话 ID
        user_id: 系统用户 ID
        max_turns: 最大保留轮数（默认 10）
        ttl_seconds: 过期时间（秒，默认 600 = 10 分钟）
        user_memory_context: M8c 用户长期记忆上下文文本（可选）
    """

    def __init__(
        self,
        conversation_id: str,
        user_id: str = "",
        max_turns: int = 10,
        ttl_seconds: int = 600,
        user_memory_context: str = "",
    ):
        self.conversation_id = conversation_id
        self.user_id = user_id
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._turns: deque[ConversationTurn] = deque()
        self.last_active: float = time.time()
        self.user_memory_context: str = user_memory_context  # M8c
        self._lock = threading.Lock()

    def add_turn(
        self,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        """添加一轮对话记录。

        Args:
            role: "user" / "assistant" / "tool"
            content: 消息文本或 JSON 结果字符串
            tool_name: 工具名（仅 role="tool" 时使用）
            tool_call_id: OpenAI tool_call_id（仅 role="tool" 时使用）
            reasoning_content: DeepSeek 思考链（仅 role="assistant" + tool_calls 时需要）
        """
        with self._lock:
            self._turns.append(
                ConversationTurn(
                    role=role,
                    content=content,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    reasoning_content=reasoning_content,
                )
            )
            # 滑动窗口：超过 max_turns 时丢弃最早的记录
            while len(self._turns) > self.max_turns * 2:
                # *2 因为一轮对话包含 user + assistant 两条（可能还有 tool）
                self._turns.popleft()

            self.last_active = time.time()

    def get_recent_turns(self, n: int | None = None) -> list[ConversationTurn]:
        """获取最近 n 轮对话（默认全部）。

        Args:
            n: 返回的轮数，None 表示全部

        Returns:
            ConversationTurn 列表
        """
        turns = list(self._turns)
        if n is not None:
            turns = turns[-n:]
        return turns

    def get_messages_for_llm(self) -> list[dict]:
        """将对话历史转换为 OpenAI messages 格式。

        只返回 user 和 assistant 的文本消息。
        tool/tool_call 消息不在跨轮次上下文中保留——
        它们由 MasterAgent 的 ReAct 循环在当轮临时管理。

        Returns:
            [{"role": "user/assistant", "content": "..."}, ...]
        """
        messages: list[dict] = []
        for turn in self._turns:
            if turn.role in ("user", "assistant"):
                messages.append({"role": turn.role, "content": turn.content})
        return messages

    def clear(self) -> None:
        """清空对话历史。"""
        self._turns.clear()

    def is_expired(self) -> bool:
        """检查上下文是否已过期。

        Returns:
            True 如果距离最后活跃时间超过 ttl_seconds
        """
        return (time.time() - self.last_active) > self.ttl_seconds

    @property
    def turn_count(self) -> int:
        """当前对话轮数。"""
        return len(self._turns)
