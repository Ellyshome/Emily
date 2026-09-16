"""会话主循环模块（M1）—— 唯一连续对话主体。

定位（计划 M1 / PRD US-01、D6、D7、R2、R4）：
  - Session 持有 `chat_with_tools` 循环：LLM → 工具执行 → tool_result 回灌 → 收敛；
  - 回复由循环**直接产出**，取消"渲染结构化结果 → LLM 二次合成"中间层；
  - 是否调用能力是循环内的隐式判断（能直答则直答），无回合开始的前置分类步骤（D6）；
  - 多能力协作时由 M4 做任务级粗排，循环**照单逐项执行**、成果逐步回灌、按结果重排（D7/R4）；
  - 挂起（缺参）→ M5 登记 + 对话中提问；确认/取消 → M6。

与旧链路的关系（PRD §4.4-4）：本模块为**并行模块**，`session_agent.py` / `session_pool.py`
/ `workitem/**` 零改动；由 EmilyCore 入口按开关分派。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

from ..adapters.standard.reply import ReplyMessage
from ..services.session_archive_writer import CapabilityCallRecord

logger = logging.getLogger("emily.session.loop")

# 顺序/并列语义提示词：命中才尝试任务级粗排（避免每条业务消息都多花一次规划 LLM）
_COMPOUND_HINTS = (
    "然后", "再", "之后", "接着", "随后", "并且", "同时", "顺便", "以及", "并把", "并将",
    "分别", "依次", "先", "然后给", "再给",
)

_DEFAULT_MAX_ITERATIONS = 12


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _strip_unfilled(template: str) -> str:
    return re.sub(r"\{[a-z_]+\}", "", template or "")


def _looks_compound(text: str) -> bool:
    """粗略判断是否需要任务级粗排（零 LLM 成本）。"""
    t = text or ""
    if len(t) < 8:
        return False
    return any(h in t for h in _COMPOUND_HINTS)


class SessionLoop:
    """会话主循环 —— 每会话一个实例。"""

    def __init__(
        self,
        conversation_id: str,
        context,
        llm_client=None,
        catalog=None,
        planner=None,
        dialog=None,
        capability_registry=None,
        capability_runner=None,
        resolvers=None,
        business_tools=None,
        archive_writer=None,
        outbound_bus=None,
        path_router=None,
        config=None,
    ) -> None:
        self.conversation_id = conversation_id
        self.context = context
        self._llm = llm_client
        self._catalog = catalog
        self._planner = planner
        self._dialog = dialog
        self._capability_registry = capability_registry
        self._capability_runner = capability_runner
        self._resolvers = resolvers
        self._business_tools = business_tools
        self._archive_writer = archive_writer
        self._outbound_bus = outbound_bus
        self._path_router = path_router
        self._config = config

        self._archive_md_path = ""
        self._turn_counter = 0          # 文件内轮次号（跨段续接，下方按现有正文校准）
        self._turns_at_start = 0        # 本段会话起始基线，用于算「本段轮数」
        self._segment_started_at = datetime.now(timezone.utc).isoformat()
        self._compacting = False
        self._last_actor: dict | None = None
        self._last_calls: list = []
        self._turn_capability_names: set = set()

        # 归档：写文件头（fail-open）+ 实时建档（索引在会话建立即落库，不等超时归档）
        if self._archive_writer is not None:
            try:
                self._archive_md_path = self._archive_writer.ensure_header(
                    conversation_id=conversation_id,
                    user_name=getattr(context, "user_name", "") or "anonymous",
                    started_at=self._segment_started_at,
                    context=self._header_context(),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("SessionLoop archive header failed: %s", e)
        # 轮次号跨段续接 + 本段基线：同天重启会复用同一 md 文件，若从 1 重开会
        # 在同一文件里出现两个「第 1 轮」；以现有正文轮次为基线续编，并据此算本段轮数。
        if self._archive_writer is not None and self._archive_md_path:
            try:
                self._turns_at_start = self._archive_writer.count_turns(self._archive_md_path)
            except Exception as e:  # noqa: BLE001
                logger.warning("SessionLoop archive count_turns failed: %s", e)
        self._turn_counter = self._turns_at_start
        try:
            context.register_live_index(md_file_path=self._archive_md_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoop live archive index failed: %s", e)

    def _header_context(self) -> dict:
        c = self.context
        return {
            "user_id": getattr(c, "user_id", ""),
            "user_position": getattr(c, "user_position", ""),
            "company_name": getattr(c, "company_name", ""),
            "company_type": getattr(c, "company_type", ""),
            "level": getattr(c, "level", 1),
            "is_management_unit": getattr(c, "is_management_unit", False),
            "scopes": list(getattr(c, "scopes", []) or []),
            "authorized_node_ids": list(getattr(c, "authorized_node_ids", []) or []),
            "project_ids": list(getattr(c, "project_ids", []) or []),
            "sop_allow": list(getattr(c, "sop_allow", []) or []),
            "project_name": getattr(c, "project_name", ""),
            "available_tools": list(getattr(c, "available_tools", []) or []),
            "available_skills": list(getattr(c, "available_skills", []) or []),
            "rag_available": getattr(c, "rag_available", False),
            "visible_files_count": getattr(c, "visible_files_count", 0),
            "prompt_name": "session_loop.md",
            # 通道身份：访客（未登记用户）同样留痕
            "platform": getattr(c, "platform", ""),
            "im_user_id": getattr(c, "im_user_id", ""),
            "is_guest": getattr(c, "is_guest", False),
        }

    # ══════════════════════════════════════════════════════════════════════
    # 入口
    # ══════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════
    # 主流程
    # ══════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════
    # 工具执行
    # ══════════════════════════════════════════════════════════════════════

    async def _execute_tool(self, tool_name: str, arguments: dict, message, db_message_id: str,
                            calls: list, capability_names: set) -> dict:
        # fail-closed：不在当前可见能力集内 → 拒绝
        if not tool_name or tool_name not in capability_names:
            return {"success": False, "reply": "该操作无法执行，您可能没有相应权限。"}

        # ── 控制工具：确认 / 取消（M6）──
        if self._dialog is not None and tool_name in ("confirm_pending", "cancel_pending"):
            actor_uid = (self._last_actor or {}).get("user_id") or self.context.user_id
            event_id, _ = self._dialog.resolve_event(
                self.conversation_id, str(arguments.get("event_no") or ""))
            action = "confirm" if tool_name == "confirm_pending" else "cancel"
            text = await self._dialog.handle(action, event_id, actor_uid)
            calls.append(CapabilityCallRecord(
                capability=tool_name, params_digest=json.dumps(arguments, ensure_ascii=False),
                result_status="success", result_digest=text, triggered_by=actor_uid,
            ))
            return {"success": True, "reply": text}

        # ── SOP 能力（M3）──
        if self._capability_registry is not None and self._capability_registry.has(tool_name):
            request = str(arguments.get("request") or "")
            additional = str(arguments.get("additional_input") or "").strip()
            if additional:
                request = f"{request}\n\n[用户补充] {additional}"
            result = await self._run_capability(tool_name, request, message, db_message_id)
            calls.append(self._record_call(tool_name, request, result))
            return result.to_tool_dict()

        # ── resolver（名称→UUID 解析等）──
        resolver = self._resolvers.get(tool_name) if self._resolvers is not None else None
        if resolver is not None:
            try:
                rresult = await resolver.handle(arguments, self.context)
            except Exception as e:  # noqa: BLE001
                logger.error("resolver %s failed: %s", tool_name, e, exc_info=True)
                rresult = {"found": False, "error": f"resolver 异常: {e}"}
            return rresult if isinstance(rresult, dict) else {"result": rresult}

        # ── 业务工具（查询 / 执行手脚）──
        return await self._execute_business_tool(tool_name, arguments, db_message_id, calls)

    async def _execute_business_tool(self, tool_name: str, arguments: dict,
                                     db_message_id: str, calls: list) -> dict:
        from ..workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy

        tool = self._business_tools.get(tool_name) if self._business_tools is not None else None
        if tool is None:
            return {"success": False, "reply": f"工具 '{tool_name}' 未注册"}

        actor = self._last_actor or {}
        tier = FallbackPolicy.gate(actor).value
        allowed = set(FallbackPolicy.resolve(tier, with_write=True) or [])
        if tool_name not in allowed:
            return {"success": False,
                    "reply": "该操作在当前档位不可用，请走对应标准流程或联系管理员。"}
        ok, gate_err = FallbackPolicy.assert_write_allowed(
            tool_name, tier, getattr(tool, "write_mode", "read"))
        if not ok:
            return {"success": False, "reply": gate_err}

        params = self._inject_runtime_params(arguments, db_message_id)
        try:
            import inspect
            sig = inspect.signature(tool.handler)
            kwargs = {"params": params}
            if "user_id" in sig.parameters:
                kwargs["user_id"] = actor.get("user_id") or self.context.user_id
            if "message_id" in sig.parameters:
                kwargs["message_id"] = db_message_id
            handler_result = await tool.handler(**kwargs)
        except Exception as e:  # noqa: BLE001
            logger.error("business tool %s failed: %s", tool_name, e, exc_info=True)
            handler_result = {"success": False, "reply": f"工具执行异常: {e}"}

        payload = handler_result if isinstance(handler_result, dict) else {}
        calls.append(CapabilityCallRecord(
            capability=tool_name,
            params_digest=json.dumps({k: v for k, v in arguments.items() if not k.startswith("_")},
                                     ensure_ascii=False, default=str),
            result_status="success" if payload.get("success", True) else "failed",
            result_digest=str(payload.get("reply", ""))[:300],
            triggered_by=(actor.get("user_id") or self.context.user_id),
        ))
        return payload

    def _inject_runtime_params(self, arguments: dict, db_message_id: str) -> dict:
        p = dict(arguments or {})
        p["_user_id"] = (self._last_actor or {}).get("user_id") or self.context.user_id
        p["_message_id"] = db_message_id
        p["_conversation_id"] = self.conversation_id
        return p

    # ══════════════════════════════════════════════════════════════════════
    # 能力调用（M3）+ 超时（AC-US-01.4）
    # ══════════════════════════════════════════════════════════════════════

    async def _run_capability(self, name: str, request: str, message, db_message_id: str):
        from .capability_runner import CapabilityResult

        cap = self._capability_registry.get(name) if self._capability_registry is not None else None
        if cap is None or self._capability_runner is None:
            return CapabilityResult.failed("能力未注册")
        timeout = int(getattr(self._config, "capability_call_timeout_seconds", 120) or 120)
        t0 = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._capability_runner.run(
                    cap.sop_id, request, name=cap.name, message=message,
                    db_message_id=db_message_id, actor_snapshot=self._last_actor,
                    session_context=self.context,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # 实际耗时必须回填：否则归档把 120s 的超时渲染成「失败（0ms）」，
            # 日志里只有阈值也答不出"这次到底卡了多久"。
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("SessionLoop[%s] capability %s timed out after %dms (limit %ds)",
                           self.conversation_id, name, elapsed_ms, timeout)
            return CapabilityResult.failed(
                f"该操作耗时超过 {timeout} 秒，已中止。", elapsed_ms=elapsed_ms)

    def _record_call(self, capability: str, request: str, result) -> CapabilityCallRecord:
        actor_uid = (self._last_actor or {}).get("user_id") or self.context.user_id
        return CapabilityCallRecord(
            capability=capability,
            params_digest=json.dumps({"request": request}, ensure_ascii=False)[:300],
            result_status=getattr(result, "status", "failed"),
            result_digest=(getattr(result, "readable_text", "") or "")[:300],
            triggered_by=actor_uid,
            elapsed_ms=int(getattr(result, "elapsed_ms", 0) or 0),
            needs_input=bool(getattr(result, "needs_input", False)),
            issues=list(getattr(result, "issues", []) or [])[:3],
        )

    # ══════════════════════════════════════════════════════════════════════
    # LLM / Prompt
    # ══════════════════════════════════════════════════════════════════════

    async def _llm_call(self, messages: list, tool_specs: list):
        if self._llm is None:
            return None
        model = (getattr(self._llm, "agent_loop_model", None)
                 or getattr(self._llm, "router_model", None) or self._llm.model)
        max_tokens = int(getattr(self._config, "llm_agent_loop_max_tokens", 8192) or 8192)
        cap = getattr(self.context, "cap_max_tokens", None)
        if callable(cap):
            try:
                max_tokens = cap(max_tokens, model, system_prompt="",
                                 tools=tool_specs,
                                 window_override=getattr(self._config, "llm_context_window_override", 0))
            except Exception as e:  # noqa: BLE001
                logger.debug("cap_max_tokens skipped: %s", e)
        try:
            result = await self._llm.chat_messages(
                messages, tools=tool_specs or None, model=model, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            logger.error("SessionLoop LLM call failed: %s", e, exc_info=True)
            return None
        rec = getattr(self.context, "record_usage", None)
        if callable(rec):
            try:
                rec((result or {}).get("usage"))
            except Exception as e:  # noqa: BLE001
                logger.debug("record_usage skipped: %s", e)
        return result

    def _build_system_prompt(self, entries: list) -> str:
        from ..infrastructure.llm.prompt_loader import load_prompt

        template = load_prompt("session_loop") or ""
        caps = "\n".join(
            f"- {e.name}（{e.kind}）: {e.description}" for e in entries if e.name
        ) or "（暂无可用能力）"
        prompt = template.replace("{capability_catalog}", caps)
        try:
            for key, value in (self.context.get_prompt_variables(self._last_actor) or {}).items():
                prompt = prompt.replace(key, str(value) if value else "（无）")
        except Exception as e:  # noqa: BLE001
            logger.debug("prompt variables skipped: %s", e)
        prompt = prompt.replace("{current_datetime}", _beijing_now_str())
        return _strip_unfilled(prompt)

    def _llm_history(self) -> list:
        getter = getattr(self.context, "get_llm_history", None)
        if callable(getter):
            try:
                return list(getter() or [])
            except Exception as e:  # noqa: BLE001
                logger.debug("get_llm_history skipped: %s", e)
        return list(getattr(self.context, "message_history", []) or [])

    # ══════════════════════════════════════════════════════════════════════
    # 上下文：操作者快照 / 群聊注入 / 待确认项
    # ══════════════════════════════════════════════════════════════════════

    async def _fetch_actor(self, current_user_id: str) -> dict:
        actor_uid = current_user_id or self.context.user_id
        try:
            from .session_data_fetcher import SessionDataFetcher
            core = getattr(self, "_core", None)
            snapshot = await asyncio.to_thread(
                SessionDataFetcher.fetch_actor_snapshot, actor_uid, core)
            snapshot = snapshot or {}
            snapshot["user_id"] = actor_uid
            return snapshot
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoop fetch_actor_snapshot failed: %s", e)
            return {"user_id": actor_uid}

    async def _group_injections(self, message, db_message_id: str) -> list:
        """群聊回溯上下文 + 群级长期记忆注入（fail-open，与旧路径对齐）。"""
        extra: list = []
        if getattr(message, "conversation_type", "") != "group" or not getattr(message, "group_id", ""):
            return extra
        if db_message_id and self._llm is not None:
            try:
                from ..services.group_context_service import GroupContextService
                ctx_text = await GroupContextService(llm_client=self._llm).build_group_context(
                    group_id=message.group_id, current_message_id=db_message_id,
                    user_question=message.content or "")
                if ctx_text:
                    extra.append({"role": "system", "content": ctx_text})
            except Exception as e:  # noqa: BLE001
                logger.warning("group context build failed (non-blocking): %s", e)
        try:
            from ..services.group_memory_service import GroupMemoryService
            mem = GroupMemoryService().build_injection(message.group_id)
            if mem:
                extra.append({"role": "system", "content": mem})
        except Exception as e:  # noqa: BLE001
            logger.debug("group memory injection failed: %s", e)
        return extra

    def _pending_event_injection(self):
        if self._dialog is None:
            return None
        return self._dialog.prompt_injection(self._dialog.fetch_pending(self.conversation_id))

    # ══════════════════════════════════════════════════════════════════════
    # 归档 / 进度 / 回复
    # ══════════════════════════════════════════════════════════════════════

    def _append_archive_turn_start(self, message) -> None:
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            content = self._archive_writer.render_turn_start(
                turn_idx=self._turn_counter, user_message=(message.content or "")[:2000])
            self._archive_writer.append_section(self._archive_md_path, content)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive turn_start failed: %s", e)

    def _append_archive_turn_end(self, reply_text: str) -> None:
        if self._archive_writer is None or not self._archive_md_path:
            return
        try:
            from ..services.session_archive_writer import SessionArchiveWriter
            reply_body, warnings = SessionArchiveWriter._split_reply_and_warnings((reply_text or "")[:2000])
            self._archive_writer.append_section(
                self._archive_md_path, self._archive_writer.render_turn_end(reply_body, warnings))
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive turn_end failed: %s", e)

    def append_capability_section(self, calls: list) -> None:
        """把本轮能力调用清单写入归档（AC-US-08.1）。"""
        if self._archive_writer is None or not self._archive_md_path or not calls:
            return
        try:
            section = self._archive_writer.render_capability_section(calls)
            if section:
                self._archive_writer.append_section(self._archive_md_path, section)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive capability section failed: %s", e)

    def _record_turn(self, message, reply_text: str) -> None:
        try:
            self.context.record_turn(
                user_content=(message.content or "")[:2000],
                assistant_content=(reply_text or "")[:2000],
                sender_name=getattr(message, "sender_name", "") or "",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("record_turn failed: %s", e)

    async def touch_archive_index(self) -> None:
        """每轮收口实时刷新归档索引（轮次 + 最后活跃时间）。

        DB I/O 走线程池，不阻塞会话主循环；失败只告警（归档非主流程）。
        """
        try:
            await asyncio.to_thread(self.context.touch_live_index,
                                    self._turn_counter - self._turns_at_start)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionArchive live index touch failed: %s", e)

    def _maybe_compact(self) -> None:
        if self._llm is None or self._compacting:
            return
        should = getattr(self.context, "should_compact", None)
        if not callable(should):
            return
        try:
            if not should(getattr(self._llm, "model", None), system_prompt="",
                          reserve_tokens=getattr(self._config, "llm_compact_reserve_tokens", 16384),
                          window_override=getattr(self._config, "llm_context_window_override", 0)):
                return
        except Exception as e:  # noqa: BLE001
            logger.debug("should_compact check failed: %s", e)
            return
        self._compacting = True

        async def _run():
            try:
                await self.context.compress_overflow(
                    self._llm,
                    keep_recent_tokens=getattr(self._config, "llm_compact_keep_recent_tokens", 20000),
                    reserve_tokens=getattr(self._config, "llm_compact_reserve_tokens", 16384))
            except Exception as e:  # noqa: BLE001
                logger.warning("compress_overflow failed: %s", e)
            finally:
                self._compacting = False

        asyncio.ensure_future(_run())

    def _reply(self, message, content: str) -> ReplyMessage:
        msg_id = message.message_id if getattr(message, "conversation_type", "") == "group" else None
        return ReplyMessage(
            conversation_id=message.conversation_id, content=content,
            reply_to_message_id=msg_id,
        )

    async def archive(self, archive_reason: str = "expired") -> None:
        """会话截断（TTL 超时 / 手动终止）。

        归档索引在会话建立时已实时落库，此处只做截断收口：
        追加 md footer + 标记 status=truncated + 整合个人摘要。
        """
        try:
            await self.context.persist_and_consolidate(
                llm_client=self._llm, md_file_path=self._archive_md_path,
                archive_writer=self._archive_writer, archive_reason=archive_reason,
                segment_turn_count=self._turn_counter - self._turns_at_start,
                segment_started_at=self._segment_started_at,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoop archive warning: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# 会话池（并行模块，与旧 SessionPoolManager 同形）
# ══════════════════════════════════════════════════════════════════════════════

#: 访客身份（退役后新内核为唯一渠道：发送者未解析为系统用户时仍建会话，
#: 权限快照为空并按 fail-closed 收窄，避免消息被静默丢弃）
GUEST_USER_ID = "guest"


class SessionLoopPool:
    """新路径会话池：conversation_id → SessionLoop（TTL 复用 + 会话内串行）。"""

    class _Entry:
        __slots__ = ("loop", "last_active", "lock")

        def __init__(self, loop) -> None:
            import time
            self.loop = loop
            self.last_active = time.time()
            self.lock = asyncio.Lock()

    def __init__(self, config=None, core=None) -> None:
        import time
        self._core = core
        self._config = config
        self._sessions: dict = {}
        self._sweeper_task = None
        self._start_time = time.time()

    def _ttl(self) -> int:
        return int(getattr(self._config, "session_ttl_seconds", 600) or 600)

    def _max_concurrent(self) -> int:
        return int(getattr(self._config, "session_max_concurrent", 100) or 100)

    async def route(self, message, user_id: str = "", db_message_id: str = "") -> ReplyMessage | None:
        """路由一条入站消息（与旧 SessionPoolManager.route 同签名）。"""
        conv_id = message.conversation_id
        await self._ensure_sweeper()
        # 退役后：发送者未解析为系统用户 → 按访客身份建会话（新内核为唯一渠道，不丢消息）
        effective_user = str(user_id or "").strip() or GUEST_USER_ID
        entry = self._sessions.get(conv_id)
        if entry is None:
            if len(self._sessions) >= self._max_concurrent():
                self.sweep_expired()
            loop = self._build_loop(message, effective_user)
            if loop is None:
                logger.error("SessionLoopPool: 会话构建失败，消息未处理（conv=%s）", conv_id)
                return None
            entry = SessionLoopPool._Entry(loop)
            self._sessions[conv_id] = entry
            logger.info("SessionLoopPool created: conv=%s (pool size=%d)", conv_id, len(self._sessions))
        async with entry.lock:
            import time
            entry.last_active = time.time()
            # 退役后：会话编排图为唯一渠道（旧循环实现已删除）
            from .graph_wiring import handle_via_graph
            return await handle_via_graph(
                entry.loop, message, db_message_id=db_message_id,
                current_user_id=effective_user)

    def _build_loop(self, message, user_id: str):
        core = self._core
        if core is None:
            logger.error("SessionLoopPool: core 未注入，无法构建会话主循环")
            return None
        try:
            from .session_context import SessionContext
            from .capability_catalog import build_catalog
            from .capability_plan import SessionPlanner
            from .capability_runner import build_runner
            from .confirm_dialog import ConfirmDialog

            context = SessionContext.create(
                user_id=user_id, conversation_id=message.conversation_id,
                sender_name=getattr(message, "sender_name", "") or "", core=core,
                platform=getattr(message, "platform", "") or "",
                im_user_id=getattr(message, "sender_id", "") or "",
            )
            llm = getattr(core, "_llm_client", None)
            path_router = getattr(core, "_session_path_router", None)
            loop = SessionLoop(
                conversation_id=message.conversation_id,
                context=context,
                llm_client=llm,
                catalog=build_catalog(core),
                planner=SessionPlanner(llm_client=llm, config=getattr(core, "config", None)),
                dialog=ConfirmDialog(journal=getattr(core, "_event_journal", None)),
                capability_registry=getattr(core, "_capability_registry", None),
                capability_runner=build_runner(core),
                resolvers=getattr(core, "_resolvers", None),
                business_tools=getattr(core, "_business_flow_tools", None),
                archive_writer=getattr(core, "_session_archive_writer", None),
                outbound_bus=getattr(core, "outbound_bus", None),
                path_router=path_router,
                config=getattr(core, "config", None),
            )
            loop._core = core
            return loop
        except Exception as e:  # noqa: BLE001
            logger.error("SessionLoopPool build failed: %s", e, exc_info=True)
            return None

    # ── TTL 清理 ──

    async def _ensure_sweeper(self) -> None:
        if self._sweeper_task is not None:
            return
        interval = int(getattr(self._config, "sweep_interval_seconds", 300) or 300)
        self._sweeper_task = asyncio.create_task(self._sweep_loop(interval))

    async def _sweep_loop(self, interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                self.sweep_expired()
            except Exception as e:  # noqa: BLE001
                logger.warning("SessionLoopPool sweeper error: %s", e)

    def sweep_expired(self) -> int:
        """按 TTL 截断并移除过期会话（memory-only 池，无 WorkItem 任务段判定）。

        超时只做「截断」（写 footer + 标记 status=truncated），归档索引早在
        会话建立时已实时落库，故此处不再承担归档触发职责。

        截断同时广播 session_closed：插件据此清掉 conversation_id → IM event
        的映射，避免过期会话的 event 长期残留、被后续（如测试注入的）出站事件
        误发到真实用户会话。
        """
        import time
        now = time.time()
        ttl = self._ttl()
        expired = [cid for cid, e in self._sessions.items() if now - e.last_active > ttl]
        core = self._core
        for cid in expired:
            entry = self._sessions.pop(cid, None)
            if entry is not None:
                try:
                    asyncio.ensure_future(entry.loop.archive(archive_reason="expired"))
                except Exception as e:  # noqa: BLE001
                    logger.warning("SessionLoopPool sweep archive failed for %s: %s", cid, e)
            bus = getattr(core, "outbound_bus", None)
            if bus is not None:
                try:
                    bus.publish("session_closed", {"conversation_id": cid})
                except Exception as e:  # noqa: BLE001
                    logger.warning("SessionLoopPool session_closed publish failed: %s: %s", cid, e)
        if expired:
            logger.info("SessionLoopPool swept %d expired session(s)", len(expired))
        return len(expired)

    async def terminate(self, conversation_id: str, archive_reason: str = "terminated") -> bool:
        """强制终止指定会话：截断归档后从池中移除。

        Args:
            conversation_id: 会话 ID。
            archive_reason: 截断原因（terminated=手动终止）。

        Returns:
            bool: 池中是否存在该会话（存在即已终止）。
        """
        entry = self._sessions.pop(conversation_id, None)
        if entry is None:
            return False
        try:
            await entry.loop.archive(archive_reason=archive_reason)
        except Exception as e:  # noqa: BLE001
            logger.warning("SessionLoopPool terminate archive failed: %s", e)
        logger.info("SessionLoopPool terminated: conv=%s", conversation_id)
        return True

    @property
    def size(self) -> int:
        return len(self._sessions)

    @property
    def uptime_seconds(self) -> int:
        import time
        return int(time.time() - self._start_time)

    def get_status(self) -> dict:
        """返回会话池状态摘要（供观测接口调用，与 SessionPoolManager.get_status 同形）。

        Returns:
            {"total": int, "uptime_seconds": int,
             "sessions": [{"conversation_id", "last_active_ts", "idle_seconds"}, ...]}
        """
        import time
        now = time.time()
        sessions = [
            {
                "conversation_id": cid,
                "last_active_ts": entry.last_active,
                "idle_seconds": int(now - entry.last_active),
            }
            for cid, entry in self._sessions.items()
        ]
        return {
            "total": len(self._sessions),
            "uptime_seconds": self.uptime_seconds,
            "sessions": sessions,
        }
