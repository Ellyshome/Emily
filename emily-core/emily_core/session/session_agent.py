"""SessionAgent —— 会话调度主脑（蓝图 §4.3）。

以 Session-Agent 为核心，负责：
  · 用自然语言与用户交互
  · 对入站消息做意图识别 + WorkItem 拆分（蓝图 §4.3.2）
  · 管理多 WorkItem 并发、优先级调度、待确认队列
  · 出站消息审核
  · Session 注销归档

Phase B 实现：
  - LLM 意图识别（从 SOPIntentRegistry 注入 SOP 目录，单次 chat_json 匹配）
  - 复合请求检测与多 WorkItem 拆分
  - Pipeline 节点1 简化为验证+注入（路由已在 SessionAgent 完成）
  - FocusLock + ConfirmQueue 接入 handle()
  - 真实的 SessionAgent（由 MasterAgent 升级，多 WorkItem 编排 + LLM 意图识别）

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
from datetime import datetime, timezone, timedelta
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

# ══════════════════════════════════════════════════════════════════════════════
# 闲聊短路（与旧 message_app 对齐，节省 LLM 调用）
# ══════════════════════════════════════════════════════════════════════════════

_SIMPLE_GREETINGS = {
    "你好", "早", "早上好", "下午好", "晚上好", "早安", "午安",
    "hi", "hello", "hey", "nihao", "在吗", "在不", "在不在",
}
_SIMPLE_THANKS = {"谢谢", "感谢", "多谢", "thanks", "thank you", "thx", "3q"}
_SIMPLE_FAREWELLS = {"再见", "拜拜", "bye", "goodbye", "晚安", "回头见", "下次见"}
_SIMPLE_SELF_INTRO = {"你是谁", "你叫什么", "你是什么", "who are you", "介绍自己", "介绍一下自己"}

# ══════════════════════════════════════════════════════════════════════════════
# Phase B: Session Agent 系统提示（从 prompt 文件加载，含人格 + 路由）
# ══════════════════════════════════════════════════════════════════════════════

def _load_session_prompt() -> str:
    """加载 SessionAgent 核心系统 prompt（带缓存）

    session.md 包含：Emy 人格 / 回复格式规范 / 路由规则 / JSON 输出约束
    旧 routing.md 的内容已合并到此 prompt 中，不再单独加载。
    """
    from ..infrastructure.llm.prompt_loader import load_prompt
    return load_prompt("session")


_SESSION_SYSTEM_PROMPT = _load_session_prompt()


def _beijing_now_str() -> str:
    """返回北京时间字符串（供 LLM prompt 使用）。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


