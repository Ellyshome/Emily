"""SessionAgent —— 会话调度主脑（蓝图 §4.3）。

以 Session-Agent 为核心，负责：
  · 用自然语言与用户交互
  · 对入站消息做意图识别 + WorkItem 拆分（蓝图 §4.3.2）
  · 管理多 WorkItem 并发、优先级调度、待确认队列
  · 出站消息审核
  · Session 注销归档

本期实现：
- 意图识别用 Mock 大脑（短路指令 / WorkItem 拆分判定为占位规则 + MockRouter）。
- WorkItem 经 SessionScheduler 分配到全局公共 Pipeline BUS 执行。
- 真实的 SessionAgent（由 MasterAgent 升级，多 WorkItem 编排 + LLM 意图识别）
  接线属蓝图 §12 Phase B/C；真实 agent/master_agent.py 已随包迁移备用。

入站处理决策树（蓝图 §4.3.2）：
  短路指令？
    ├── 闲聊/问候/简单确认 → 直接组织自然语言回复（不创建 WorkItem）
    └── 兜底（无法理解）→ 引导性回复
  非短路指令？
    ├── 单一任务 → 创建 1 个 WorkItem
    └── 复合任务 → 拆分为 N 个 WorkItem
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .session_state import SessionState
from .session_context import SessionContext
from .focus_lock import FocusLock
from .confirm_queue import ConfirmQueue
from ..workitem import WorkItem, SessionScheduler
from ..adapters.standard.reply import ReplyMessage

if TYPE_CHECKING:
    from ..adapters.standard.message import StandardMessage
    from ..workitem.pipeline.bus import PipelineBUS

logger = logging.getLogger("emily.session_agent")

# 闲聊短路（与旧 message_app 对齐，节省 LLM 调用）
_SIMPLE_GREETINGS = {
    "你好", "早", "早上好", "下午好", "晚上好", "早安", "午安",
    "hi", "hello", "hey", "nihao", "在吗", "在不", "在不在",
}
_SIMPLE_THANKS = {"谢谢", "感谢", "多谢", "thanks", "thank you", "thx", "3q"}
_SIMPLE_FAREWELLS = {"再见", "拜拜", "bye", "goodbye", "晚安", "回头见", "下次见"}
_SIMPLE_SELF_INTRO = {"你是谁", "你叫什么", "你是什么", "who are you", "介绍自己", "介绍一下自己"}


class SessionAgent:
    """会话调度主脑 —— 每个 Session 一个实例。"""

    def __init__(
        self,
        conversation_id: str,
        context: SessionContext,
        bus: "PipelineBUS",
    ):
        self.conversation_id = conversation_id
        self.context = context
        self.state = SessionState.CREATED
        self.focus = FocusLock()
        self.confirm_queue = ConfirmQueue()
        self.scheduler = SessionScheduler(conversation_id, bus)
        # 灌注完成 → ACTIVE
        self.state = SessionState.ACTIVE

    async def handle(self, message: "StandardMessage") -> ReplyMessage | None:
        """处理一条入站消息（蓝图 §4.3.2）。

        Returns:
            ReplyMessage: 需要同步回复时返回；None 表示无回复。
        """
        content = (message.content or "").strip()
        logger.debug("Session[%s] handle: %s", self.conversation_id, content[:60])

        # ① 短路指令：闲聊/问候/自我介绍 → 直接回复，不创建 WorkItem
        fast = self._try_fast_reply(content, message.sender_name)
        if fast is not None:
            return self._reply(message, fast)

        # ② 非短路指令：拆分为 WorkItem（本期 Mock 判定为单一任务）
        work_items = self._split_into_workitems(message)
        if not work_items:
            # 兜底：无法理解 → 引导性回复
            return self._reply(
                message,
                "我暂时不太确定你的意思，可以换个说法吗？比如"
                "「帮我创建事件：样板段放线完成」。",
            )

        # ③ 入队 + 经公共 Pipeline BUS 执行
        for wi in work_items:
            self.scheduler.enqueue(wi)
        done = await self.scheduler.run_all()

        # ④ 出站：汇总各 WorkItem 成果（已在 BUS node4 完成审核）
        replies = [wi.result_text for wi in done if wi.result_text]
        if not replies:
            return self._reply(message, "Emily 已处理完毕。")
        return self._reply(message, "\n\n".join(replies))

    # ── 意图识别 + WorkItem 拆分 ──

    def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
        """将消息拆分为 0..N 个 WorkItem（蓝图 §4.3.2）。

        本期 Mock 实现：非短路消息 → 1 个 WorkItem（单一任务）。
        真实的复合任务拆分（LLM 意图识别 + 依赖排序）属 Phase B/C。
        """
        return [
            WorkItem(
                session_id=self.conversation_id,
                user_input=message.content or "",
                user_id=self.context.user_id,
                priority=1,
            )
        ]

    # ── Session 注销归档（蓝图 §3.5）──

    async def archive(self) -> None:
        """执行注销归档（占位：状态机推进 + 钩子位）。

        真实归档 SOP（SOP-010-SYS-session_archive：更新用户长期记忆、
        通信记录归档）属 Phase B/C。
        """
        if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
            return
        self.state = SessionState.ARCHIVING
        logger.info("Session[%s] archiving...", self.conversation_id)
        # TODO(Phase B): 更新用户长期记忆 + 通信记录归档 + SOP-010
        self.state = SessionState.CLOSED

    # ── 辅助 ──

    def _reply(self, message: "StandardMessage", content: str) -> ReplyMessage:
        """构建回复对象（群聊时引用原消息）。"""
        msg_id = None
        if getattr(message, "conversation_type", "") == "group":
            msg_id = message.message_id
        return ReplyMessage(
            conversation_id=message.conversation_id,
            content=content,
            reply_to_message_id=msg_id,
        )

    @staticmethod
    def _try_fast_reply(content: str, sender_name: str = "") -> str | None:
        """闲聊快速通道（与旧 message_app 一致）。"""
        text = content.strip().lower().replace(" ", "")
        if not text:
            return None
        if text in _SIMPLE_GREETINGS:
            greeting = f"你好呀，{sender_name}！" if sender_name else "你好呀！"
            return f"{greeting} 有什么需要帮忙的吗？"
        if text in _SIMPLE_THANKS or any(k in text for k in ["谢谢", "感谢", "thank"]):
            return "不客气！随时为你效劳。"
        if text in _SIMPLE_FAREWELLS:
            return "再见，有事随时找我！"
        if text in _SIMPLE_SELF_INTRO or any(k in text for k in ["你是谁", "你叫什么"]):
            return ("我是 Emily，你的工程项目管理助手。可以帮你记录事件、管理任务、"
                    "归档会议、查询项目数据，随时吩咐！")
        return None
