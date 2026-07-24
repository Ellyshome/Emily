"""SessionArchiveWriter —— 会话归档 md 文件实时追加服务。

每个 Session（conversation_id）一个 md 文件，按时间+人员命名：
  {开始日期}_{人员}_{conv_id前8位}.md

每轮对话实时追加，Session 归档时写结尾。
参照 event_journal.py 的追加式 md 模式。
"""

import os
import re
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("emily.service.session_archive_writer")


# 文件名非法字符（Windows + Linux 通用）
_FILENAME_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize_filename(name: str) -> str:
    """移除文件名中的非法字符，空格替换为下划线。"""
    name = _FILENAME_ILLEGAL_RE.sub("", name)
    name = name.replace(" ", "_")
    # 去掉连续下划线
    name = re.sub(r"_{2,}", "_", name)
    # 去掉首尾下划线
    name = name.strip("_")
    return name or "anonymous"


def _beijing_date_str() -> str:
    """返回北京时间日期字符串 YYYY-MM-DD。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _beijing_time_str() -> str:
    """返回北京时间时间字符串 HH:MM:SS。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%H:%M:%S")


def _beijing_datetime_str() -> str:
    """返回北京时间完整日期时间。"""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


class SessionArchiveWriter:
    """会话归档 md 文件写入服务。

    提供会话归档 md 文件的创建、每轮追加、结尾写入能力。
    所有文件 I/O 包装 try/except，失败只记 warning，绝不阻断 Agent 主流程。

    Args:
        archive_dir: 归档目录路径。
        enabled: 是否启用归档写入。
    """

    def __init__(self, archive_dir: str, enabled: bool = True):
        self.archive_dir = archive_dir
        self.enabled = enabled
        if self.enabled and self.archive_dir:
            os.makedirs(self.archive_dir, exist_ok=True)

    def _path_for(self, conversation_id: str, user_name: str, started_at: str = "") -> Path:
        """生成归档文件路径。

        命名规则：{开始日期}_{人员 sanitized}_{conv_id[:8]}.md
        同 conv_id 续接则复用现有文件。

        Args:
            conversation_id: 会话 ID。
            user_name: 用户姓名。
            started_at: 会话开始时间 ISO8601 字符串。

        Returns:
            Path: 归档文件路径。
        """
        date_str = ""
        if started_at:
            try:
                dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                local_dt = dt + timedelta(hours=8)
                date_str = local_dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date_str = _beijing_date_str()
        else:
            date_str = _beijing_date_str()

        safe_name = _sanitize_filename(user_name) or "anonymous"
        conv_short = conversation_id[:8] if len(conversation_id) >= 8 else conversation_id
        filename = f"{date_str}_{safe_name}_{conv_short}.md"
        return Path(self.archive_dir) / filename

    # ── 渲染函数（纯函数，无 I/O，可独立测试）──

    @staticmethod
    def _join_list(items: list, sep: str = "、", limit: int = 10) -> str:
        """列表拼接为字符串，超过 limit 截断并标注总数。

        Args:
            items: 待拼接的列表。
            sep: 分隔符（默认顿号）。
            limit: 最多展示的元素数，超出则尾部标注「等 N 项」。

        Returns:
            str: 拼接结果；空列表返回空字符串。
        """
        if not items:
            return ""
        cleaned = [str(x) for x in items if x is not None and x != ""]
        if not cleaned:
            return ""
        if len(cleaned) <= limit:
            return sep.join(cleaned)
        return f"{sep.join(cleaned[:limit])} 等 {len(cleaned)} 项"

    @staticmethod
    def _render_snapshot(ctx: dict) -> list[str]:
        """渲染拉起时会话快照（身份/可见范围/项目/能力）。

        供管理员复查「该 Session 实际拉起时的运行上下文」。所有字段缺失则整行
        跳过；大文本字段（世界书/规则书等）只显示「有 + 字符数」，不写全文
        （避免归档正文膨胀；全文可回查 DB）。

        Args:
            ctx: SessionContext 拉起时字段构成的 dict。

        Returns:
            list[str]: 快照 markdown 行；无任何可写字段时返回空列表。
        """
        lines: list[str] = []

        # ── 身份与组织 ──
        identity: list[str] = []
        user_id = ctx.get("user_id", "")
        if user_id:
            identity.append(f"- user_id: {user_id}")
        pos = ctx.get("user_position", "")
        dept = SessionArchiveWriter._join_list(ctx.get("department", []))
        if pos or dept:
            parts = [p for p in (f"职位: {pos}" if pos else "", f"部门: {dept}" if dept else "") if p]
            identity.append(f"- {' · '.join(parts)}")
        company = ctx.get("company_name", "")
        if company:
            parts = [company]
            ctype = ctx.get("company_type", "")
            if ctype:
                parts.append(ctype)
            cid = ctx.get("company_id", "")
            if cid:
                parts.append(f"id={cid}")
            identity.append(f"- 企业: {' / '.join(parts)}")
        level = ctx.get("level", 1)
        is_mgmt = ctx.get("is_management_unit", False)
        identity.append(f"- 权限: level {level}（{'管理单位' if is_mgmt else '非管理单位'}）")
        if identity:
            lines.append("**身份与组织**")
            lines.extend(identity)
            lines.append("")

        # ── 可见范围 ──
        scope: list[str] = []
        nodes = SessionArchiveWriter._join_list(ctx.get("authorized_node_ids", []))
        if nodes:
            scope.append(f"- 授权节点: {nodes}")
        scopes = SessionArchiveWriter._join_list(ctx.get("scopes", []), sep=" · ")
        if scopes:
            scope.append(f"- scopes: {scopes}")
        proj_ids = SessionArchiveWriter._join_list(ctx.get("project_ids", []))
        if proj_ids:
            scope.append(f"- 可见项目: {proj_ids}")
        partner_ids = SessionArchiveWriter._join_list(ctx.get("partner_ids", []))
        if partner_ids:
            scope.append(f"- 合作方: {partner_ids}")
        info_level = ctx.get("info_level", "")
        if info_level:
            scope.append(f"- info_level: {info_level}")
        sop_allow = ctx.get("sop_allow", [])
        if sop_allow:
            scope.append(f"- sop_allow: {SessionArchiveWriter._join_list(sop_allow)}")
        perm_ver = ctx.get("permission_version", 0)
        perm_loaded = ctx.get("permissions_loaded_at", "")
        if perm_ver or perm_loaded:
            scope.append(f"- 权限版本: v{perm_ver} @ {perm_loaded or '(未知)'}")
        if scope:
            lines.append("**可见范围**")
            lines.extend(scope)
            lines.append("")

        # ── 项目上下文 ──
        proj_parts = [p for p in (
            ctx.get("project_name", ""),
            ctx.get("project_type", ""),
            ctx.get("project_status", ""),
        ) if p]
        if proj_parts:
            lines.append(f"**项目**: {' / '.join(proj_parts)}")
            lines.append("")

        # ── 能力摘要 ──
        cap: list[str] = []
        skills = ctx.get("available_skills", [])
        tools = ctx.get("available_tools", [])
        files_count = ctx.get("visible_files_count", 0)
        cap_parts = [p for p in (
            f"技能 {len(skills)}" if skills else "",
            f"工具 {len(tools)}" if tools else "",
            f"可见文件 {files_count}" if files_count else "",
        ) if p]
        if cap_parts:
            cap.append(f"- {' · '.join(cap_parts)}")
        rag_avail = ctx.get("rag_available", False)
        rag_cols = ctx.get("rag_collections", [])
        if rag_avail:
            cols = SessionArchiveWriter._join_list(rag_cols) or "默认知识库"
            cap.append(f"- 知识库: 可用（{cols}）")
        else:
            cap.append("- 知识库: 不可用")
        # 大文本字段：有/无 + 字符数（不写全文）
        big_texts = [
            ("项目世界书", ctx.get("project_world_book", "")),
            ("规则书", ctx.get("rule_book", "")),
            ("系统自我描述", ctx.get("system_description", "")),
            ("可见库表摘要", ctx.get("visible_schema_summary", "")),
            ("可见文件摘要", ctx.get("visible_files_summary", "")),
            ("SOP目录摘要", ctx.get("sop_catalog_summary", "")),
        ]
        big_parts = [f"{label} {len(text)} 字" for label, text in big_texts if text]
        if big_parts:
            cap.append(f"- {' · '.join(big_parts)}")
        if cap:
            lines.append("**能力**")
            lines.extend(cap)
            lines.append("")

        return lines

    @staticmethod
    def _render_header(
        conversation_id: str,
        user_name: str,
        started_at: str,
        context: Optional[dict] = None,
    ) -> str:
        """渲染文件头部。

        Args:
            conversation_id: 会话 ID。
            user_name: 用户姓名。
            started_at: 会话开始时间。
            context: 可选上下文信息。含身份/可见范围/项目/能力等拉起时快照字段，
                以及 prompt_name/prompt_chars 元信息；缺字段自动跳过。

        Returns:
            str: 文件头部 markdown。
        """
        ctx = context or {}
        position = ctx.get("user_position", "")
        company = ctx.get("company_name", "")
        level = ctx.get("level", 1)

        start_display = started_at
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            local_dt = dt + timedelta(hours=8)
            start_display = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            pass

        persona = user_name
        extras = []
        if position:
            extras.append(position)
        if company:
            extras.append(company)
        if level:
            extras.append(f"level {level}")
        if extras:
            persona += "（" + " · ".join(extras) + "）"

        lines = [
            f"# Emily 会话归档：{user_name}",
            "",
            f"> 会话ID: {conversation_id}  ·  开始: {start_display} (UTC+8)",
            f"> 人员: {persona}",
        ]

        # prompt 元信息（模板原文字符数；渲染后变量值见下方快照区）
        prompt_name = ctx.get("prompt_name", "")
        prompt_chars = ctx.get("prompt_chars", 0)
        if prompt_name:
            lines.append(
                f"> Session Prompt: {prompt_name} (模板 {prompt_chars} 字，变量见快照)"
            )

        # 会话快照（拉起时）——身份/可见范围/项目/能力
        snapshot_lines = SessionArchiveWriter._render_snapshot(ctx)
        if snapshot_lines:
            lines.append("")
            lines.append("## 会话快照（拉起时）")
            lines.append("")
            lines.extend(snapshot_lines)
            # snapshot_lines 每个区块末尾自带空行，无需再补
        else:
            lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_turn(
        turn_idx: int,
        user_message: str,
        reply_content: str,
        turn_time: str = "",
        workitems: Optional[list] = None,
        llm_logs: Optional[list] = None,
    ) -> str:
        """渲染一轮对话。

        Args:
            turn_idx: 轮次序号（从 1 开始）。
            user_message: 用户消息文本。
            reply_content: Emily 回复文本。
            turn_time: 本轮时间。
            workitems: WorkItem 列表。
            llm_logs: EvolutionLLMInteractionLog 列表。

        Returns:
            str: 一轮的 markdown 文本。
        """
        time_str = turn_time or _beijing_time_str()
        wits = workitems or []
        logs = llm_logs or []

        lines = [
            f"## 第 {turn_idx} 轮 · {time_str}",
            "",
            "### 👤 用户",
            user_message or "（空消息）",
            "",
            "### 🤖 Emily",
            reply_content or "（无回复）",
            "",
        ]

        # 每个 WorkItem 的执行追踪
        for wi in wits:
            lines.extend(SessionArchiveWriter._render_workitem(wi, logs))

        lines.append("---")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_workitem(wi, llm_logs: list) -> list[str]:
        """渲染单个 WorkItem 的执行追踪。

        Args:
            wi: WorkItem 实例。
            llm_logs: 所有 LLM 日志（按 pipeline_run_id 过滤）。

        Returns:
            list[str]: workitem 的 markdown 行。
        """
        sop_id = getattr(wi, "sop_id", "") or "unknown"
        state = getattr(wi, "state", None)
        state_str = state.value if hasattr(state, "value") else str(state)

        lines = [
            f"### 🔧 执行追踪（WorkItem · sop={sop_id} · {state_str}）",
        ]

        # 意图
        user_input = getattr(wi, "user_input", "") or ""
        intent_type = getattr(wi, "intent_type", "") or ""
        if user_input:
            lines.append(f"- 意图: sop={sop_id}, 意图类型={intent_type}")

        # 工具调用
        step_results = getattr(wi, "step_results", []) or []
        if step_results:
            lines.append("- 工具调用:")
            for sr in step_results:
                tool_calls = getattr(sr, "tool_calls", []) or []
                for tc in tool_calls:
                    name = getattr(tc, "tool_name", "?") or "?"
                    success = "✓" if getattr(tc, "is_success", True) else "✗"
                    elapsed = getattr(tc, "elapsed_ms", 0) or 0
                    args = getattr(tc, "tool_input", "{}") or "{}"
                    arg_preview = (str(args)[:120] + "...") if len(str(args)) > 120 else str(args)
                    lines.append(f"  - {success} {name} ({elapsed}ms)  参数: {arg_preview}")

        # LLM 调用明细
        wi_run_id = getattr(wi, "pipeline_run_id", "")
        matched_logs = [l for l in llm_logs if getattr(l, "pipeline_run_id", "") == wi_run_id] if wi_run_id else llm_logs

        if matched_logs:
            lines.append(f"- LLM 调用 ({len(matched_logs)}):")
            for i, log in enumerate(matched_logs, 1):
                category = getattr(log, "call_category", "") or "?"
                model = getattr(log, "model", "") or "?"
                latency = getattr(log, "latency_ms", 0) or 0
                tokens = getattr(log, "total_tokens", 0) or 0
                summary = getattr(log, "response_summary", "") or ""
                json_mode = getattr(log, "json_mode", False)
                mode_tag = "[json]" if json_mode else ""
                full = getattr(log, "response_full", "") or ""
                display = full or summary
                lines.append(
                    f"  - #{i} {category} {mode_tag} {model} · {latency}ms · {tokens} tok"
                    + (f" · 摘要: {display[:2000]}{'…(共%d字)' % len(display) if len(display) > 2000 else ''}" if display else "")
                )
                reasoning = getattr(log, "reasoning_content", "") or ""
                if reasoning:
                    reasoning_flat = reasoning.replace("\n", " ").replace("\r", " ")
                    lines.append(f"    💭 思维链(共{len(reasoning)}字): {reasoning_flat[:2000]}{'…' if len(reasoning) > 2000 else ''}")

        # 错误信息
        error = getattr(wi, "error_message", "") or ""
        if error:
            lines.append(f"- ⚠️ 错误: {error[:300]}")

        lines.append("")
        return lines

    @staticmethod
    def _render_footer(turn_count: int, archive_reason: str = "expired") -> str:
        """渲染归档结尾。

        Args:
            turn_count: 总轮数。
            archive_reason: 归档原因。

        Returns:
            str: 归档结尾 markdown。
        """
        now = _beijing_datetime_str()
        reason_map = {
            "expired": "expired (TTL 无活动)",
            "terminated": "手动终止",
            "manual": "手动归档",
        }
        reason_display = reason_map.get(archive_reason, archive_reason)

        lines = [
            "",
            "## 会话归档",
            f"- 归档时间: {now}",
            f"- 归档原因: {reason_display}",
            f"- 总轮数: {turn_count}",
        ]
        return "\n".join(lines)

    # ── V2 逐段追加渲染方法（纯函数，无 I/O）──

    @staticmethod
    def _render_llm_log_line(log, idx: int) -> list[str]:
        """渲染单条 LLM 调用明细行（主行 + 可选摘要行）。"""
        category = getattr(log, "call_category", "") or "?"
        model = getattr(log, "model", "") or "?"
        latency = getattr(log, "latency_ms", 0) or 0
        tokens = getattr(log, "total_tokens", 0) or 0
        summary = getattr(log, "response_summary", "") or ""
        json_mode = getattr(log, "json_mode", False)
        mode_tag = "[json]" if json_mode else ""
        lines = [f"  - #{idx} {category} {mode_tag} {model} · {latency}ms · {tokens} tok"]
        full = getattr(log, "response_full", "") or ""
        display = full or summary
        if display:
            lines.append(f"    摘要: {display[:2000]}{'…(共%d字)' % len(display) if len(display) > 2000 else ''}")
        reasoning = getattr(log, "reasoning_content", "") or ""
        if reasoning:
            reasoning_flat = reasoning.replace("\n", " ").replace("\r", " ")
            lines.append(f"    💭 思维链(共{len(reasoning)}字): {reasoning_flat[:2000]}{'…' if len(reasoning) > 2000 else ''}")
        return lines

    @staticmethod
    def _render_prompt_info(prompt_info) -> list[str]:
        """渲染 Prompt 注入信息段落（模板名 + 渲染后字符数 + 关键变量摘要）。

        格式：
          - Prompt: planner.md (渲染后 1560 字)
            - 关键变量: sop_text=847字 · user_input="..." · available_tools=6个
            - Session级: user_name=李景利 · project_name=翠湖庭院 · level=L4
        """
        if not prompt_info or not isinstance(prompt_info, dict):
            return []
        template = prompt_info.get("template", "?")
        chars = prompt_info.get("rendered_chars", 0)
        chars_display = f"渲染后 {chars} 字" if chars else "（未追踪）"
        lines = [f"- Prompt: {template} ({chars_display})"]

        variables = prompt_info.get("variables", {}) or {}
        if variables:
            var_parts: list[str] = []
            session_vars = None
            for key, value in variables.items():
                if key == "session_vars" and isinstance(value, dict):
                    session_vars = value
                    continue
                val_str = str(value)
                if len(val_str) > 300:
                    val_str = val_str[:297] + "..."
                var_parts.append(f"{key}={val_str}")
            if var_parts:
                lines.append(f"  - 关键变量: {' · '.join(var_parts)}")
            if session_vars:
                sv_parts = [f"{sk}={str(sv)[:120]}" for sk, sv in session_vars.items()]
                lines.append(f"  - Session级: {' · '.join(sv_parts)}")
        return lines

    @staticmethod
    def render_turn_start(turn_idx: int, user_message: str, turn_time: str = "") -> str:
        """渲染轮次开头：标题 + 用户消息段。"""
        time_str = turn_time or _beijing_time_str()
        return "\n".join([
            f"## 第 {turn_idx} 轮 · {time_str}",
            "",
            "### 👤 用户",
            user_message or "（空消息）",
            "",
        ])

    @staticmethod
    def render_intent_section(intent_data: dict, llm_logs: list,
                              prompt_info=None) -> str:
        """渲染意图识别段（SessionAgent 层，BUS 之前）。"""
        intent_data = intent_data or {}
        lines = ["### 🔍 意图识别"]
        sop_id = intent_data.get("sop_id", "") or "unknown"
        fallback = intent_data.get("fallback", False)
        intent_type = "fallback" if fallback else "sop"
        confidence = intent_data.get("confidence", "") or "none"
        lines.append(f"- sop={sop_id}, 意图类型={intent_type}, 置信度={confidence}")
        reasoning = intent_data.get("reasoning", "")
        if reasoning:
            lines.append(f"- 推理: {str(reasoning)[:200]}")
        if prompt_info:
            lines.extend(SessionArchiveWriter._render_prompt_info(prompt_info))
        if llm_logs:
            lines.append(f"- LLM 调用 ({len(llm_logs)}):")
            for i, log in enumerate(llm_logs, 1):
                lines.extend(SessionArchiveWriter._render_llm_log_line(log, i))
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_node_section(node_name: str, work_item, llm_logs: list,
                            prompt_info=None,
                            prompt_info_guardian=None) -> str:
        """渲染单个 BUS 节点的归档段落（含 Prompt 注入信息）。"""
        node_labels = {
            "wi_node1": "意图验证",
            "wi_node2": "规划",
            "wi_node3": "执行+验收",
            "wi_node4": "成果总结",
        }
        label = node_labels.get(node_name, node_name)
        node_idx = node_name[-1] if node_name else "?"
        lines = [f"### 🔧 {label} (Node{node_idx})"]

        wi = work_item

        if node_name == "wi_node1":
            sop_id = getattr(wi, "sop_id", "") or "unknown"
            intent_type = getattr(wi, "intent_type", "") or "fallback"
            route = getattr(wi, "route_decision", None)
            source = getattr(route, "_source", "") if route else ""
            lines.append(f"- sop={sop_id}, 意图类型={intent_type}, _source={source or 'session_agent'}")
            lines.append("- Prompt: (node1 无 LLM 调用，仅验证路由)")

        elif node_name == "wi_node2":
            plan = getattr(wi, "execution_plan", None)
            if plan:
                risk = getattr(plan, "risk_level", "?")
                source = getattr(plan, "_source", "?")
                lines.append(f"- 风险等级: {risk}, _source={source}")
                steps = getattr(plan, "steps", []) or []
                if steps:
                    lines.append("- 步骤:")
                    for s in steps:
                        tool = getattr(s, "tool_name", "") or "(无工具)"
                        desc = (getattr(s, "description", "") or "")[:80]
                        lines.append(f"  - {getattr(s, 'step_id', '?')} ({tool}) — {desc}")
            if prompt_info:
                lines.extend(SessionArchiveWriter._render_prompt_info(prompt_info))

        elif node_name == "wi_node3":
            step_results = getattr(wi, "step_results", []) or []
            if step_results:
                lines.append("- 工具调用:")
                for sr in step_results:
                    for tc in getattr(sr, "tool_calls", []) or []:
                        name = getattr(tc, "tool_name", "?") or "?"
                        success = "✓" if getattr(tc, "is_success", True) else "✗"
                        elapsed = getattr(tc, "elapsed_ms", 0) or 0
                        args = getattr(tc, "tool_input", "{}") or "{}"
                    arg_preview = (str(args)[:120] + "...") if len(str(args)) > 120 else str(args)
                    lines.append(f"  - {success} {name} ({elapsed}ms)  参数: {arg_preview}")
            # Guardian 并进审核
            guardian_notes: list[str] = []
            for sr in step_results:
                g = getattr(sr, "guardian", None)
                if g:
                    verdict = getattr(g, "verdict", "?")
                    reason = (getattr(g, "reason", "") or "")[:100]
                    guardian_notes.append(f"  - {getattr(sr, 'step_id', '?')}: {verdict} ({reason})")
            if guardian_notes:
                lines.append("- Guardian 并进审核:")
                lines.extend(guardian_notes)
            # Prompt 信息（可能是 list，每个 step 一项）
            if prompt_info:
                pis = prompt_info if isinstance(prompt_info, list) else [prompt_info]
                for pi in pis:
                    lines.extend(SessionArchiveWriter._render_prompt_info(pi))

        elif node_name == "wi_node4":
            lines.append(f"- 回复合成: {getattr(wi, 'llm_call_count', 0)} 次 LLM 调用")
            if getattr(wi, "warnings", None):
                lines.append("- Guardian 出站审核:")
                for w in wi.warnings[-5:]:
                    lines.append(f"  - {w[:150]}")
            if prompt_info:
                lines.extend(SessionArchiveWriter._render_prompt_info(prompt_info))
            if prompt_info_guardian:
                lines.extend(SessionArchiveWriter._render_prompt_info(prompt_info_guardian))

        # LLM 调用明细（按 call_category 分组）
        if llm_logs:
            phase_groups: dict = {}
            for log in llm_logs:
                cat = getattr(log, "call_category", "") or "unknown"
                phase_groups.setdefault(cat, []).append(log)
            lines.append(f"- LLM 调用 ({len(llm_logs)}):")
            phase_labels = {
                "intent": "意图",
                "planning": "规划",
                "execution": "执行/合成",
                "guardian": "审核",
            }
            for phase in ["intent", "planning", "execution", "guardian"]:
                group = phase_groups.get(phase, [])
                if not group:
                    continue
                plabel = phase_labels.get(phase, phase)
                lines.append(f"  [{plabel}] ({len(group)}):")
                for i, log in enumerate(group, 1):
                    lines.extend(SessionArchiveWriter._render_llm_log_line(log, i))

        # 错误信息
        error = getattr(wi, "error_message", "") or ""
        if error:
            lines.append(f"- ⚠️ 错误: {error[:300]}")

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_turn_end(reply_body: str, guardian_warnings: str = "") -> str:
        """渲染轮次结尾：系统审核标记（如有）+ Emily 回复 + 分隔线。"""
        lines = []
        if guardian_warnings:
            lines.extend([
                "### ⚠️ 系统审核标记",
                guardian_warnings.strip(),
                "",
            ])
        lines.extend([
            "### 🤖 Emily",
            reply_body or "（无回复）",
            "",
            "---",
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _split_reply_and_warnings(reply_content: str) -> tuple[str, str]:
        """将 reply_content 中的 Guardian warning 段分离出来。

        node4_summary 以 '\\n\\n⚠️ Emily 提醒' 为标记将 warnings 追加到回复末尾，
        此方法将其拆分，供 render_turn_end 分别写入独立段落。
        """
        if not reply_content:
            return "", ""
        marker = "\n\n⚠️ Emily 提醒"
        if marker in reply_content:
            idx = reply_content.index(marker)
            return reply_content[:idx], reply_content[idx:]
        return reply_content, ""

    # ── 文件 I/O 方法 ──

    def ensure_header(
        self,
        conversation_id: str,
        user_name: str,
        started_at: str,
        context: Optional[dict] = None,
    ) -> str:
        """确保归档文件存在并已写入头部（幂等）。

        Args:
            conversation_id: 会话 ID。
            user_name: 用户姓名。
            started_at: 会话开始时间。
            context: 可选上下文信息。

        Returns:
            str: 文件绝对路径；未启用或失败返回空字符串。
        """
        if not self.enabled or not self.archive_dir:
            return ""

        path = self._path_for(conversation_id, user_name, started_at)
        try:
            os.makedirs(path.parent, exist_ok=True)
            if not path.exists():
                header = self._render_header(conversation_id, user_name, started_at, context)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header)
                logger.info("SessionArchive header created: %s", path)
            return str(path)
        except OSError as e:
            logger.warning("SessionArchive ensure_header failed: %s — %s", path, e)
            return ""

    def append_turn(
        self,
        path: str,
        turn_idx: int,
        user_message: str,
        reply_content: str,
        workitems: Optional[list] = None,
        llm_logs: Optional[list] = None,
    ) -> bool:
        """渲染并追加一轮对话到归档文件。

        Args:
            path: 文件路径（由 ensure_header 返回）。
            turn_idx: 轮次序号。
            user_message: 用户消息。
            reply_content: Emily 回复。
            workitems: WorkItem 列表。
            llm_logs: EvolutionLLMInteractionLog 列表。

        Returns:
            bool: 是否成功写入。
        """
        if not self.enabled or not path:
            return False

        try:
            content = self._render_turn(
                turn_idx=turn_idx,
                user_message=user_message,
                reply_content=reply_content,
                workitems=workitems,
                llm_logs=llm_logs,
            )
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            logger.debug("SessionArchive turn %d appended: %s", turn_idx, path)
            return True
        except OSError as e:
            logger.warning("SessionArchive append_turn failed: %s — %s", path, e)
            return False

    def append_footer(self, path: str, turn_count: int, archive_reason: str = "expired") -> bool:
        """渲染并追加归档结尾到归档文件。

        Args:
            path: 文件路径。
            turn_count: 总轮数。
            archive_reason: 归档原因。

        Returns:
            bool: 是否成功写入。
        """
        if not self.enabled or not path:
            return False

        try:
            content = self._render_footer(turn_count, archive_reason)
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            logger.info("SessionArchive footer appended: %s (turns=%d)", path, turn_count)
            return True
        except OSError as e:
            logger.warning("SessionArchive append_footer failed: %s — %s", path, e)
            return False

    def append_section(self, path: str, content: str) -> bool:
        """追加一段内容到归档文件（供 ArchiveHook 和 SessionAgent 逐段调用）。

        与 EventJournal.append() 同模式：open(path, "a") + try/except OSError。
        """
        if not self.enabled or not path:
            return False
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            logger.warning("SessionArchive append_section failed: %s — %s", path, e)
            return False
