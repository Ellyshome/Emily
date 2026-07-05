"""SessionAgent —— 会话调度主脑（蓝图 §4.3）。

以 Session-Agent 为核心，负责：
  · 用自然语言与用户交互
  · 对入站消息做意图识别 + WorkItem 拆分（蓝图 §4.3.2）
  · 管理多 WorkItem 并发、优先级调度、待确认队列
  · 出站消息审核
  · Session 注销归档

重构后：消息记录 / LLM 拼装 / 归档持久化 / 压缩全部委托 SessionContext 操作台。
SessionAgent 只保留决策职责。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .session_state import SessionState
from .session_context import SessionContext
from .focus_lock import FocusLock
from .confirm_queue import ConfirmQueue
from ..adapters.standard.reply import ReplyMessage
from ..services.event_journal import EventJournal

if TYPE_CHECKING:
    from ..adapters.standard.message import StandardMessage
    from ..workitem.pipeline.bus import PipelineBUS
    from ..workitem import WorkItem

logger = logging.getLogger("emily.session_agent")

# ══════════════════════════════════════════════════════════════════════════════
# 闲聊短路
# ══════════════════════════════════════════════════════════════════════════════

_SIMPLE_GREETINGS = {
    "你好", "早", "早上好", "下午好", "晚上好", "早安", "午安",
    "hi", "hello", "hey", "nihao", "在吗", "在不", "在不在",
}
_SIMPLE_THANKS = {"谢谢", "感谢", "多谢", "thanks", "thank you", "thx", "3q"}
_SIMPLE_FAREWELLS = {"再见", "拜拜", "bye", "goodbye", "晚安", "回头见", "下次见"}
_SIMPLE_SELF_INTRO = {"你是谁", "你叫什么", "你是什么", "who are you", "介绍自己", "介绍一下自己"}

# ══════════════════════════════════════════════════════════════════════════════
# Session Agent 系统提示
# ══════════════════════════════════════════════════════════════════════════════

def _load_session_prompt() -> str:
    from ..infrastructure.llm.prompt_loader import load_prompt
    return load_prompt("session")


_SESSION_SYSTEM_PROMPT = _load_session_prompt()


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


class SessionAgent:
    """会话调度主脑 —— 每个 Session 一个实例。

    重构后：
      - 消息记录 → context.record_turn()
      - LLM 上下文拼装 → context.build_llm_messages()
      - 归档 → context.persist_and_consolidate()
      - 压缩 → context.compress_overflow()
    """

    def __init__(
        self,
        conversation_id: str,
        context: SessionContext,
        bus: "PipelineBUS",
        llm_client=None,
        skill_registry=None,
        sop_intent_registry=None,  # 已废弃，保留签名兼容
    ):
        self.conversation_id = conversation_id
        self.context = context
        self.state = SessionState.CREATED
        self.focus = FocusLock()
        self.confirm_queue = ConfirmQueue()

        # 延迟导入避免循环依赖
        from ..workitem import SessionScheduler
        self.scheduler = SessionScheduler(conversation_id, bus, session_context=context)

        self._llm = llm_client
        self._skill_registry = skill_registry
        self._sop_intent_registry = sop_intent_registry  # 已废弃，保留签名兼容

        from datetime import datetime, timezone
        self._created_at = datetime.now(timezone.utc).isoformat()

        self.state = SessionState.ACTIVE

    async def handle(self, message: "StandardMessage") -> ReplyMessage | None:
        """处理一条入站消息（蓝图 §4.3.2）。"""
        reply = await self._handle_impl(message)
        if reply is not None:
            self._record_turn(message, reply.content)
            # 溢出压缩由 record_turn 内部检测触发
            if len(self.context.message_history) > 40 and self._llm:
                asyncio.ensure_future(self.context.compress_overflow(self._llm))
        return reply

    async def _handle_impl(self, message: "StandardMessage") -> ReplyMessage | None:
        """内部方法：实际的消息处理逻辑。"""
        content = (message.content or "").strip()
        logger.debug("Session[%s] handle: %s", self.conversation_id, content[:60])

        # ① 短路指令
        fast = self._try_fast_reply(content, message.sender_name)
        if fast is not None:
            return self._reply(message, fast)

        # ①b 焦点切换检测
        if FocusLock.wants_switch(content):
            self.focus.clear_focus()
            logger.debug("Session[%s] focus cleared by user switch", self.conversation_id)

        # ② LLM 意图识别 + WorkItem 拆分
        work_items = await self._split_into_workitems(message)
        if not work_items:
            return self._reply(
                message,
                "我暂时不太确定你的意思，可以换个说法吗？比如"
                "「帮我创建事件：样板段放线完成」。",
            )

        # ②b 设置焦点
        self.focus.set_focus(work_items[0].id)

        # SYS-confirm 直接处理
        for wi in work_items:
            if wi.sop_id == "SYS-confirm":
                confirm_reply = await self._handle_confirm(wi)
                if confirm_reply:
                    return self._reply(message, confirm_reply)
                return self._reply(message, "确认处理完成。")

        # ③ 经 Pipeline BUS 执行
        for wi in work_items:
            self.scheduler.enqueue(wi)
        done = await self.scheduler.run_all_with_message(message)

        # ③b 待确认队列
        pending = self._collect_pending_confirms(done)
        if pending:
            return self._reply(message, pending)

        # ④ 汇总
        replies = [wi.result_text for wi in done if wi.result_text]
        if not replies:
            return self._reply(message, "Emily 已处理完毕。")
        return self._reply(message, "\n\n".join(replies))

    # ── 意图识别 + WorkItem 拆分 ──

    async def _recognize_intent(self, message: "StandardMessage") -> dict:
        """LLM 意图识别。"""
        content = message.content or ""

        # 优先使用 SkillRegistry，回退到 SOPIntentRegistry（已废弃但保留兼容）
        catalog_source = self._skill_registry or self._sop_intent_registry
        if not self._llm or not catalog_source:
            return {"sop_id": None, "confidence": "none", "reasoning": "",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        if not content.strip():
            return {"sop_id": None, "confidence": "none", "reasoning": "空消息",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        try:
            sop_catalog = catalog_source.dump_as_text()
        except Exception as e:
            logger.warning("Failed to dump SOP catalog: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"SOP目录加载失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        system_prompt = _SESSION_SYSTEM_PROMPT.format(
            sop_catalog=sop_catalog,
            current_datetime=_beijing_now_str(),
        )

        # 注入 Session 级变量（D5：两阶段 format）
        prompt_vars = self.context.get_prompt_variables()
        for key, value in prompt_vars.items():
            if value:
                system_prompt = system_prompt.replace(key, str(value))

        # 组装 messages
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.context.message_history)

        # TC-J03: 注入 pending 确认状态
        pending_event = self._get_pending_event()
        if pending_event:
            full_messages.append({
                "role": "system",
                "content": (
                    f"⚠️ 当前存在待确认的录入项：\n"
                    f"  编号：{pending_event.event_no}\n"
                    f"  内容：{pending_event.title}\n"
                    f"  状态：等待用户确认\n"
                    f"  如果用户表达了确认/取消/修改意图，请路由到 SYS-confirm，"
                    f"不要走其他 SOP 路由。"
                ),
            })

        sender = getattr(message, "sender_name", "") or ""
        full_messages.append({
            "role": "user",
            "content": content,
            "name": sender if sender else None,
        })

        try:
            result = await self._llm.chat_messages(full_messages, json_mode=True)
            data = result.get("data", {})
            logger.debug("SessionAgent intent for '%s': sop=%s conf=%s compound=%s",
                         content[:40], data.get("sop_id"), data.get("confidence"),
                         data.get("is_compound"))
            return data
        except Exception as e:
            logger.warning("SessionAgent intent recognition failed: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"LLM调用失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

    async def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
        """Phase B: 基于 LLM 意图识别的 WorkItem 拆分。"""
        content = message.content or ""
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

        # SYS-confirm
        if sop_id == "SYS-confirm":
            action = (intent.get("data") or {}).get("action", "confirm")
            pending_event = self._get_pending_event()
            if pending_event:
                wi = WorkItem(
                    session_id=self.conversation_id,
                    user_input=content,
                    user_id=self.context.user_id,
                    sop_id="SYS-confirm",
                    intent_type="sop",
                    priority=0,
                )
                setattr(wi, "_confirm_action", action)
                setattr(wi, "_confirm_event_id", pending_event.id)
                return [wi]
            else:
                return [WorkItem(
                    session_id=self.conversation_id,
                    user_input=content,
                    user_id=self.context.user_id,
                    sop_id=None,
                    intent_type="fallback",
                    priority=1,
                )]

        if fallback or not sop_id:
            return [WorkItem(
                session_id=self.conversation_id,
                user_input=content,
                user_id=self.context.user_id,
                sop_id=None,
                intent_type="fallback",
                priority=1,
            )]

        if is_compound and sub_tasks:
            items = []
            for i, st in enumerate(sub_tasks[:5]):
                wi = WorkItem(
                    session_id=self.conversation_id,
                    user_input=st.get("user_input", content) if isinstance(st, dict) else content,
                    user_id=self.context.user_id,
                    sop_id=st.get("sop_id", sop_id) if isinstance(st, dict) else sop_id,
                    intent_type="sop",
                    priority=st.get("priority", 1) if isinstance(st, dict) else 1,
                )
                items.append(wi)
            return items

        return [WorkItem(
            session_id=self.conversation_id,
            user_input=content,
            user_id=self.context.user_id,
            sop_id=sop_id,
            intent_type="sop",
            priority=1,
        )]

    # ── Pending / Confirm ──

    def _get_pending_event(self):
        try:
            from ..repositories.event_repo import EventRepository
            repo = EventRepository()
            return repo.find_pending_by_conversation_id(self.conversation_id)
        except Exception as e:
            logger.debug("_get_pending_event failed: %s", e)
            return None

    async def _handle_confirm(self, wi) -> str | None:
        action = getattr(wi, "_confirm_action", "confirm")
        event_id = getattr(wi, "_confirm_event_id", "")

        if not event_id:
            logger.warning("SYS-confirm: no _confirm_event_id on WorkItem %s", wi.id)
            return "没有待确认的事件。请重新录入。"

        try:
            from ..repositories.event_repo import EventRepository
            from ..services.event_service import EventService
            from ..application.event_app import EventApplication

            event_repo = EventRepository()
            event = event_repo.get_by_id(event_id)

            if event is None:
                return "找不到该事件记录，可能已被处理。"
            if event.status != "pending":
                return f"该事件（{event.event_no}）已经处理过了，当前状态为「{event.status}」。"

            event_service = EventService()
            event_app = EventApplication(event_service)
            journal = EventJournal(path="", enabled=True)
            event_app.set_journal(journal)

            result = event_app.handle_confirmation(event_id=event_id, action=action)
            logger.info(
                "SYS-confirm: event=%s action=%s success=%s",
                event.event_no, action, result.success,
            )
            return result.reply or "确认操作完成。"
        except Exception as e:
            logger.error("SYS-confirm handling failed: %s", e)
            return f"确认处理失败：{e}"

    def _collect_pending_confirms(self, done_workitems: list) -> str | None:
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

    # ── Session 注销归档 ──

    async def archive(self) -> None:
        """执行注销归档（委托 SessionContext.persist_and_consolidate）。"""
        if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
            return
        self.state = SessionState.ARCHIVING
        logger.info("Session[%s] archiving (history_msgs=%d)...",
                     self.conversation_id, len(self.context.message_history))

        try:
            self.confirm_queue.clear()

            from ..workitem.workitem_state import WorkItemState
            for wi in list(self.scheduler._active.values()):
                if not wi.is_terminal:
                    try:
                        wi.transition_to(WorkItemState.FAILED)
                    except ValueError:
                        pass

            await self.context.persist_and_consolidate(llm_client=self._llm)

            logger.info("Session[%s] archived successfully", self.conversation_id)
        except Exception as e:
            logger.warning("Session[%s] archive warning: %s", self.conversation_id, e)
        finally:
            self.state = SessionState.CLOSED

    # ── 热更新 ──

    async def _maybe_refresh_context(self) -> None:
        """每 10 轮对话后检查权限/项目变更。

        使用 SessionDataFetcher.fetch() 获取最新数据，
        通过 context.refresh() 只覆盖可热更新字段。
        """
        from .session_data_fetcher import SessionDataFetcher

        try:
            data = SessionDataFetcher.fetch(
                user_id=self.context.user_id,
                conversation_id=self.conversation_id,
            )
            updated = self.context.refresh(data)
            if updated:
                logger.info("Session[%s] auto-refreshed: %s", self.conversation_id, updated)
        except Exception as e:
            logger.debug("Session[%s] refresh skipped: %s", self.conversation_id, e)

    # ── 消息记录（委托 context）──

    def _record_turn(self, message: "StandardMessage", reply_content: str) -> None:
        """记录一轮对话（委托 SessionContext.record_turn）。"""
        sender = getattr(message, "sender_name", "") or ""
        self.context.record_turn(
            user_content=(message.content or "")[:2000],
            assistant_content=(reply_content or "")[:2000],
            sender_name=sender,
        )

        # 每 10 轮检测热更新
        if len(self.context.message_history) % 20 == 0:
            asyncio.ensure_future(self._maybe_refresh_context())

    # ── 辅助 ──

    def _reply(self, message: "StandardMessage", content: str) -> ReplyMessage:
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
