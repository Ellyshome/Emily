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
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("emily.session_context")


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
    department: list[str] = field(default_factory=list)
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

    # ── SOP 目录摘要 ──
    sop_catalog_summary: str = ""

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
    system_description: str = ""        # 系统自我描述文本（注入 prompt）

    # ══════════════════════════════════════════════════════════════════════════
    #  计算属性
    # ══════════════════════════════════════════════════════════════════════════

    @property
    def history_summary(self) -> str:
        """合并 long_term_memory + conversation_summary。"""
        parts = [p for p in (self.long_term_memory, self.conversation_summary) if p]
        return "\n".join(parts)

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
        ctx.user_position = snapshot.get("user_position", "")

        # 灌注项目上下文
        ctx.project_name = snapshot.get("project_name", "")
        ctx.project_type = snapshot.get("project_type", "")
        ctx.project_status = snapshot.get("project_status", "")

        # 灌注记忆
        ctx.long_term_memory = snapshot.get("long_term_memory", "")
        ctx.conversation_summary = snapshot.get("conversation_summary", "")

        # 灌注权限（扁平化 snapshot，直接从顶层取值）
        ctx.level = snapshot.get("level", 1)
        ctx.is_management_unit = snapshot.get("is_management_unit", False)
        ctx.company_id = snapshot.get("company_id", "")
        ctx.company_type = snapshot.get("company_type", "")
        ctx.company_name = snapshot.get("company_name", "")
        ctx.department = list(snapshot.get("department", []))
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

        # 灌注最近对话 → message_history
        recent_turns = runtime.get("recent_turns", [])
        for turn in recent_turns:
            role = turn.get("role", "user")
            name = turn.get("sender_name", "") if role == "user" else None
            ctx.message_history.append({
                "role": role if role in ("user", "assistant") else "user",
                "content": turn.get("content", "") or "",
                "name": name if name else None,
            })

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
        """记录一轮对话到 message_history 滑动窗口。

        窗口 > 40 条时异步触发压缩（D6：record_turn 内部自动检测）。
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

        if len(self.message_history) > 40:
            logger.debug("SessionContext message_history overflow (%d), triggering compress",
                         len(self.message_history))
            # 需要外部传入 llm_client，此时只记日志
            # compress_overflow 由 SessionAgent 在 record_turn 后显式调用

    def build_llm_messages(self, system_prompt_template: str,
                           current_user_msg: str = "",
                           sender_name: str = "",
                           pending_context: str = "") -> list[dict]:
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
        prompt_vars = self.get_prompt_variables()
        system_prompt = system_prompt_template
        for key, value in prompt_vars.items():
            if value:
                system_prompt = system_prompt.replace(key, str(value))

        full_messages: list[dict] = [{"role": "system", "content": system_prompt}]
        full_messages.extend(self.message_history)

        # pending 上下文注入
        if pending_context:
            full_messages.append({
                "role": "system",
                "content": pending_context,
            })

        # 当前用户消息
        if current_user_msg:
            full_messages.append({
                "role": "user",
                "content": current_user_msg,
                "name": sender_name if sender_name else None,
            })

        return full_messages

    def get_prompt_variables(self) -> dict[str, str]:
        """返回 prompt 模板变量映射。"""
        from ..permission.level import level_label as _level_label

        return {
            "{project_name}": self.project_name,
            "{project_type}": self.project_type,
            "{project_status}": self.project_status,
            "{user_name}": self.user_name,
            "{user_position}": self.user_position,
            "{user_company}": self.company_name,
            "{user_company_type}": self.company_type,
            "{user_department}": "、".join(self.department) if self.department else "",
            "{user_level}": _level_label(self.level),
            "{user_permission_level}": _level_label(self.level),
            "{current_node_ids}": "、".join(self.authorized_node_ids),
            "{conversation_summary}": self.conversation_summary,
            "{user_memory}": self.long_term_memory,
            "{sop_catalog}": self.sop_catalog_summary,
            "{current_datetime}": self.current_datetime,
            "{available_skills}": ", ".join(self.available_skills) or "（无）",
            "{recent_turns}": "",
            "{available_tools}": self._format_tools_summary(),
            "{visible_schema}": self.visible_schema_summary,
            "{visible_files}": self.visible_files_summary,
            "{rag_info}": self._format_rag_summary(),
            "{project_world_book}": self.project_world_book,
            "{rule_book}": self.rule_book,
            "{system_description}": self.system_description,
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
        """归档时整合本次对话到 conversation_summary。"""
        from ..repositories.user_repo import UserRepository

        user = UserRepository.get_by_id(self.user_id)
        if not user:
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

    async def compress_overflow(self, llm_client) -> None:
        """裁剪 message_history：取最旧一批消息，调用 LLM 压缩为摘要。

        LLM 不可用时直接丢弃旧消息（fail-open）。
        """
        if not llm_client:
            batch = self.message_history[:20]
            self.message_history = self.message_history[20:]
            logger.debug("SessionContext compression skipped (no LLM): %d msgs dropped", len(batch))
            return

        batch = self.message_history[:20]
        self.message_history = self.message_history[20:]

        existing_summary = ""
        if (self.message_history
                and self.message_history[0].get("name") == "system"
                and "[对话历史摘要]" in self.message_history[0].get("content", "")):
            existing_summary = self.message_history[0]["content"]
            self.message_history = self.message_history[1:]

        compress_msgs = _build_compress_messages(batch, existing_summary)
        try:
            result = await llm_client.chat_messages(compress_msgs)
            summary_content = result.get("content", "") or ""
            if summary_content and len(summary_content) > 20:
                self.message_history.insert(0, {
                    "role": "user",
                    "content": f"[对话历史摘要] {summary_content.strip()}",
                    "name": "system",
                })
                logger.info("SessionContext compressed %d msgs → summary (%d chars), history now %d",
                            len(batch), len(summary_content), len(self.message_history))
        except Exception as e:
            logger.warning("SessionContext compression failed (msgs dropped): %s", e)

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
            "department": snapshot.get("department"),
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


def _build_compress_messages(history: list[dict], existing_summary: str) -> list[dict]:
    """构建压缩用的 messages 列表。"""
    if not history:
        return []
    history_text = _format_message_history(history)
    return [
        {"role": "system", "content": (
            "你是一个对话摘要助手。请将以下对话压缩为简短的要点摘要（中文，不超过 300 字），"
            "只保留关键事实：人物、事件、决策、任务、时间。不要包含套话。"
        )},
        {"role": "user", "content": (
            f"## 已有摘要\n{existing_summary or '（无）'}\n\n"
            f"## 近期对话\n{history_text}\n\n"
            f"请输出合并后的完整摘要（不超过 300 字）："
        )},
    ]


# 模块级兼容别名
format_message_history = _format_message_history
build_compress_messages = _build_compress_messages