class SessionAgent:
    """会话调度主脑 —— 每个 Session 一个实例。

    Phase B 升级（蓝图 §12.2）：
      - MockRouter → Session-Agent 意图识别（LLM 单次 chat_json 路由）
      - 复合请求 → 多 WorkItem 拆分
      - Pipeline 节点1 简化为验证+注入
    """

    def __init__(
        self,
        conversation_id: str,
        context: SessionContext,
        bus: "PipelineBUS",
        # ── Phase B: 意图识别依赖 ──
        llm_client=None,
        sop_intent_registry=None,
    ):
        self.conversation_id = conversation_id
        self.context = context
        self.state = SessionState.CREATED
        self.focus = FocusLock()
        self.confirm_queue = ConfirmQueue()

        # 权限架构 v1.2：SessionContext 不直接灌注到 Session-Agent
        # 而是通过 SessionScheduler 传递给 BusContext
        # WorkItemAgent 通过 BusContext 只读方法获取权限信息
        self.scheduler = SessionScheduler(conversation_id, bus, session_context=context)

        # Phase B: 意图识别
        self._llm = llm_client
        self._sop_intent_registry = sop_intent_registry

        # 灌注完成 → ACTIVE
        self.state = SessionState.ACTIVE

    async def handle(self, message: "StandardMessage") -> ReplyMessage | None:
        """处理一条入站消息（蓝图 §4.3.2 + Phase B 升级）。

        Returns:
            ReplyMessage: 需要同步回复时返回；None 表示无回复。
        """
        content = (message.content or "").strip()
        logger.debug("Session[%s] handle: %s", self.conversation_id, content[:60])

        # ① 短路指令：闲聊/问候/自我介绍 → 直接回复，不创建 WorkItem
        fast = self._try_fast_reply(content, message.sender_name)
        if fast is not None:
            return self._reply(message, fast)

        # ①b Phase B: 焦点切换检测
        if FocusLock.wants_switch(content):
            self.focus.clear_focus()
            logger.debug("Session[%s] focus cleared by user switch", self.conversation_id)

        # ② Phase B: LLM 意图识别 + WorkItem 拆分（async）
        work_items = await self._split_into_workitems(message)
        if not work_items:
            # 兜底：无法理解 → 引导性回复
            return self._reply(
                message,
                "我暂时不太确定你的意思，可以换个说法吗？比如"
                "「帮我创建事件：样板段放线完成」。",
            )

        # ②b Phase B: 设置焦点到第一个 WorkItem
        self.focus.set_focus(work_items[0].id)

        # ③ 入队 + 经公共 Pipeline BUS 执行
        for wi in work_items:
            self.scheduler.enqueue(wi)
        done = await self.scheduler.run_all()

        # ③b Phase B: 检查待确认队列
        pending = self._collect_pending_confirms(done)
        if pending:
            return self._reply(message, pending)

        # ④ 出站：汇总各 WorkItem 成果（已在 BUS node4 完成审核）
        replies = [wi.result_text for wi in done if wi.result_text]
        if not replies:
            return self._reply(message, "Emily 已处理完毕。")
        return self._reply(message, "\n\n".join(replies))

    # ── Phase B: 意图识别 + WorkItem 拆分 ──

    async def _recognize_intent(self, message: "StandardMessage") -> dict:
        """LLM 意图识别：匹配用户消息到 SOP（蓝图 §4.3.2 Phase B）。

        从 MasterAgent 的路由逻辑提取核心——单次 chat_json() 调用，
        不做 ReAct 循环。LLM 根据 SOP 目录语义匹配用户意图。

        Returns:
            dict: LLM 输出的 JSON 字典，字段：
                sop_id (str|None), confidence (str), reasoning (str),
                is_compound (bool), sub_tasks (list), fallback (bool)
        """
        content = message.content or ""

        # 无 LLM 或注册表 → 回退
        if not self._llm or not self._sop_intent_registry:
            return {"sop_id": None, "confidence": "none", "reasoning": "",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        # 空消息 → 回退
        if not content.strip():
            return {"sop_id": None, "confidence": "none", "reasoning": "空消息",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        # 构建 prompt：注入 SOP 目录
        try:
            sop_catalog = self._sop_intent_registry.dump_as_text()
        except Exception as e:
            logger.warning("Failed to dump SOP catalog: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"SOP目录加载失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        prompt = _SESSION_SYSTEM_PROMPT.format(
            sop_catalog=sop_catalog,
            current_datetime=_beijing_now_str(),
        )

        try:
            result = await self._llm.chat_json(prompt, content)
            logger.debug("SessionAgent intent for '%s': sop=%s conf=%s compound=%s",
                         content[:40], result.get("sop_id"), result.get("confidence"),
                         result.get("is_compound"))
            return result
        except Exception as e:
            logger.warning("SessionAgent intent recognition failed: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"LLM调用失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

    async def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
        """Phase B: 基于 LLM 意图识别的 WorkItem 拆分（蓝图 §4.3.2）。

        流程：
        1. LLM 意图识别 → 获取 sop_id / is_compound / sub_tasks
        2. 回退 → 1 个 fallback WorkItem
        3. 复合请求 → N 个 WorkItem（每个子任务一个）
        4. 单 SOP 匹配 → 1 个 WorkItem
        """
        content = message.content or ""

        # LLM 意图识别
        intent = await self._recognize_intent(message)

        sop_id = intent.get("sop_id")
        is_compound = intent.get("is_compound", False)
        sub_tasks = intent.get("sub_tasks", [])
        fallback = intent.get("fallback", False)
        confidence = intent.get("confidence", "none")

        logger.info(
            "Session[%s] intent: sop=%s conf=%s compound=%s fallback=%s sub_tasks=%d",
            self.conversation_id, sop_id, confidence, is_compound, fallback, len(sub_tasks),
        )

        # 回退：无匹配 SOP → 1 个 fallback WorkItem
        if fallback or not sop_id:
            return [WorkItem(
                session_id=self.conversation_id,
                user_input=content,
                user_id=self.context.user_id,
                sop_id=None,
                intent_type="fallback",
                priority=1,
            )]

        # 复合请求：每个子任务一个 WorkItem
        if is_compound and sub_tasks:
            max_n = getattr(self.context, "workitem_max_per_session", 5) or 5
            items = []
            prev_id = None
            for i, st in enumerate(sub_tasks[:max_n]):
                wi = WorkItem(
                    session_id=self.conversation_id,
                    user_input=st.get("user_input", content) if isinstance(st, dict) else content,
                    user_id=self.context.user_id,
                    sop_id=st.get("sop_id", sop_id) if isinstance(st, dict) else sop_id,
                    intent_type="sop",
                    priority=st.get("priority", 1) if isinstance(st, dict) else 1,
                )
                items.append(wi)
                prev_id = wi.id
            return items

        # 单 SOP 匹配：1 个 WorkItem
        return [WorkItem(
            session_id=self.conversation_id,
            user_input=content,
            user_id=self.context.user_id,
            sop_id=sop_id,
            intent_type="sop",
            priority=1,
        )]

    # ── Phase B: 待确认队列收集 ──

    def _collect_pending_confirms(self, done_workitems: list) -> str | None:
        """收集需要用户确认的 WorkItem（蓝图 §4.3.3）。"""
        from ..workitem.workitem_state import WorkItemState

        needs_confirm = [
            wi for wi in done_workitems
            if wi.state == WorkItemState.WAITING_CONFIRM
        ]
        for wi in needs_confirm:
            self.confirm_queue.add(
                workitem_id=wi.id,
                prompt=f"关于「{wi.user_input[:50]}...」需要你的确认",
                priority=wi.priority,
            )

        if not self.confirm_queue.is_empty:
            entry = self.confirm_queue.pop()
            if entry:
                return entry.prompt
        return None

    # ── Session 注销归档（蓝图 §3.5）──

    async def archive(self) -> None:
        """Phase B: 执行注销归档（蓝图 §3.5）。

        流程：状态推进 → 清空待确认队列 → 标记活跃 WorkItem 失败 → 关闭。
        SOP-010 完整归档逻辑（用户记忆更新 + 通信记录归档）属 Phase C。
        """
        if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
            return
        self.state = SessionState.ARCHIVING
        logger.info("Session[%s] archiving (turns=%d)...",
                     self.conversation_id, len(self.context.recent_turns))

        try:
            # 1. 清空待确认队列
            self.confirm_queue.clear()

            # 2. 标记活跃 WorkItem 为失败
            from ..workitem.workitem_state import WorkItemState
            for wi in list(self.scheduler._active.values()):
                if not wi.is_terminal:
                    try:
                        wi.transition_to(WorkItemState.FAILED)
                    except ValueError:
                        pass

            logger.info("Session[%s] archived successfully", self.conversation_id)
        except Exception as e:
            logger.warning("Session[%s] archive warning: %s", self.conversation_id, e)
        finally:
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
