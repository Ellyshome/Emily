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
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .session_state import SessionState
from .session_context import SessionContext
from .focus_lock import FocusLock
from .confirm_queue import ConfirmQueue
from ..adapters.standard.reply import ReplyMessage
from ..services.event_journal import EventJournal

from ..workitem import WorkItem

if TYPE_CHECKING:
    from ..adapters.standard.message import StandardMessage
    from ..workitem.pipeline.bus import PipelineBUS

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
        journal=None,
        archive_writer=None,
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
        self._journal = journal
        self._archive_writer = archive_writer

        from datetime import datetime, timezone
        self._created_at = datetime.now(timezone.utc).isoformat()

        self.state = SessionState.ACTIVE

        # ── 实时归档：Session 创建时写 md 文件头 ──
        self._archive_md_path = ""
        self._last_turn_workitems: list = []
        self._turn_counter: int = 0
        # 意图识别阶段 Prompt 注入信息（_recognize_intent 暂存，归档段读取）
        self._last_intent_prompt_info: dict | None = None
        if self._archive_writer is not None:
            try:
                ctx_dict = {
                    # 身份与组织
                    "user_id": context.user_id,
                    "user_position": context.user_position,
                    "company_name": context.company_name,
                    "company_type": context.company_type,
                    "company_id": context.company_id,
                    "department": list(context.department),
                    "level": context.level,
                    "is_management_unit": context.is_management_unit,
                    # 可见范围
                    "authorized_node_ids": list(context.authorized_node_ids),
                    "scopes": list(context.scopes),
                    "project_ids": list(context.project_ids),
                    "partner_ids": list(context.partner_ids),
                    "info_level": context.info_level,
                    "sop_allow": list(context.sop_allow),
                    "permission_version": context.permission_version,
                    "permissions_loaded_at": context.permissions_loaded_at,
                    # 项目上下文
                    "project_name": context.project_name,
                    "project_type": context.project_type,
                    "project_status": context.project_status,
                    # 能力摘要
                    "available_skills": list(context.available_skills),
                    "available_tools": list(context.available_tools),
                    "visible_files_count": context.visible_files_count,
                    "rag_available": context.rag_available,
                    "rag_collections": list(context.rag_collections),
                    # 大文本字段（渲染时只显示「有 + 字符数」，不写全文）
                    "project_world_book": context.project_world_book,
                    "rule_book": context.rule_book,
                    "system_description": context.system_description,
                    "visible_schema_summary": context.visible_schema_summary,
                    "visible_files_summary": context.visible_files_summary,
                    "sop_catalog_summary": context.sop_catalog_summary,
                    # prompt 元信息（模板原文；渲染后变量值已在上方快照字段覆盖）
                    "prompt_name": "session.md" if _SESSION_SYSTEM_PROMPT else "",
                    "prompt_chars": len(_SESSION_SYSTEM_PROMPT),
                }
                self._archive_md_path = self._archive_writer.ensure_header(
                    conversation_id=conversation_id,
                    user_name=context.user_name or "anonymous",
                    started_at=self._created_at,
                    context=ctx_dict,
                )
            except Exception as e:
                logger.warning("SessionArchive ensure_header failed: %s", e)

        # 将归档路径注入 scheduler，由其在 BusContext.baggage 中传递给 ArchiveHook
        self.scheduler.archive_md_path = self._archive_md_path

        # ── 进化日志：Session 创建 ──
        _log_session_lifecycle(self.conversation_id, self.context.user_id, "created")

        # M4: 预渲染 Session 级 system prompt base（sop_catalog + 非权限变量，Session 内稳定）
        # 权限变量由 _build_rendered_system_prompt 每条消息渲染（群聊多用户权限修复 BUG #3）
        self._session_prompt_base = self._build_session_prompt_base()
        logger.debug("Session[%s] system prompt base cached: %d chars",
                     self.conversation_id, len(self._session_prompt_base))

    # 权限变量集合：随当前操作者变化，不进 base 缓存，由 _build_rendered_system_prompt 每条消息渲染
    _PERM_PROMPT_KEYS = frozenset({
        "{user_company}", "{user_company_type}", "{user_department}",
        "{user_level}", "{user_permission_level}", "{current_node_ids}",
        "{available_skills}",
    })

    def _build_session_prompt_base(self) -> str:
        """渲染 Session 级 system prompt base（sop_catalog + 非权限变量）。

        Session 内字节级稳定，是 DeepSeek cache 命中的前提。
        权限变量不在此渲染（由 _build_rendered_system_prompt 每条消息补）。
        """
        try:
            sop_catalog = self._skill_registry.dump_as_text()
        except Exception as e:
            logger.warning("Failed to dump SOP catalog for cached prompt: %s", e)
            sop_catalog = "（SOP 目录加载失败）"

        prompt = _SESSION_SYSTEM_PROMPT.replace("{sop_catalog}", sop_catalog)
        # 只替换非权限变量（权限变量留给每条消息渲染）
        prompt_vars = self.context.get_prompt_variables()
        for key, value in prompt_vars.items():
            if key in self._PERM_PROMPT_KEYS:
                continue
            replacement = str(value) if value else "（无）"
            prompt = prompt.replace(key, replacement)
        return prompt

    def _build_rendered_system_prompt(self, actor_snapshot: dict | None = None) -> str:
        """渲染完整 system prompt：base + 权限变量（当前操作者）。

        群聊多用户权限修复（BUG #3）：权限字段用 actor_snapshot（当前操作者），
        使 LLM 看到的权限与 AuthHook 鉴权用的权限一致。
        私聊场景 actor_snapshot=None，回退 Session 创建者（与原行为一致，base cache 仍命中）。
        """
        prompt = self._session_prompt_base
        # 只替换权限变量（用当前操作者的值）
        prompt_vars = self.context.get_prompt_variables(actor_snapshot)
        for key in self._PERM_PROMPT_KEYS:
            value = prompt_vars.get(key, "")
            replacement = str(value) if value else "（无）"
            prompt = prompt.replace(key, replacement)
        return prompt

    async def handle(self, message: "StandardMessage", db_message_id: str = "", current_user_id: str = "") -> ReplyMessage | None:
        """处理一条入站消息（蓝图 §4.3.2）。"""
        # ── 群聊多用户权限越界修复：取当前操作者权限快照 ──
        actor_user_id = current_user_id or self.context.user_id
        # 兜底：若 current_user_id 不像 UUID，回退 SessionContext.user_id
        if actor_user_id and len(actor_user_id) >= 32:
            pass  # 看起来像 UUID
        elif not actor_user_id:
            actor_user_id = self.context.user_id
        try:
            from .session_data_fetcher import SessionDataFetcher
            core = getattr(self, "_core", None)
            actor_snapshot = await asyncio.to_thread(
                SessionDataFetcher.fetch_actor_snapshot, actor_user_id, core,
            )
            actor_snapshot["user_id"] = actor_user_id
        except Exception as e:
            logger.warning("fetch_actor_snapshot failed, fallback to session ctx: %s", e)
            actor_snapshot = None

        # 暂存到实例，供 _split_into_workitems 和 scheduler 使用
        self._current_actor = actor_snapshot

        # ── 群聊 DB 回溯上下文（非阻断）──
        self._group_context = ""
        if (message.conversation_type == "group"
                and getattr(message, "group_id", None) and db_message_id):
            try:
                from ..services.group_context_service import GroupContextService
                svc = GroupContextService(llm_client=self._llm)
                self._group_context = await svc.build_group_context(
                    group_id=message.group_id,
                    current_message_id=db_message_id,
                    user_question=message.content or "",
                )
                if self._group_context:
                    logger.debug("Session[%s] group context built: %d chars",
                                 self.conversation_id, len(self._group_context))
            except Exception as e:
                logger.warning("group context build failed (non-blocking): %s", e)

        # ── 群级长期记忆注入（非阻断）──
        self._group_memory_injection = ""
        if message.conversation_type == "group" and getattr(message, "group_id", None):
            try:
                from ..services.group_memory_service import GroupMemoryService
                self._group_memory_injection = GroupMemoryService().build_injection(
                    message.group_id
                )
                if self._group_memory_injection:
                    logger.info("Session[%s] group memory injected: %d chars",
                                 self.conversation_id, len(self._group_memory_injection))
                else:
                    logger.info("Session[%s] no group memory found for group=%s",
                                 self.conversation_id, message.group_id)
            except Exception as e:
                logger.debug("group memory injection failed: %s", e)

        # ── 归档：轮次开头（用户消息段，BUS 之前写入）──
        self._append_archive_turn_start(message)
        reply = await self._handle_impl(message, db_message_id=db_message_id)
        if reply is not None:
            self._record_turn(message, reply.content)
            # 溢出压缩由 record_turn 内部检测触发
            if len(self.context.message_history) > 40 and self._llm:
                asyncio.ensure_future(self.context.compress_overflow(self._llm))
            # ── 归档：轮次结尾（系统审核标记 + Emily 回复，BUS 之后写入）──
            self._append_archive_turn_end(reply)
            # ── 进化日志：反馈信号检测 ──
            _detect_feedback(message.content or "", reply.content or "",
                             self.conversation_id, self.context.user_id)
        return reply

    async def _handle_impl(self, message: "StandardMessage", db_message_id: str = "") -> ReplyMessage | None:
        """内部方法：实际的消息处理逻辑。"""
        content = (message.content or "").strip()
        logger.debug("Session[%s] handle: %s", self.conversation_id, content[:60])

        # 重置本轮 WorkItem 追踪
        self._last_turn_workitems = []

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
                    self._last_turn_workitems = [wi]
                    return self._reply(message, confirm_reply)
                self._last_turn_workitems = [wi]
                return self._reply(message, "确认处理完成。")

        # ③ 经 Pipeline BUS 执行
        for wi in work_items:
            self.scheduler.enqueue(wi)
        done = await self.scheduler.run_all_with_message(message, db_message_id=db_message_id, actor_snapshot=getattr(self, "_current_actor", None))
        self._last_turn_workitems = done

        # ③b 待确认队列
        pending = self._collect_pending_confirms(done)
        if pending:
            return self._reply(message, pending)

        # ④ M4: Session 合成最终回复（替代 join 拼接）
        final_reply = await self._synthesize_final_reply(message, done)
        return self._reply(message, final_reply)

    # ── 意图识别 + WorkItem 拆分 ──

    async def _recognize_intent(self, message: "StandardMessage") -> dict:
        """LLM 意图识别。"""
        content = message.content or ""

        # 使用 SkillRegistry 作为意图识别目录源
        catalog_source = self._skill_registry
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

        # 渲染 system prompt（权限变量用当前操作者，群聊多用户权限修复 BUG #3）
        system_prompt = self._build_rendered_system_prompt(getattr(self, "_current_actor", None))

        # 组装 messages
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.context.message_history)

        # ── 群级长期记忆注入 ──
        group_mem = getattr(self, "_group_memory_injection", "")
        if group_mem:
            full_messages.append({
                "role": "system",
                "content": group_mem,
            })

        # ── 群聊 DB 回溯上下文注入 ──
        group_ctx = getattr(self, "_group_context", "")
        if group_ctx:
            full_messages.append({
                "role": "system",
                "content": group_ctx,
            })

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
        # M3: 当前时间从 system prompt 迁到 user message 末尾，保持 system 前缀稳定
        user_content = f"{content}\n\n[当前时间: {_beijing_now_str()}]"
        full_messages.append({
            "role": "user",
            "content": user_content,
            "name": sender if sender else None,
        })

        # ── 归档：暂存意图识别 Prompt 注入信息 + 设置 LLM 日志上下文 ──
        # 意图识别在 BUS 之前运行，用独立 pipeline_run_id 标记，归档据此查回日志。
        from ..infrastructure.logging.llm_logger import LLMInteractionLogger
        intent_run_id = f"intent-{self.conversation_id[:8]}"
        authorized = list(getattr(self.context, "authorized_node_ids", []) or [])
        self._last_intent_prompt_info = {
            "template": "session.md",
            "rendered_chars": len(system_prompt),
            "variables": {
                "sop_catalog": f"{len(sop_catalog)}字",
                "current_datetime": _beijing_now_str(),
                "user_name": (getattr(self.context, "user_name", "") or "")[:80],
                "project_name": (getattr(self.context, "project_name", "") or "")[:80],
                "authorized_node_ids": (
                    "、".join(authorized[:5]) + ("..." if len(authorized) > 5 else "")
                ) or "（无）",
            },
        }
        LLMInteractionLogger.set_context(
            pipeline_run_id=intent_run_id,
            conversation_id=self.conversation_id,
            user_id=self.context.user_id,
            call_category="intent",
        )
        try:
            # M5: 路由用 router_model（v4-flash，便宜快）。M4 已缓存稳定 prompt，flash 可靠性提升。
            # fallback：flash 返回空白/解析失败/异常时回退主模型（v4-pro），避免路由退化。
            router_model = getattr(self._llm, "router_model", None) or self._llm.model
            data = {}
            try:
                result = await self._llm.chat_messages(full_messages, json_mode=True, model=router_model)
                data = result.get("data", {}) or {}
            except Exception as router_err:
                logger.warning("router_model (%s) intent failed, fallback to main model: %s",
                               router_model, router_err)
                result = await self._llm.chat_messages(full_messages, json_mode=True)
                data = result.get("data", {}) or {}

            # flash 可能把输出放进 reasoning_content 返回空白 content → data 为空 dict
            # 注意：sop_id=null 是合法路由结果（无 SOP 匹配），不能误判为空白触发 fallback
            if not data:
                logger.warning("router_model returned empty data, fallback to main model")
                result = await self._llm.chat_messages(full_messages, json_mode=True)
                data = result.get("data", {}) or {}

            logger.debug("SessionAgent intent for '%s': sop=%s conf=%s compound=%s",
                         content[:40], data.get("sop_id"), data.get("confidence"),
                         data.get("is_compound"))
            return data
        except Exception as e:
            logger.warning("SessionAgent intent recognition failed: %s", e)
            return {"sop_id": None, "confidence": "none", "reasoning": f"LLM调用失败: {e}",
                    "is_compound": False, "sub_tasks": [], "fallback": True}
        finally:
            LLMInteractionLogger.clear_context()

    async def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
        """Phase B: 基于 LLM 意图识别的 WorkItem 拆分。"""
        content = message.content or ""
        intent = await self._recognize_intent(message)
        # ── 归档：意图识别段（BUS 之前，含 Prompt 注入信息）──
        await self._append_archive_intent(intent)

        # 当前操作者 user_id（群聊多用户权限越界修复）
        actor_uid = getattr(self, "_current_actor", {}).get("user_id") or self.context.user_id

        sop_id = intent.get("sop_id")
        is_compound = intent.get("is_compound", False)
        sub_tasks = intent.get("sub_tasks") or []
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
                    user_id=actor_uid,
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
                    user_id=actor_uid,
                    sop_id=None,
                    intent_type="fallback",
                    priority=1,
                )]

        if fallback or not sop_id:
            wi = WorkItem(
                session_id=self.conversation_id,
                user_input=content,
                user_id=actor_uid,
                sop_id=None,
                intent_type="fallback",
                priority=1,
            )
            wi.output_spec = self._derive_output_spec(intent, None)  # M1
            return [wi]

        if is_compound and sub_tasks:
            items = []
            for i, st in enumerate(sub_tasks[:5]):
                wi = WorkItem(
                    session_id=self.conversation_id,
                    user_input=st.get("user_input", content) if isinstance(st, dict) else content,
                    user_id=actor_uid,
                    sop_id=st.get("sop_id", sop_id) if isinstance(st, dict) else sop_id,
                    intent_type="sop",
                    priority=st.get("priority", 1) if isinstance(st, dict) else 1,
                )
                wi.output_spec = self._derive_output_spec(st if isinstance(st, dict) else intent,
                                                          wi.sop_id)  # M1
                items.append(wi)
            return items

        wi = WorkItem(
            session_id=self.conversation_id,
            user_input=content,
            user_id=actor_uid,
            sop_id=sop_id,
            intent_type="sop",
            priority=1,
        )
        wi.output_spec = self._derive_output_spec(intent, sop_id)  # M1
        # M2: 路由派生的 query_type 预填给 SkillExecutor，省掉 step-01 的 LLM 参数提取
        query_type = intent.get("query_type")
        if sop_id == "SOP-005-QRY" and query_type:
            setattr(wi, "_prefilled_params", {"query_type": query_type})
            logger.debug("Session[%s] prefilled query_type=%s for SOP-005-QRY",
                         self.conversation_id, query_type)
        return [wi]

    def _derive_output_spec(self, intent: dict, sop_id: str | None) -> dict:
        """M1: 从路由 LLM 输出解析 output_spec，代码补全 max_length/data_fields。"""
        spec = dict(intent.get("output_spec") or {})
        # LLM 判的 4 个语义字段，缺字段兜底
        spec.setdefault("intent", sop_id or "fallback")
        spec.setdefault("detail", "standard")
        spec.setdefault("format", "natural")
        spec.setdefault("cite_source", False)
        # 代码兜底：max_length 按 detail 映射
        detail_to_len = {"brief": 150, "standard": 300, "detailed": 500}
        spec["max_length"] = detail_to_len.get(spec["detail"], 300)
        # 代码兜底：data_fields 按 sop_id + query_type 映射
        spec["data_fields"] = SessionAgent._map_data_fields(sop_id, intent.get("query_type"))
        return spec

    @staticmethod
    def _map_data_fields(sop_id: str | None, query_type: str | None) -> list[str]:
        """M1: 按 SOP + query_type 映射要回传的数据字段（结构化、可枚举）。"""
        if sop_id == "SOP-005-QRY":
            return {
                "event": ["events"], "task": ["tasks"], "meeting": ["meetings"],
                "file": ["files"], "summary": ["events", "tasks", "node_progress"],
                "project": ["project_info"], "user": ["user_info"],
            }.get(query_type or "", ["events"])
        if sop_id and sop_id.startswith("SOP-002"):
            return ["event_no", "status"]
        # 默认：让 WorkItem 自行决定
        return []

    # ── M4: Session 回复合成层 ──

    def _has_meta_cognition_intent(self, done_workitems: list) -> bool:
        """检测是否存在 meta_cognition 意图的 WorkItem。"""
        for wi in done_workitems:
            sr = getattr(wi, "structured_result", None)
            if sr is not None and getattr(sr, "intent", "") == "meta_cognition":
                return True
        return False

    async def _synthesize_final_reply(self, message: "StandardMessage",
                                      done_workitems: list) -> str:
        """M4: 基于 WorkItem 的 structured_result 调 LLM 组织最终回复。

        单 WI / 多 WI 统一走本方法。review_reply 审核在合成后做（M4）。
        LLM 不可用时回退到规则拼串兜底（保留 fail-open）。
        """
        if not done_workitems:
            return "Emily 已处理完毕。"

        # 渲染 wi_results 文本
        wi_results_text = self._render_wi_results(done_workitems)

        # 加载 + format prompt
        from ..infrastructure.llm.prompt_loader import load_prompt
        prompt_template = load_prompt("session_reply")
        prompt_vars = self.context.get_prompt_variables(getattr(self, "_current_actor", None))
        system_prompt = prompt_template.replace("{wi_results}", wi_results_text)
        system_prompt = system_prompt.replace("{user_input}", (message.content or "")[:500])
        system_prompt = system_prompt.replace("{current_datetime}", _beijing_now_str())
        for key, value in prompt_vars.items():
            replacement = str(value) if value else "（无）"
            system_prompt = system_prompt.replace(key, replacement)
        # M4: 元认知意图 ← 注入 SOP 能力树到回复合成上下文
        has_meta = self._has_meta_cognition_intent(done_workitems)
        if has_meta and self._skill_registry:
            try:
                sop_catalog = self._skill_registry.dump_as_text()
                system_prompt = system_prompt.replace("{sop_catalog}", sop_catalog)
            except Exception as e:
                logger.warning("M4: failed to dump SOP catalog for meta_cognition: %s", e)
                system_prompt = system_prompt.replace("{sop_catalog}", "（SOP 能力目录暂不可用）")
        else:
            system_prompt = system_prompt.replace("{sop_catalog}", "")
        system_prompt = re.sub(r'\{[a-z_]+\}', '', system_prompt)

        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.context.message_history)
        full_messages.append({"role": "user", "content": message.content or ""})

        # LLM 合成 —— 用主模型（reasoner）合成最终回复
        # router_model（v4-flash）在长 prompt 下可能把内容放进 reasoning_content
        # 而返回空白 content，导致 JSON 解析失败；主模型（v4-pro）更可靠
        if self._llm:
            try:
                result = await self._llm.chat_messages(full_messages, json_mode=True)
                data = result.get("data", {})
                reply = data.get("reply", "") if isinstance(data, dict) else ""
                if reply and len(reply) > 10:
                    # M4: review_reply 上移——审核合适性
                    await self._review_final_reply(reply, done_workitems)
                    return reply
                logger.warning("M4: Session 合成 reply 不可用 %r，回退拼串", reply[:80])
            except Exception as e:
                logger.warning("M4: Session 合成失败 %s，回退拼串", e)

        # 兜底拼串（fail-open）
        return self._fallback_join_results(done_workitems)

    def _render_wi_results(self, done_workitems: list) -> str:
        """M4: 把多个 WI 的 structured_result 渲染成 prompt 文本。"""
        parts = []
        for idx, wi in enumerate(done_workitems, 1):
            sr = getattr(wi, "structured_result", None)
            if sr is None:
                parts.append(f"### 任务 {idx}\n（无结构化结果）")
                continue
            parts.append(
                f"### 任务 {idx}\n"
                f"- intent: {sr.intent}\n"
                f"- status: {sr.status}\n"
                f"- risk_level: {sr.risk_level}\n"
                f"- data: {json.dumps(sr.data, ensure_ascii=False, default=str)}\n"
                f"- summary_facts: {json.dumps(sr.summary_facts, ensure_ascii=False)}\n"
                f"- rag_sources: {json.dumps(sr.rag_sources, ensure_ascii=False)}\n"
                f"- business_object_no: {sr.business_object_no}\n"
                f"- issues: {json.dumps(sr.issues, ensure_ascii=False)}\n"
                f"- needs_confirm: {sr.needs_confirm}\n"
                f"- error_category: {sr.error_category}\n"
                f"- suggested_followup: {sr.suggested_followup}\n"
            )
        return "\n".join(parts)

    async def _review_final_reply(self, reply: str, done_workitems: list) -> None:
        """M4: review_reply 上移——审回复合适性，只标记不拦截（沿用 RealGuardian 语义）。"""
        if not done_workitems:
            return
        from ..workitem.pipeline.real_guardian import RealGuardian
        guardian = RealGuardian(llm_client=self._llm, config=None)
        for wi in done_workitems:
            try:
                note = await guardian.review_reply(reply, wi)
                if note and note.issues:
                    logger.info("M4 review_reply issues: %s", note.issues)
            except Exception as e:
                logger.debug("M4 review_reply failed (silent skip): %s", e)

    def _fallback_join_results(self, done_workitems: list) -> str:
        """M4: LLM 不可用时的兜底拼串（fail-open）。"""
        parts = []
        for wi in done_workitems:
            sr = getattr(wi, "structured_result", None)
            if sr is None:
                continue
            if sr.status == "success":
                facts = "；".join(sr.summary_facts[:3])
                parts.append(facts or "操作完成")
            elif sr.status == "failed":
                parts.append(f"处理失败：{sr.issues[0] if sr.issues else '未知原因'}")
            else:
                parts.append("；".join(sr.summary_facts[:3]) or "部分完成")
        return "\n\n".join(parts) if parts else "Emily 已处理完毕。"

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
            # 优先复用 Core 注入的 journal；兜底仍临时构造（保持 fail-open）
            journal = self._journal
            if journal is None:
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

        # 当前操作者 user_id
        actor_uid = getattr(self, "_current_actor", {}).get("user_id") or self.context.user_id

        needs_confirm = [
            wi for wi in done_workitems
            if wi.state == WorkItemState.WAITING_CONFIRM
        ]
        for wi in needs_confirm:
            self.confirm_queue.add(
                workitem_id=wi.id,
                prompt=f"关于「{wi.user_input[:50]}...」需要你的确认",
                priority=wi.priority,
                user_id=actor_uid,  # 谁发起谁确认
            )

        if not self.confirm_queue.is_empty:
            # 只取出当前操作者的待确认项
            entry = self.confirm_queue.pop_for_user(actor_uid)
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

            # 归档时传入 md_file_path
            await self.context.persist_and_consolidate(
                llm_client=self._llm,
                md_file_path=self._archive_md_path,
                archive_writer=self._archive_writer,
            )

            logger.info("Session[%s] archived successfully", self.conversation_id)
            # ── 进化日志：Session 归档 ──
            _log_session_lifecycle(self.conversation_id, self.context.user_id, "archived")
        except Exception as e:
            logger.warning("Session[%s] archive warning: %s", self.conversation_id, e)
        finally:
            self.state = SessionState.CLOSED

    # ── 实时归档逐段追加（V2：turn_start / intent / [ArchiveHook 各节点] / turn_end）──

    def _append_archive_turn_start(self, message: "StandardMessage") -> None:
        """归档：写入轮次标题 + 用户消息段（BUS 之前）。fail-open。"""
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            self._turn_counter += 1
            content = self._archive_writer.render_turn_start(
                turn_idx=self._turn_counter,
                user_message=(message.content or "")[:2000],
            )
            self._archive_writer.append_section(self._archive_md_path, content)
        except Exception as e:
            logger.warning("SessionArchive turn_start failed: %s", e)

    async def _append_archive_intent(self, intent: dict) -> None:
        """归档：写入意图识别段（BUS 之前，含 Prompt 注入信息）。fail-open。"""
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            import asyncio
            from ..repositories.evolution_llm_interaction_repo import EvolutionLLMInteractionRepo
            intent_run_id = f"intent-{self.conversation_id[:8]}"
            llm_logs = await asyncio.to_thread(
                EvolutionLLMInteractionRepo.list_by_pipeline_run_ids, [intent_run_id]
            )
            prompt_info = getattr(self, "_last_intent_prompt_info", None)
            content = self._archive_writer.render_intent_section(intent, llm_logs, prompt_info)
            self._archive_writer.append_section(self._archive_md_path, content)
        except Exception as e:
            logger.warning("SessionArchive intent section failed: %s", e)

    def _append_archive_turn_end(self, reply) -> None:
        """归档：写入系统审核标记（如有）+ Emily 回复段 + 分隔线（BUS 之后）。fail-open。"""
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            from ..services.session_archive_writer import SessionArchiveWriter
            reply_content = (reply.content or "")[:2000]
            reply_body, guardian_warnings = SessionArchiveWriter._split_reply_and_warnings(reply_content)
            content = self._archive_writer.render_turn_end(reply_body, guardian_warnings)
            self._archive_writer.append_section(self._archive_md_path, content)
        except Exception as e:
            logger.warning("SessionArchive turn_end failed: %s", e)

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
            return ("我是艾米（Emily），Emily 系统中负责与你对话的服务 AI。"
                    "可以帮你记录事件、管理任务、归档会议、查询项目数据，随时吩咐！")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 进化日志辅助函数（模块级，避免循环 import）
