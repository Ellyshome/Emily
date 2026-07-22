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
            context: 可选上下文信息（职位、公司、level 等）。

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
            f"> 自动生成，供人工复查。含对话、工具调用、LLM 调用记录。",
            f"> 会话ID: {conversation_id}  ·  开始: {start_display} (UTC+8)",
            f"> 人员: {persona}",
            "",
            "---",
            "",
        ]
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
                    args = getattr(tc, "tool_arguments", "{}") or "{}"
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
                lines.append(
                    f"  - #{i} {category} {mode_tag} {model} · {latency}ms · {tokens} tok"
                    + (f" · 摘要: {summary[:200]}" if summary else "")
                )

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
