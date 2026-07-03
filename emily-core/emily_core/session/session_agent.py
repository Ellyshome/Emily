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

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .session_state import SessionState
from .session_context import SessionContext, format_message_history, build_compress_messages
from .focus_lock import FocusLock
from .confirm_queue import ConfirmQueue
from ..workitem import WorkItem, SessionScheduler
from ..adapters.standard.reply import ReplyMessage
from ..services.event_journal import EventJournal

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

# ── messages 多轮记忆配置 ──
_MAX_HISTORY_MESSAGES = 40       # message_history 上限（20 轮 × 2）
_COMPRESS_BATCH_SIZE = 20        # 每次压缩处理的消息数（溢出时取最旧 20 条）


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

        # BUG-004: 记录 Session 创建时间（供归档时使用）
        from datetime import datetime, timezone
        self._created_at = datetime.now(timezone.utc).isoformat()

        # 灌注完成 → ACTIVE
        self.state = SessionState.ACTIVE

    async def handle(self, message: "StandardMessage") -> ReplyMessage | None:
        """处理一条入站消息（蓝图 §4.3.2 + Phase B 升级）。

        包装 _handle_impl()，在回复后记录本轮对话到 message_history。
        """
        reply = await self._handle_impl(message)
        if reply is not None:
            self._record_turn(message, reply.content)
        return reply

    async def _handle_impl(self, message: "StandardMessage") -> ReplyMessage | None:
        """内部方法：实际的消息处理逻辑（从 handle() 提取）。

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

        # ═══ TC-J03: SYS-confirm 直接处理，不经过 Pipeline BUS ═══
        for wi in work_items:
            if wi.sop_id == "SYS-confirm":
                confirm_reply = await self._handle_confirm(wi)
                if confirm_reply:
                    return self._reply(message, confirm_reply)
                return self._reply(message, "确认处理完成。")

        # ③ 入队 + 经公共 Pipeline BUS 执行（携带原始消息，确保附件等元数据可用）
        for wi in work_items:
            self.scheduler.enqueue(wi)
        done = await self.scheduler.run_all_with_message(message)

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

        使用 chat_messages() 传入完整 message_history，利用 KV cache 复用。
        LLM 根据 SOP 目录 + 对话历史语义匹配用户意图。

        TC-J03: 注入当前 session 的 pending 确认状态到 LLM 上下文，
        使 LLM 能识别"确认"/"取消"类回复并路由到 SYS-confirm。

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

        # 构建 system prompt：注入 SOP 目录
        try:
            sop_catalog = self._sop_intent_registry.dump_as_text()
        except Exception as e:
            logger.warning("Failed to dump SOP catalog: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"SOP目录加载失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}

        system_prompt = _SESSION_SYSTEM_PROMPT.format(
            sop_catalog=sop_catalog,
            current_datetime=_beijing_now_str(),
        )

        # ═══ 组装多轮 messages: [system] + message_history + [current_user] ═══
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.context.message_history)
        # 注：不拼接当前 user message 到 message_history 中——那是 _record_turn 的工作。
        # 这里只是临时拼一条 user message 给 LLM 看。

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
            logger.debug("Session[%s] injected pending context: %s",
                         self.conversation_id, pending_event.event_no)

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
        """Phase B: 基于 LLM 意图识别的 WorkItem 拆分（蓝图 §4.3.2）。

        流程：
        1. LLM 意图识别 → 获取 sop_id / is_compound / sub_tasks
        2. TC-J03: SYS-confirm → 特殊处理，直接确认/取消 pending 事件
        3. 回退 → 1 个 fallback WorkItem
        4. 复合请求 → N 个 WorkItem（每个子任务一个）
        5. 单 SOP 匹配 → 1 个 WorkItem
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

        # ═══ TC-J03: SYS-confirm 特殊处理 ═══
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
                    priority=0,  # 最高优先级
                )
                # 将确认上下文存入 WorkItem（避免创建新的属性字段污染 WorkItem 模型）
                # 使用已有的 payload 机制
                setattr(wi, "_confirm_action", action)
                setattr(wi, "_confirm_event_id", pending_event.id)
                logger.info(
                    "Session[%s] SYS-confirm: action=%s event=%s",
                    self.conversation_id, action, pending_event.event_no,
                )
                return [wi]
            else:
                logger.debug("Session[%s] SYS-confirm but no pending event — fallback", self.conversation_id)
                # 无 pending 事件，降级为普通对话
                return [WorkItem(
                    session_id=self.conversation_id,
                    user_input=content,
                    user_id=self.context.user_id,
                    sop_id=None,
                    intent_type="fallback",
                    priority=1,
                )]

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

    def _get_pending_event(self):
        """TC-J03: 查找当前 conversation 中最近的 pending 事件。

        用于确认流程：当用户在已有 pending 事件的 Session 中回复时，
        将此信息注入 LLM 上下文，使 LLM 能正确路由到 SYS-confirm。

        BUG-005 修复：改用 EventRepository.find_pending_by_conversation_id()
        直查，不再依赖 messages 表中转。

        Returns:
            Event | None: 最近的 pending 事件，或 None
        """
        try:
            from ..repositories.event_repo import EventRepository
            repo = EventRepository()
            return repo.find_pending_by_conversation_id(self.conversation_id)
        except Exception as e:
            logger.debug("_get_pending_event failed: %s", e)
            return None

    async def _handle_confirm(self, wi) -> str | None:
        """TC-J03: 处理 SYS-confirm WorkItem，直接确认/取消 pending 事件。

        不经过 Pipeline BUS（无需 LLM planning/execution），
        直接调用 EventApplication.handle_confirmation() + journal 写入。

        Args:
            wi: SYS-confirm 类型的 WorkItem

        Returns:
            用户可读的确认结果文本，或 None
        """
        action = getattr(wi, "_confirm_action", "confirm")
        event_id = getattr(wi, "_confirm_event_id", "")

        if not event_id:
            logger.warning("SYS-confirm: no _confirm_event_id on WorkItem %s", wi.id)
            return "没有待确认的事件。请重新录入。"

        try:
            from ..repositories.event_repo import EventRepository
            from ..services.event_service import EventService
            from ..application.event_app import EventApplication

            # 查找 pending 事件
            event_repo = EventRepository()
            event = event_repo.get_by_id(event_id)

            if event is None:
                return "找不到该事件记录，可能已被处理。"
            if event.status != "pending":
                return f"该事件（{event.event_no}）已经处理过了，当前状态为「{event.status}」。"

            # 创建 Application 并注入 journal
            event_service = EventService()
            event_app = EventApplication(event_service)

            # TC-J03: 注入 journal（与 EmilyCore 中路径一致，追加写幂等）
            journal = EventJournal(
                path="",  # 使用默认路径推导
                enabled=True,
            )
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
        """Phase B + BUG-004: 执行注销归档（蓝图 §3.5）。

        流程：状态推进 → 清空待确认队列 → 标记活跃 WorkItem 失败
              → 持久化归档到 session_archives 表
              → 整合 conversation_summary 到 users 表
              → 关闭。
        """
        if self.state in (SessionState.CLOSED, SessionState.ARCHIVING):
            return
        self.state = SessionState.ARCHIVING
        logger.info("Session[%s] archiving (history_msgs=%d)...",
                     self.conversation_id, len(self.context.message_history))

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

            # 3. BUG-004: 持久化归档到 session_archives 表
            await self._persist_archive()

            # 4. BUG-004: 整合 conversation_summary 到 users 表
            if self.context.user_id and self._llm:
                await self._consolidate_conversation_summary()

            logger.info("Session[%s] archived successfully", self.conversation_id)
        except Exception as e:
            logger.warning("Session[%s] archive warning: %s", self.conversation_id, e)
        finally:
            self.state = SessionState.CLOSED

    async def _persist_archive(self) -> None:
        """BUG-004: 将 Session 关键数据持久化到 session_archives 表。"""
        try:
            import json
            from ..repositories.session_archive_repo import SessionArchiveRepo

            turn_count = len(self.context.message_history) // 2
            # 快照：保留最近 40 条消息（约 20 轮）
            history_snapshot = json.dumps(
                self.context.message_history[-40:], ensure_ascii=False
            )
            context_snapshot = json.dumps({
                "user_name": self.context.user_name,
                "sop_catalog_summary": self.context.sop_catalog_summary,
                "permission_level": self.context.permissions.permission_level,
                "company_name": self.context.permissions.company_name,
            }, ensure_ascii=False)

            SessionArchiveRepo.create(
                conversation_id=self.conversation_id,
                user_id=self.context.user_id or None,
                user_name=self.context.user_name,
                turn_count=turn_count,
                message_history_snapshot=history_snapshot,
                context_snapshot=context_snapshot,
                started_at=getattr(self, "_created_at", None),
                archive_reason="expired",
            )
            logger.info(
                "Session[%s] archive persisted: %d turns",
                self.conversation_id, turn_count,
            )
        except Exception as e:
            logger.warning("Session[%s] archive persist failed: %s", self.conversation_id, e)

    async def _consolidate_conversation_summary(self) -> None:
        """BUG-004: 归档时整合本次对话到 users.conversation_summary。

        流程：
        1. 读取 users.conversation_summary 已有内容
        2. 将本次 message_history 格式化为文本
        3. 调用 LLM 将（旧摘要 + 新对话）压缩为新的摘要
        4. 回写 users.conversation_summary
        """
        from ..repositories.user_repo import UserRepository

        user_id = self.context.user_id
        if not user_id:
            return

        user = UserRepository.get_by_id(user_id)
        if not user:
            return

        existing_summary = user.conversation_summary or ""
        current_conversation = format_message_history(self.context.message_history)

        # 空对话不整合
        if not current_conversation or current_conversation == "（无历史消息）":
            return

        # 构建压缩 prompt
        compress_messages = [
            {"role": "system", "content": (
                "你是一个对话摘要助手。将用户的「已有历史摘要」和「本次对话」合并为一份新的摘要。"
                "只保留关键事实：人物、事件、决策、任务、时间。不超过 500 字。"
            )},
            {"role": "user", "content": (
                f"## 已有历史摘要\n{existing_summary or '（无）'}\n\n"
                f"## 本次对话\n{current_conversation}\n\n"
                f"请输出合并后的完整摘要："
            )},
        ]

        try:
            result = await self._llm.chat_messages(compress_messages)
            new_summary = result.get("content", "") or ""
            if new_summary and len(new_summary) > 20:
                UserRepository.update_user(user_id, conversation_summary=new_summary)
                logger.info(
                    "Session[%s] conversation_summary consolidated for user %s (%d→%d chars)",
                    self.conversation_id, user_id,
                    len(existing_summary), len(new_summary),
                )
        except Exception as e:
            logger.warning("Session[%s] conversation_summary consolidation failed: %s",
                           self.conversation_id, e)

    # ── messages 多轮记忆：轮次记录 + 溢出压缩 ──

    def _record_turn(self, message: "StandardMessage", reply_content: str) -> None:
        """记录一轮对话到 message_history 滑动窗口。

        消息历史存储为 OpenAI 格式的 user/assistant 消息对。
        窗口满时异步触发压缩（不阻塞当前回复）。
        """
        sender = getattr(message, "sender_name", "") or ""
        self.context.message_history.append({
            "role": "user",
            "content": (message.content or "")[:2000],
            "name": sender if sender else None,
        })
        self.context.message_history.append({
            "role": "assistant",
            "content": (reply_content or "")[:2000],
        })

        # 溢出检查：异步触发压缩
        if len(self.context.message_history) > _MAX_HISTORY_MESSAGES:
            asyncio.ensure_future(self._compress_overflow())

        logger.debug("Session[%s] recorded turn → history: %d msgs",
                     self.conversation_id, len(self.context.message_history))

    async def _compress_overflow(self) -> None:
        """裁剪 message_history：取最旧一批消息，调用 LLM 压缩为摘要。

        摘要以 {"role":"user","name":"system","content":"[对话历史摘要] ..."} 的
        形式插入 message_history 头部，替换被压缩的旧消息。
        LLM 不可用时直接丢弃旧消息（fail-open）。
        """
        batch = self.context.message_history[:_COMPRESS_BATCH_SIZE]
        self.context.message_history = self.context.message_history[_COMPRESS_BATCH_SIZE:]

        if not self._llm:
            logger.debug("Session[%s] compression skipped (no LLM): %d msgs dropped",
                         self.conversation_id, len(batch))
            return

        # 提取已有的历史摘要（如果存在）
        existing_summary = ""
        if (self.context.message_history
                and self.context.message_history[0].get("name") == "system"
                and "[对话历史摘要]" in self.context.message_history[0].get("content", "")):
            existing_summary = self.context.message_history[0]["content"]
            self.context.message_history = self.context.message_history[1:]

        compress_msgs = build_compress_messages(batch, existing_summary)
        try:
            result = await self._llm.chat_messages(compress_msgs)
            summary_content = result.get("content", "") or ""
            if summary_content and len(summary_content) > 20:
                self.context.message_history.insert(0, {
                    "role": "user",
                    "content": f"[对话历史摘要] {summary_content.strip()}",
                    "name": "system",
                })
                logger.info("Session[%s] compressed %d msgs → summary (%d chars), history now %d msgs",
                            self.conversation_id, len(batch),
                            len(summary_content), len(self.context.message_history))
        except Exception as e:
            logger.warning("Session[%s] compression failed (msgs dropped): %s",
                           self.conversation_id, e)
            # fail-open: 旧消息已被截除，不阻塞对话

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