# ══════════════════════════════════════════════════════════════════════════════

def _log_session_lifecycle(conversation_id: str, user_id: str, event_type: str) -> None:
    """非阻断写入 Session 生命周期日志。"""
    try:
        from ..infrastructure.logging.session_lifecycle_logger import SessionLifecycleLogger
        asyncio.ensure_future(SessionLifecycleLogger.log(
            conversation_id=conversation_id,
            user_id=user_id or "",
            event_type=event_type,
            message_count=0,
            duration_ms=0,
        ))
    except Exception as e:
        logger.debug("_log_session_lifecycle failed: %s", e, exc_info=True)


def _detect_feedback(user_message: str, assistant_reply: str,
                     conversation_id: str, user_id: str) -> None:
    """非阻断检测反馈信号并写入。"""
    try:
        from ..infrastructure.logging.feedback_detector import FeedbackSignalDetector
        signals = FeedbackSignalDetector.detect(
            user_message,
            conversation_id=conversation_id,
            user_id=user_id or "",
            assistant_reply=assistant_reply,
        )
        if signals:
            asyncio.ensure_future(FeedbackSignalDetector.log_signals(
                signals,
                pipeline_run_id="",
                conversation_id=conversation_id,
                user_id=user_id or "",
            ))
    except Exception as e:
        logger.debug("_detect_feedback failed: %s", e, exc_info=True)
