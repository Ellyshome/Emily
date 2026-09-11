"""SessionContext —— Session 操作台（聚合根）。

重构要点：
  - PermissionSnapshot 移除，所有字段扁平化为 SessionContext 直接字段
  - 新增 user_position / project_name / project_type / project_status / long_term_memory / 
    conversation_summary / created_at / available_skills
  - 删除僵尸字段：permissions / user_preferences / tool_catalog_summary / schema_summary / 
    system_prompt / perm_list / extra
  - history_summary 改为 @property（合并 long_term_memory + conversation_summary）
  - 新增操作台方法：create / record_turn / build_llm_messages / get_prompt_variables /
    persist_and_consolidate / compress_overflow / refresh / skill 方法
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger("emily.session_context")


# ══════════════════════════════════════════════════════════════════════════════
# 上下文预算设置（参照 Pi DEFAULT_COMPACTION_SETTINGS）
# ══════════════════════════════════════════════════════════════════════════════

# 触发压缩的保留额：`已用 > 窗口 - reserveTokens` 时压缩。
# reserveTokens 同时充当摘要调用可用的输出预算来源。
DEFAULT_RESERVE_TOKENS = 16384
# 保尾 token 预算：压缩时从最新往回累计保留，而非固定条数。
DEFAULT_KEEP_RECENT_TOKENS = 20000
# 输出上限动态压低时至少保留的余量（window - used - 余量）。
MIN_OUTPUT_RESERVE_TOKENS = 4096
# 无真实 usage 时的字符→token 估算比（与 Pi estimate.ts 的 chars/4 一致）。
CHARS_PER_TOKEN = 4
# 摘要条目在 message_history 中的 role（独立于 user/assistant，避免被当作发言）
SUMMARY_ROLE = "context_summary"


@dataclass
class SessionContext:
    """Session 操作台（聚合根）—— 统一承载数据、消息记录、LLM 拼装、归档持久化。"""

    # ── 标识字段（🔒 冻结）──
    conversation_id: str = ""
    user_id: str = ""
    user_name: str = ""

    # ── 用户属性 ──
    user_position: str = ""           # 🔒 冻结
    created_at: str = ""              # 🔒 冻结

    # ── 项目上下文（🔄 可热更新-谨慎）──
    project_name: str = ""
    project_type: str = ""
    project_status: str = ""

    # ── 记忆字段（📝 运行时自维护）──
    long_term_memory: str = ""
    conversation_summary: str = ""

    # ── 权限字段（🔥 可热更新）──
    level: int = 1
    is_management_unit: bool = False
    company_id: str = ""
    company_type: str = ""
    company_name: str = ""
    project_ids: list[str] = field(default_factory=list)
    partner_ids: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    sop_allow: list[str] = field(default_factory=list)
    db_perms: dict[str, str] = field(default_factory=dict)
    info_level: str = "public"
    supervisor_id: str = ""
    granted_codes: list[str] = field(default_factory=list)
    denied_codes: list[str] = field(default_factory=list)
    authorized_node_ids: list[str] = field(default_factory=list)
    permission_version: int = 0
    permissions_loaded_at: str = ""

    # ── 多轮对话记忆（📝 运行时自维护）──
    message_history: list[dict] = field(default_factory=list)

    # ── 上下文用量感知（📝 运行时自维护；不落库）──
    last_usage: dict = field(default_factory=dict)
    # 最近一次 assistant 调用的真实 usage：{"prompt_tokens": int, ...}
    # prompt_tokens 即"上下文净输入量"，是窗口预算判据的锚点。
    context_summary: str = ""
    # 压缩后的结构化交接摘要（独立 role=context_summary，非普通 user 发言）
    carried_facts: dict = field(default_factory=dict)
    # 跨压缩累积的关键成果：{"files": [...], "workitems": [...], "notes": [...]}

    # ── SOP 目录摘要 ──
    sop_catalog_summary: str = ""

    # ── 全景节点参考模板摘要 ──
    node_template_catalog: str = ""

    # ── 当前日期时间 ──
    current_datetime: str = ""

    # ── Skill 预留（最简：可用技能列表）──
    available_skills: list[str] = field(default_factory=list)

    # ── 原子化能力字段（🔥 可热更新）──
    available_tools: list[dict] = field(default_factory=list)
    # [{"api_id": "search_files", "display_name": "根据自然语言描述搜索可见文件"}, ...]

    visible_schema_summary: str = ""
    visible_files_count: int = 0
    visible_files_summary: str = ""

    rag_available: bool = False
    rag_collections: list[str] = field(default_factory=list)

    # ── 元认知模块字段（🔥 可热更新）──
    project_world_book: str = ""        # 项目世界书纯文本摘要（注入 prompt）
    rule_book: str = ""                 # 规则书全文（注入 prompt）
    system_description: str = ""        # 认知书文本（注入 prompt）

    # ── 三书裁剪源数据（M1：供按操作者权限实时裁剪，避免每条消息 IO）──
    world_books_json: str = ""             # 世界书 content_json 原文（JSON 数组，多项目合并）
    system_description_json: str = ""      # 认知书 content_json 原文
    rule_book_sections: list[dict] = field(default_factory=list)   # 规则书解析后章节

    # ══════════════════════════════════════════════════════════════════════════
    #  计算属性
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def history_summary(self) -> str:
        """合并 long_term_memory + conversation_summary。"""
        parts = [p for p in (self.long_term_memory, self.conversation_summary) if p]
        return "\n".join(parts)

    # ══════════════════════════════════════════════════════════════════════════
    #  上下文感知 / 预算（P0：token 度量 + 窗口预算判据）
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def estimate_tokens_for_text(text: str) -> int:
        """字符数 → token 估算（无真实 usage 时的补尾手段）。"""
        if not text:
            return 0
        return max(1, len(text) // CHARS_PER_TOKEN)

    def _estimate_message_tokens(self, messages: list[dict] | None = None) -> int:
        """估算 messages 的 token 总量（不含 system / tools）。"""
        msgs = self.message_history if messages is None else messages
        total = 0
        for m in msgs or []:
            content = m.get("content", "")
            total += self.estimate_tokens_for_text(content if isinstance(content, str) else str(content))
            total += 4  # 每条消息的角色/分隔开销
        return total

    def estimate_context_tokens(self, system_prompt: str = "",
                                tools: list[dict] | None = None) -> int:
        """估算当前上下文总 token：真实 usage 优先，其后消息按 chars/4 补尾。

        与 Pi estimate.ts 同构：以最后一次有效 assistant 的真实 prompt_tokens
        为锚点，补上 system prompt 与 tools 定义。
        """
        base = self._estimate_message_tokens()
        total = base + self.estimate_tokens_for_text(system_prompt)
        if tools:
            try:
                import json as _json
                total += self.estimate_tokens_for_text(
                    _json.dumps(tools, ensure_ascii=False))
            except Exception:
                total += len(tools) * 40
        return total

    def used_tokens(self, system_prompt: str = "",
                    tools: list[dict] | None = None) -> int:
        """当前上下文占用量。

        真实 usage 锚点存在时，以最后一次调用的 prompt_tokens 为准（它已包含
        当时的 system + tools + history）；仅在无锚点时退回估算。
        """
        anchor = int((self.last_usage or {}).get("prompt_tokens") or 0)
        if anchor > 0:
            return anchor
        return self.estimate_context_tokens(system_prompt=system_prompt, tools=tools)

    def record_usage(self, usage: dict | None) -> None:
        """记录最近一次 LLM 调用的真实 usage（供窗口预算判据使用）。"""
        if not usage:
            return
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        if prompt_tokens > 0:
            self.last_usage = dict(usage)

    @staticmethod
    def context_window(model: str | None = None, window_override: int = 0) -> int:
        """查询模型上下文窗口（未知模型回退保守默认值）。"""
        from ..infrastructure.llm.model_registry import get_context_window
        return get_context_window(model, window_override=window_override)

    def should_compact(self, model: str | None = None, *,
                       system_prompt: str = "",
                       tools: list[dict] | None = None,
                       reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
                       window_override: int = 0) -> bool:
        """窗口预算判据：`已用 > 窗口 - reserveTokens` 时触发压缩。"""
        window = self.context_window(model, window_override)
        used = self.used_tokens(system_prompt=system_prompt, tools=tools)
        return used > (window - reserve_tokens)

    def context_usage_report(self, model: str | None = None, *,
                             system_prompt: str = "",
                             tools: list[dict] | None = None,
                             window_override: int = 0) -> dict:
        """上下文用量报告（供日志/监控）。"""
        window = self.context_window(model, window_override)
        used = self.used_tokens(system_prompt=system_prompt, tools=tools)
        return {
            "context_tokens": used,
            "context_window": window,
            "percent": round(used / window * 100, 1) if window else 0.0,
            "anchored": int((self.last_usage or {}).get("prompt_tokens") or 0) > 0,
        }

    def cap_max_tokens(self, configured: int, model: str | None = None, *,
                       system_prompt: str = "",
                       tools: list[dict] | None = None,
                       window_override: int = 0,
                       min_reserve: int = MIN_OUTPUT_RESERVE_TOKENS) -> int:
        """动态压低输出上限：min(configured, 窗口 - 已用 - 余量)。

        长上下文时自动收缩输出，避免"输入挤爆输出"导致请求被拒。
        """
        window = self.context_window(model, window_override)
        used = self.used_tokens(system_prompt=system_prompt, tools=tools)
        available = window - used - min_reserve
        if available <= 0:
            # 已用已逼近窗口：给一个最小可用输出，交由 should_compact 先压缩
            return max(256, configured // 4)
        return max(256, min(configured, available))

    # ══════════════════════════════════════════════════════════════════════════
    #  工厂方法
    # ══════════════════════════════════════════════════════════════════════════

    @classmethod
    def create(cls, user_id: str, conversation_id: str,
               sender_name: str, core) -> "SessionContext":
        """一次性全量灌注创建。

        流程：
        1. 构造基础 SessionContext（标识 + 时间）
        2. 调 SessionDataFetcher.fetch() 获取数据
        3. 从 snapshot dict 灌注所有字段
        4. 从 runtime dict 灌注 recent_turns → message_history
        5. SOP 目录摘要
        6. available_skills 初始化自 sop_allow
        """
        from .session_data_fetcher import SessionDataFetcher

        now = datetime.now(timezone.utc).isoformat()
        ctx = cls(
            conversation_id=conversation_id,
            user_id=user_id,
            user_name=sender_name,
            current_datetime=now,
            created_at=now,
        )

        # 采集数据
        data = SessionDataFetcher.fetch(user_id, conversation_id, core=core)
        snapshot = data.get("session_snapshot", {})
        runtime = data.get("session_runtime", {})
        errors = data.get("errors", [])

        # 灌注用户属性
        # 优先用 DB 解析的显示名（IM 绑定 display_name → username），回退构造时的 sender_name。
        # 由 SessionDataFetcher._resolve_user_name() 解析；sentinel 时计入 errors，此时保留 sender_name。
        resolved_name = snapshot.get("user_name", "")
        if resolved_name and "user_name" not in errors:
            ctx.user_name = resolved_name
        ctx.user_position = snapshot.get("user_position", "")

        # 灌注项目上下文
        ctx.project_name = snapshot.get("project_name", "")
        ctx.project_type = snapshot.get("project_type", "")
        ctx.project_status = snapshot.get("project_status", "")

        # 灌注记忆（仅长期记忆全量载入；往期对话历史由 Agent 通过 chat_archive 工具按需检索）
        ctx.long_term_memory = snapshot.get("long_term_memory", "")

        # 灌注权限（扁平化 snapshot，直接从顶层取值）
        ctx.level = snapshot.get("level", 1)
        ctx.is_management_unit = snapshot.get("is_management_unit", False)
        ctx.company_id = snapshot.get("company_id", "")
        ctx.company_type = snapshot.get("company_type", "")
        ctx.company_name = snapshot.get("company_name", "")
        ctx.project_ids = list(snapshot.get("project_ids", []))
        ctx.partner_ids = list(snapshot.get("partner_ids", []))
        ctx.scopes = list(snapshot.get("scopes", []))
        ctx.sop_allow = list(snapshot.get("sop_allow", []))
        ctx.db_perms = dict(snapshot.get("db_perms", {}))
        ctx.info_level = snapshot.get("info_level", "public")
        ctx.supervisor_id = snapshot.get("supervisor_id", "")
        ctx.granted_codes = list(snapshot.get("granted_codes", []))
        ctx.denied_codes = list(snapshot.get("denied_codes", []))
        ctx.authorized_node_ids = list(snapshot.get("authorized_node_ids", []))
        ctx.permission_version = snapshot.get("permission_version", 0)
        ctx.permissions_loaded_at = snapshot.get("permissions_loaded_at", "")
        # available_skills: 优先取 SkillRegistry，回退到 sop_allow
        if core is not None:
            skill_registry = getattr(core, "_skill_registry", None)
            if skill_registry is not None:
                try:
                    skill_ids = skill_registry.list_sop_ids()
                    if skill_ids:
                        ctx.available_skills = list(skill_ids)
                except Exception:
                    ctx.available_skills = list(ctx.sop_allow)
            else:
                ctx.available_skills = list(ctx.sop_allow)
        else:
            ctx.available_skills = list(ctx.sop_allow)

        # 灌注原子化能力字段
        ctx.available_tools = list(snapshot.get("available_tools", []))
        ctx.visible_schema_summary = snapshot.get("visible_schema_summary", "")
        ctx.visible_files_count = snapshot.get("visible_files_count", 0)
        ctx.visible_files_summary = snapshot.get("visible_files_summary", "")
        ctx.rag_available = snapshot.get("rag_available", False)
        ctx.rag_collections = list(snapshot.get("rag_collections", []))

        # 灌注元认知字段
        ctx.project_world_book = snapshot.get("project_world_book", "")
        ctx.rule_book = snapshot.get("rule_book", "")
        ctx.system_description = snapshot.get("system_description", "")

        # 三书裁剪源数据（M1）
        ctx.world_books_json = snapshot.get("world_books_json", "")
        ctx.system_description_json = snapshot.get("system_description_json", "")
        ctx.rule_book_sections = list(snapshot.get("rule_book_sections", []))

        # （已关闭）最近对话不再在 Session 拉起时注入 message_history
        # message_history 仅在本 Session 生命周期内累积（record_turn），
        # Agent 需要跨 Session 历史时通过 chat_archive 工具按需检索

        # SOP 目录摘要（从 SkillRegistry 获取）
        if core is not None:
            skill_registry = getattr(core, "_skill_registry", None)
            if skill_registry is not None:
                try:
                    skill_ids = skill_registry.list_sop_ids()
                    if skill_ids:
                        ctx.sop_catalog_summary = (
                            f"可用业务流程 ({len(skill_ids)}): {', '.join(skill_ids[:15])}"
                        )
                except Exception as e:
                    logger.warning("sop_catalog_summary failed: %s", e, exc_info=True)

        # 全景节点模板摘要（从 index.yaml 读取）
        try:
            from pathlib import Path
            from ..infrastructure.paths import resolve_data_path
            _tmpl_dir = resolve_data_path("node_templates", "/app/data/node_templates", "emily-data/node_templates")
            _idx_path = Path(_tmpl_dir) / "index.yaml"
            if _idx_path.exists():
                import yaml
                with open(_idx_path, "r", encoding="utf-8") as _f:
                    _idx = yaml.safe_load(_f) or {}
                _templates = _idx.get("templates", [])
                if _templates:
                    _names = [t.get("node_name", "?") for t in _templates]
                    ctx.node_template_catalog = (
                        f"({len(_templates)} 个模板): {', '.join(_names)}"
                    )
        except Exception as e:
            logger.debug("node_template_catalog load skipped: %s", e)
        if errors:
            logger.warning("SessionContext.create: %d data fetch errors for user=%s",
                           len(errors), user_id)

        return ctx

    # ══════════════════════════════════════════════════════════════════════════
    #  原子化能力格式化辅助方法
    # ══════════════════════════════════════════════════════════════════════════

    def _format_tools_summary(self) -> str:
        """格式化可用工具列表为 prompt 变量。"""
        if not self.available_tools:
            return "（无可用工具）"
        lines = []
        for t in self.available_tools:
            lines.append(f"  · {t['api_id']}: {t['display_name']}")
        return "\n".join(lines)

    def _format_rag_summary(self) -> str:
        """格式化 RAG 知识库信息为 prompt 变量。"""
        if not self.rag_available:
            return "知识库不可用"
        collections = "、".join(self.rag_collections) if self.rag_collections else "默认知识库"
        return f"知识库可用（{collections}）"

    # ══════════════════════════════════════════════════════════════════════════
    #  权限只读方法
    # ══════════════════════════════════════════════════════════════════════════

    def has_sop_permission(self, sop_id: str) -> bool:
        """检查是否有权限使用指定 SOP。"""
        return sop_id in self.sop_allow or "all" in self.sop_allow

    def has_db_permission(self, table: str, operation: str = "read") -> bool:
        """检查是否有权限访问指定数据库表。"""
        perm = self.db_perms.get(table)
        if perm is None:
            return False
        if operation == "read":
            return perm in ["read", "read_write"]
        if operation == "write":
            return perm == "read_write"
        return False

    def meets_level_requirement(self, required_level: int) -> bool:
        """检查是否满足权限层级要求（6 级树形继承）。"""
        from ..permission.level import can_access
        return can_access(self.level, required_level)


    # ══════════════════════════════════════════════════════════════════════════
    #  操作台方法
    # ══════════════════════════════════════════════════════════════════════════

    def record_turn(self, user_content: str, assistant_content: str,
                    sender_name: str = "") -> None:
        """记录一轮对话到 message_history。

        不再在此做条数阈值判断——压缩由 SessionAgent 依窗口预算判据触发
        （P0：token 预算取代 >40 条拍脑袋阈值）。
        """
        self.message_history.append({
            "role": "user",
            "content": (user_content or "")[:2000],
            "name": sender_name if sender_name else None,
        })
        self.message_history.append({
            "role": "assistant",
            "content": (assistant_content or "")[:2000],
        })

    def build_llm_messages(self, system_prompt_template: str,
                           current_user_msg: str = "",
                           sender_name: str = "",
                           pending_context: str = "",
                           actor_snapshot: dict | None = None) -> list[dict]:
        """统一拼装 LLM messages 列表。

        Args:
            system_prompt_template: 已 format WorkItem 级变量的模板
            current_user_msg: 当前用户消息
            sender_name: 发送者名称
            pending_context: 待确认上下文文本

        Returns:
            OpenAI 格式 messages 列表
        """
        # 两阶段 format：Session 级变量替换
        # 始终替换（空值替换为"（无）"），避免 {xxx} 占位符原样残留到 prompt 里
        prompt_vars = self.get_prompt_variables(actor_snapshot)
        system_prompt = system_prompt_template
        for key, value in prompt_vars.items():
            replacement = str(value) if value else "（无）"
            system_prompt = system_prompt.replace(key, replacement)

        full_messages: list[dict] = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.get_llm_history())

        # pending 上下文注入
        if pending_context:
            full_messages.append({
                "role": "system",
                "content": pending_context,
            })

        # 当前用户消息
        if current_user_msg:
            # M3: 当前时间迁到 user message 末尾，保持 system 前缀稳定
            _now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
            full_messages.append({
                "role": "user",
                "content": f"{current_user_msg}\n\n[当前时间: {_now_str}]",
                "name": sender_name if sender_name else None,
            })

        return full_messages

    def get_prompt_variables(self, actor_snapshot: dict | None = None) -> dict[str, str]:
        """返回 prompt 模板变量映射。

        P1-1: 移除 7 个已从 session.md 删除的占位符映射
        （available_tools/visible_schema/visible_files/node_template_catalog/
          project_world_book/rule_book/system_description）。
        三书字段保留在 dataclass（PermissionSnapshot 加载不变），仅清理 prompt 变量映射。

        M1: 新增 3 个三书摘要映射（project_brief/rule_brief/system_brief）。
        摘要是权限相关变量（节点/等级/表权限），故走阶段2渲染路径：
        SessionAgent._PERM_PROMPT_KEYS 收录这 3 键，base 中保留占位符不替换。

        群聊多用户权限修复（BUG #3）：权限字段优先取 actor_snapshot（当前操作者），
        回退 self（Session 创建者，私聊场景 actor=None）。使 LLM 看到的权限与
        AuthHook 鉴权用的权限一致。身份/项目/记忆字段仍用 self（actor_snapshot
        仅含权限字段，且这些字段 Session 内稳定）。
        """
        from ..permission.level import level_label as _level_label

        a = actor_snapshot or {}
        # 权限字段：优先 actor（当前操作者），回退 self（Session 创建者）
        level = a.get("level")
        if level is None:
            level = self.level
        company_name = a.get("company_name")
        if company_name is None:
            company_name = self.company_name
        company_type = a.get("company_type")
        if company_type is None:
            company_type = self.company_type
        authorized_node_ids = a.get("authorized_node_ids")
        if authorized_node_ids is None:
            authorized_node_ids = self.authorized_node_ids
        db_perms = a.get("db_perms")
        if db_perms is None:
            db_perms = self.db_perms
        is_mgmt = a.get("is_management_unit")
        if is_mgmt is None:
            is_mgmt = self.is_management_unit
        sop_allow = a.get("sop_allow")
        if sop_allow is not None:
            available_skills = list(sop_allow)
        else:
            available_skills = self.available_skills

        return {
            "{project_name}": self.project_name,
            "{project_type}": self.project_type,
            "{project_status}": self.project_status,
            "{user_name}": self.user_name,
            "{user_position}": self.user_position,
            "{user_company}": company_name,
            "{user_company_type}": company_type,
            "{user_level}": _level_label(level),
            "{user_permission_level}": _level_label(level),
            "{current_node_ids}": "、".join(authorized_node_ids),
            "{user_memory}": self.long_term_memory,
            "{sop_catalog}": self.sop_catalog_summary,
            "{available_skills}": ", ".join(available_skills) or "（无）",
            "{recent_turns}": "",
            "{rag_info}": self._format_rag_summary(),
            # M1: 三书常驻摘要（按当前操作者权限实时裁剪，阶段2渲染）
            "{project_brief}": _brief_world(self.world_books_json, authorized_node_ids,
                                            include_events=bool(is_mgmt)),
            "{rule_brief}": _brief_rule(self.rule_book_sections, level),
            "{system_brief}": _brief_system(self.system_description_json,
                                            {"level": level, "db_perms": db_perms}),
        }

    async def persist_and_consolidate(self, llm_client=None, md_file_path: str = "", archive_writer=None) -> None:
        """持久化归档 + 整合 conversation_summary。

        从 SessionAgent._persist_archive() + _consolidate_conversation_summary() 迁入。
        """
        await self._persist_archive(md_file_path=md_file_path, archive_writer=archive_writer)
        if self.user_id and llm_client:
            await self._consolidate_conversation_summary(llm_client)

    async def _persist_archive(self, md_file_path: str = "", archive_writer=None) -> None:
        """将 Session 关键数据持久化到 session_archives 表（薄索引模式）。"""
        try:
            from ..repositories.session_archive_repo import SessionArchiveRepo

            turn_count = len(self.message_history) // 2

            # 薄索引：仅存元数据 + md_file_path
            SessionArchiveRepo.create(
                conversation_id=self.conversation_id,
                user_id=self.user_id or None,
                user_name=self.user_name,
                turn_count=turn_count,
                md_file_path=md_file_path,
                started_at=self.created_at or None,
                archive_reason="expired",
            )

            # 归档时追加 footer 到 md 文件
            if archive_writer is not None and md_file_path:
                try:
                    archive_writer.append_footer(md_file_path, turn_count, "expired")
                except Exception as e:
                    logger.warning("SessionArchive append_footer failed: %s", e)

            logger.info(
                "SessionContext archive persisted: conv=%s turns=%d md=%s",
                self.conversation_id, turn_count, md_file_path or "(none)",
            )
        except Exception as e:
            logger.warning("SessionContext archive persist failed: %s", e)

    async def _consolidate_conversation_summary(self, llm_client) -> None:
        """归档时整合本次对话到 conversation_summary。

        仅处理私聊会话。群聊中多人共用一个 Session，归档会导致其他群成员的消息
        混入本用户的个人摘要，因此群聊会话归档时跳过摘要整合。
        """
        from ..repositories.user_repo import UserRepository
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import Conversation

        user = UserRepository.get_by_id(self.user_id)
        if not user:
            return

        # 守卫：仅私聊会话才生成摘要，群聊跳过（走群级记忆沉淀）
        with get_session() as session:
            conv = session.query(Conversation).filter(
                Conversation.conversation_id == self.conversation_id
            ).first()
        if conv and conv.conversation_type != "private":
            # ── 群聊：走群级长期记忆沉淀 ──
            if getattr(conv, "group_id", None):
                try:
                    from ..services.group_memory_service import GroupMemoryService
                    from ..repositories.group_memory_repo import GroupMemoryRepository
                    existing = GroupMemoryRepository.get_by_group(conv.group_id)
                    group_name = getattr(conv, "title", "") or ""
                    svc = GroupMemoryService(llm_client=llm_client)
                    await svc.consolidate_on_archive(
                        group_id=conv.group_id,
                        group_name=group_name,
                        session_id=self.conversation_id,
                        speaker_user_id=self.user_id,
                        message_history=self.message_history,
                        existing_memory=existing,
                    )
                except Exception as e:
                    logger.warning("group memory consolidate error: %s", e)
            logger.info(
                "Skipping personal summary consolidation for group %s (type=%s, group_id=%s)",
                self.conversation_id, conv.conversation_type, getattr(conv, "group_id", ""),
            )
            return

        existing_summary = user.conversation_summary or ""
        current_conversation = _format_message_history(self.message_history)

        if not current_conversation or current_conversation == "（无历史消息）":
            return

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
            result = await llm_client.chat_messages(compress_messages)
            new_summary = result.get("content", "") or ""
            if new_summary and len(new_summary) > 20:
                UserRepository.update_user(self.user_id, conversation_summary=new_summary)
                logger.info(
                    "SessionContext summary consolidated for user %s (%d→%d chars)",
                    self.user_id, len(existing_summary), len(new_summary),
                )
        except Exception as e:
            logger.warning("SessionContext summary consolidation failed: %s", e)

    async def compress_overflow(self, llm_client, *,
                                keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
                                reserve_tokens: int = DEFAULT_RESERVE_TOKENS) -> bool:
        """裁剪 message_history：按 token 预算保留近期，压缩较早区间为摘要。

        与旧实现的关键差异（参照 Pi compaction）：
          - 保尾按 **token 预算**从最新往回累计，而非固定 20 条；
          - 切点落在 **user 轮边界**，不拆散一问一答；
          - 摘要在 **已有摘要基础上迭代更新**，不反复再摘要（避免信息衰减）；
          - 摘要以独立 role 存放于 `context_summary` 字段，不再伪装成 user 发言；
          - 关键成果累积到 `carried_facts`，跨压缩不丢业务约束。

        Returns:
            bool: 是否实际执行了压缩。
        """
        # 兼容旧数据：把历史上插回 message_history 的摘要迁出为字段
        self._migrate_legacy_summary()

        if len(self.message_history) < 4:
            return False

        cut = self._pick_keep_start(keep_recent_tokens)
        if cut <= 0:
            return False

        batch = self.message_history[:cut]
        tail = self.message_history[cut:]

        if not llm_client:
            # LLM 不可用：丢弃旧区间（fail-open），但保留累积事实
            self.message_history = tail
            logger.warning("compress_overflow (no LLM): dropped %d msgs, kept %d, facts=%d",
                           len(batch), len(tail), len(self.carried_facts.get("files", [])))
            return True

        existing_summary = self.context_summary or ""
        compress_msgs = _build_compress_messages(
            batch, existing_summary, facts=self.carried_facts,
        )
        try:
            result = await llm_client.chat_messages(compress_msgs)
            summary_content = (result.get("content", "") or "").strip()
            # 用摘要调用的（小）usage 覆盖锚点：等价于 Pi "压缩后旧 usage 失效"，
            # 避免刚压缩完就因旧锚点偏大而立刻二次压缩。
            self.record_usage(result.get("usage"))
            if summary_content and len(summary_content) > 20:
                self.context_summary = summary_content
                self._extract_carried_facts(summary_content)
                self.message_history = tail
                logger.info(
                    "compress_overflow: %d msgs → summary (%d chars), history %d, "
                    "facts(files=%d, workitems=%d)",
                    len(batch), len(summary_content), len(tail),
                    len(self.carried_facts.get("files", [])),
                    len(self.carried_facts.get("workitems", [])),
                )
                return True
            logger.warning("compress_overflow: summary too short, dropping %d msgs", len(batch))
            self.message_history = tail
            return True
        except Exception as e:
            # 压缩失败：至少丢弃旧区间止损（fail-open），下次再试
            self.message_history = tail
            logger.warning("compress_overflow failed (msgs dropped, history kept %d): %s",
                           len(tail), e)
            return True

    def _migrate_legacy_summary(self) -> None:
        """把旧版插回 message_history 的摘要条目迁出为 context_summary 字段。"""
        if not self.message_history:
            return
        first = self.message_history[0]
        if first.get("name") == "system" and "[对话历史摘要]" in (first.get("content") or ""):
            text = first["content"].replace("[对话历史摘要]", "").strip()
            if text and not self.context_summary:
                self.context_summary = text
            self.message_history = self.message_history[1:]

    def _pick_keep_start(self, keep_recent_tokens: int) -> int:
        """从最新往回累计 token，返回保留区间的起始下标（落在 user 轮边界）。

        切点只落在 role=="user" 的条目上（或 0），确保不拆散一问一答。
        """
        acc = 0
        cut = len(self.message_history)
        for i in range(len(self.message_history) - 1, -1, -1):
            acc += self.estimate_tokens_for_text(self.message_history[i].get("content", "")) + 4
            if acc > keep_recent_tokens:
                # 从 i 继续向前找到最近的 user 边界作为切点
                j = i
                while j > 0 and self.message_history[j].get("role") != "user":
                    j -= 1
                cut = j if self.message_history[j].get("role") == "user" else 0
                break
        return cut

    def _extract_carried_facts(self, summary: str) -> None:
        """从摘要中提取文件/成果线索，累积到 carried_facts（跨压缩不丢）。"""
        import re as _re
        files = self.carried_facts.setdefault("files", [])
        for m in _re.findall(r"[\w\-./\\]+\.(?:md|json|ya?ml|py|ts|tsx|js|xlsx|xls|docx|pdf|csv|txt)",
                             summary or ""):
            if m not in files:
                files.append(m)
        # 控制累积上限，避免无限增长
        self.carried_facts["files"] = files[-50:]

    def get_llm_history(self) -> list[dict]:
        """返回注入 LLM 的历史消息：摘要（独立 system 消息）+ 保留的历史条目。

        摘要存在 `context_summary` 字段中，不混入 message_history，避免被
        下一轮压缩再次摘要。
        """
        msgs: list[dict] = []
        if self.context_summary:
            facts_note = ""
            files = self.carried_facts.get("files") or []
            if files:
                facts_note = "\n<carried-files>\n" + "\n".join(files[-20:]) + "\n</carried-files>"
            msgs.append({
                "role": "system",
                "content": f"[历史交接摘要]\n{self.context_summary}{facts_note}",
            })
        msgs.extend(self.message_history)
        return msgs

    def refresh(self, data: dict) -> list[str]:
        """从 SessionDataFetcher.fetch() 结果刷新可热更新字段。

        只覆盖 🔥 和 🔄 类字段，不碰 🔒 和 📝 类。

        Returns:
            已更新的字段名列表
        """
        updated: list[str] = []
        snapshot = data.get("session_snapshot", {})

        # 🔥 可热更新字段（扁平化 snapshot，直接从顶层取值）
        _hot_fields = {
            "level": snapshot.get("level"),
            "is_management_unit": snapshot.get("is_management_unit"),
            "company_id": snapshot.get("company_id"),
            "company_type": snapshot.get("company_type"),
            "company_name": snapshot.get("company_name"),
            "project_ids": snapshot.get("project_ids"),
            "partner_ids": snapshot.get("partner_ids"),
            "scopes": snapshot.get("scopes"),
            "sop_allow": snapshot.get("sop_allow"),
            "db_perms": snapshot.get("db_perms"),
            "info_level": snapshot.get("info_level"),
            "supervisor_id": snapshot.get("supervisor_id"),
            "granted_codes": snapshot.get("granted_codes"),
            "denied_codes": snapshot.get("denied_codes"),
            "authorized_node_ids": snapshot.get("authorized_node_ids"),
            "permission_version": snapshot.get("permission_version"),
            "permissions_loaded_at": snapshot.get("permissions_loaded_at"),
            "available_tools": snapshot.get("available_tools"),
            "visible_schema_summary": snapshot.get("visible_schema_summary"),
            "visible_files_count": snapshot.get("visible_files_count"),
            "visible_files_summary": snapshot.get("visible_files_summary"),
            "rag_available": snapshot.get("rag_available"),
            "rag_collections": snapshot.get("rag_collections"),
            "project_world_book": snapshot.get("project_world_book"),
            "rule_book": snapshot.get("rule_book"),
            "system_description": snapshot.get("system_description"),
            "world_books_json": snapshot.get("world_books_json"),
            "system_description_json": snapshot.get("system_description_json"),
            "rule_book_sections": snapshot.get("rule_book_sections"),
        }

        for field, new_val in _hot_fields.items():
            if new_val is not None:
                old_val = getattr(self, field, None)
                if new_val != old_val:
                    setattr(self, field, new_val)
                    updated.append(field)

        # 🔄 可热更新(谨慎) - 项目字段
        _cautious_fields = {
            "project_name": snapshot.get("project_name"),
            "project_type": snapshot.get("project_type"),
            "project_status": snapshot.get("project_status"),
        }
        for field, new_val in _cautious_fields.items():
            if new_val is not None:
                old_val = getattr(self, field, None)
                if new_val != old_val:
                    setattr(self, field, new_val)
                    updated.append(field)

        # 更新 available_skills
        sop_allow = snapshot.get("sop_allow", [])
        if sop_allow:
            old_skills = set(self.available_skills)
            new_skills = set(sop_allow)
            if old_skills != new_skills:
                self.available_skills = list(sop_allow)
                updated.append("available_skills")

        if updated:
            logger.info("SessionContext refreshed: %s", updated)

        return updated

    # ══════════════════════════════════════════════════════════════════════════
    #  Skill 预留方法（最小实现）
    # ══════════════════════════════════════════════════════════════════════════

    def register_skill(self, skill_id: str) -> None:
        """注册一个可用技能。"""
        if skill_id not in self.available_skills:
            self.available_skills.append(skill_id)

    def unregister_skill(self, skill_id: str) -> None:
        """移除一个可用技能。"""
        if skill_id in self.available_skills:
            self.available_skills.remove(skill_id)

    def has_skill(self, skill_id: str) -> bool:
        """检查是否持有指定技能。"""
        return skill_id in self.available_skills


# ══════════════════════════════════════════════════════════════════════════════
# messages 多轮记忆工具函数（模块级）
# ══════════════════════════════════════════════════════════════════════════════

def _format_message_history(message_history: list[dict]) -> str:
    """将 message_history 格式化为可读文本（供日志/调试/压缩使用）。"""
    if not message_history:
        return "（无历史消息）"
    lines = []
    for msg in message_history:
        role = msg.get("role", "?")
        role_label = "用户" if role == "user" else ("Emy" if role == "assistant" else "系统")
        content = (msg.get("content", "") or "")[:100]
        name = msg.get("name", "")
        name_part = f"（{name}）" if name and name != "system" else ""
        lines.append(f"[{role_label}{name_part}] {content}")
    return "\n".join(lines)


def _build_compress_messages(history: list[dict], existing_summary: str,
                             facts: dict | None = None) -> list[dict]:
    """构建压缩用的 messages 列表。

    摘要需保留：人物、事件、决策、任务、时间，以及**业务约束**（节点/权限/
    成果/文件清单）——后者是长会话下最容易丢失的部分。
    """
    if not history:
        return []
    history_text = _format_message_history(history)
    facts = facts or {}
    facts_block = ""
    files = facts.get("files") or []
    if files:
        facts_block = "\n\n## 已累积的关键文件\n" + "\n".join(files[-20:])
    return [
        {"role": "system", "content": (
            "你是一个对话摘要助手。请将「已有摘要」与「近期对话」迭代合并为一份新摘要"
            "（中文，不超过 300 字）。必须保留：人物、事件、决策、任务、时间；"
            "以及业务约束——涉及的项目/节点、权限范围、已完成的成果与产出文件。"
            "不要包含套话，不要遗漏已确认的约束。"
        )},
        {"role": "user", "content": (
            f"## 已有摘要\n{existing_summary or '（无）'}\n\n"
            f"## 近期对话\n{history_text}{facts_block}\n\n"
            f"请输出合并后的完整摘要（不超过 300 字）："
        )},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# M1: 三书摘要裁剪辅助（fail-open：异常返回空串，不阻断 prompt 装配）
# ══════════════════════════════════════════════════════════════════════════════

def _brief_world(books_json: str, authorized_node_ids: list[str],
                 include_events: bool = False) -> str:
    """多项目合并摘要：逐本裁剪渲染后合并。

    无参与项目（空串）→ 返回空串，即不产出任何项目态势内容。
    include_events=True（管理单位）时才输出事件段。
    """
    if not books_json:
        return ""
    try:
        from .fetchers.fetch_world_book import render_brief
        books = json.loads(books_json)
        if not isinstance(books, list):
            books = [{"content_json": books_json}]
        briefs: list[str] = []
        auth = list(authorized_node_ids or [])
        for b in books:
            cj = b.get("content_json") if isinstance(b, dict) else None
            if not cj:
                continue
            text = render_brief(cj, auth, include_events=include_events)
            if text:
                briefs.append(text)
        return "\n\n".join(briefs)
    except Exception as e:
        logger.warning("project_brief render failed: %s", e)
        return ""


def _brief_rule(sections: list[dict], level: int) -> str:
    if not sections:
        return ""
    try:
        from ..services.rule_book_loader import render_brief
        return render_brief(sections, int(level or 1))
    except Exception as e:
        logger.warning("rule_brief render failed: %s", e)
        return ""


def _brief_system(content_json: str, perms: dict) -> str:
    if not content_json:
        return ""
    try:
        from .fetchers.fetch_system_description import render_brief
        return render_brief(content_json, perms)
    except Exception as e:
        logger.warning("system_brief render failed: %s", e)
        return ""


# 模块级兼容别名
format_message_history = _format_message_history
build_compress_messages = _build_compress_messages
