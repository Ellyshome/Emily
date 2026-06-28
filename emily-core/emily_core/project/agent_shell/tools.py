# emily-core/emily_core/project/agent_shell/tools.py
"""ProjectAgent 运维工具集 —— LLM function-calling 可调用的静态脚本。

每个工具都是一个 AI-friendly 的函数：
  - 完整的 description 让 LLM 理解何时调用
  - JSON Schema parameters 定义参数类型
  - handler 函数执行实际操作

设计原则：
  - 工具只做一件事，做好
  - 错误不抛到 LLM，返回友好的错误消息
  - 所有 DB 操作经过 Repository 层
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from emily_core.infrastructure.database.models import SMNode, OpsFinding

logger = logging.getLogger("emily.agent_shell.tools")


# ═══════════════════════════════════════════════════════════════════════════════
# Tool 定义（OpenAI function-calling 格式）
# ═══════════════════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_project_status",
            "description": (
                "查询项目的整体状态概览。返回各阶段节点数量、按状态分布的节点数、"
                "里程碑完成情况。当你需要了解项目整体进度、健康状况时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_stale_nodes",
            "description": (
                "列出卡滞/阻塞的节点。卡滞节点是指长期未更新的进行中节点。"
                "当你需要排查项目瓶颈、找出阻塞点时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold_days": {
                        "type": "integer",
                        "description": "卡滞天数阈值，默认 14 天。只返回超过此天数的节点。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_milestone_alerts",
            "description": (
                "列出即将到期的里程碑节点。当你需要了解哪些关键节点临近截止日期时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "warn_before_days": {
                        "type": "integer",
                        "description": "提前预警天数，默认 7 天。只返回在此天数内到期的里程碑。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_findings",
            "description": (
                "查看最近 N 条运维探针发现的问题（findings）。"
                "当你需要了解系统最近检测到的异常、告警时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 10。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_weekly_report",
            "description": (
                "生成项目周报并保存到文件。汇总本周的项目进展、卡滞节点、"
                "里程碑状态等信息，输出 Markdown 格式文件。"
                "当用户要求生成周报、每周报告时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_system_info",
            "description": (
                "显示当前系统运行信息：LLM 模型、数据库连接、节点总数等。"
                "当你需要了解系统配置和运行状况时调用此工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tool 执行器
# ═══════════════════════════════════════════════════════════════════════════════

class ToolExecutor:
    """工具执行器 —— 持有必要的依赖引用，分派工具调用。"""

    def __init__(self, node_repo, ops_repo, config, instance_id: str = ""):
        self._node_repo = node_repo
        self._ops_repo = ops_repo
        self._config = config
        self._instance_id = instance_id

    async def execute(self, name: str, arguments: dict) -> str:
        """执行指定工具，返回结果字符串。"""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"❌ 未知工具：{name}"

        try:
            result = await handler(arguments)
            return result
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return f"❌ 工具 {name} 执行失败：{e}"

    # ── 工具 handler ──

    async def _tool_query_project_status(self, args: dict) -> str:
        """查询项目整体状态。"""
        nodes = await asyncio.to_thread(self._node_repo.list_all)

        if not nodes:
            return "📊 项目状态：暂无节点数据（sm_nodes 表为空，请先导入全景节点数据）。"

        # 按状态分布
        status_counts: dict[str, int] = {}
        stage_counts: dict[int, int] = {}
        milestones_done = 0
        milestones_total = 0

        for n in nodes:
            status_counts[n.status] = status_counts.get(n.status, 0) + 1
            stage_counts[n.stage_id] = stage_counts.get(n.stage_id, 0) + 1
            if n.is_milestone:
                milestones_total += 1
                if n.status == "COMPLETED":
                    milestones_done += 1

        total = len(nodes)
        completed = status_counts.get("COMPLETED", 0)
        in_progress = status_counts.get("IN_PROGRESS", 0)
        blocked = status_counts.get("BLOCKED", 0)
        delayed = status_counts.get("DELAYED", 0)
        not_started = status_counts.get("NOT_STARTED", 0)

        progress_pct = round(completed / total * 100, 1) if total > 0 else 0

        stage_names = {
            1: "立项阶段", 2: "设计阶段", 3: "招标阶段",
            4: "施工阶段", 5: "验收阶段", 6: "交付阶段", 7: "运维阶段",
        }

        lines = [
            f"📊 项目整体状态",
            f"",
            f"  总节点数：{total}",
            f"  整体进度：{progress_pct}%（已完成 {completed}/{total}）",
            f"",
            f"  按状态分布：",
            f"    ✅ 已完成：{completed}",
            f"    🔄 进行中：{in_progress}",
            f"    🚫 已阻塞：{blocked}",
            f"    ⏰ 已延期：{delayed}",
            f"    ⬜ 未启动：{not_started}",
            f"",
            f"  按阶段分布：",
        ]
        for sid in sorted(stage_counts):
            name = stage_names.get(sid, f"阶段 {sid}")
            lines.append(f"    {name}：{stage_counts[sid]} 个节点")

        if milestones_total > 0:
            lines.append(f"")
            lines.append(f"  里程碑：{milestones_done}/{milestones_total} 已完成")

        return "\n".join(lines)

    async def _tool_list_stale_nodes(self, args: dict) -> str:
        """列出卡滞节点。"""
        threshold_days = args.get("threshold_days", 14)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat()

        nodes = await asyncio.to_thread(
            self._node_repo.list_stale,
            statuses=["IN_PROGRESS", "BLOCKED", "DELAYED"],
            older_than_iso=cutoff,
        )

        if not nodes:
            return f"✅ 没有卡滞超过 {threshold_days} 天的节点。"

        lines = [
            f"📋 卡滞节点清单（超过 {threshold_days} 天未更新，共 {len(nodes)} 个）：",
            "",
        ]
        for n in nodes:
            stale_days = "N/A"
            if n.updated_at:
                try:
                    dt = datetime.fromisoformat(str(n.updated_at))
                    stale_days = str((datetime.now(timezone.utc) - dt).days)
                except (ValueError, TypeError):
                    pass
            owner = n.owner or "未指定"
            lines.append(f"  🔴 {n.node_id} {n.node_name}")
            lines.append(f"     状态={n.status}  负责人={owner}  卡滞≈{stale_days}天")

        return "\n".join(lines)

    async def _tool_list_milestone_alerts(self, args: dict) -> str:
        """列出即将到期的里程碑。"""
        warn_days = args.get("warn_before_days", 7)
        now_iso = datetime.now(timezone.utc).isoformat()

        nodes = await asyncio.to_thread(
            self._node_repo.list_milestones_near_deadline,
            now_iso=now_iso,
            warn_before_days=warn_days,
        )

        if not nodes:
            return f"✅ 未来 {warn_days} 天内没有即将到期的里程碑。"

        lines = [
            f"📅 即将到期的里程碑（未来 {warn_days} 天，共 {len(nodes)} 个）：",
            "",
        ]
        for n in nodes:
            deadline = n.planned_end_date or "未设置"
            lines.append(f"  🟡 {n.node_id} {n.node_name}")
            lines.append(f"     截止日期={deadline}  状态={n.status}  负责人={n.owner or '未指定'}")

        return "\n".join(lines)

    async def _tool_list_recent_findings(self, args: dict) -> str:
        """查看最近 N 条探针发现问题。"""
        limit = args.get("limit", 10)

        findings = await asyncio.to_thread(
            self._ops_repo.get_recent_findings,
            limit=limit,
        )

        if not findings:
            return "✅ 最近没有探针发现的问题。"

        lines = [
            f"📝 最近 {len(findings)} 条发现问题：",
            "",
        ]
        for f in findings:
            severity_icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "ℹ️ "}.get(
                (f.severity or "").upper(), "•"
            )
            created = str(f.created_at)[:19] if f.created_at else ""
            lines.append(f"  {severity_icon} [{created}] {f.probe_name}: {f.message}")

        return "\n".join(lines)

    async def _tool_generate_weekly_report(self, args: dict) -> str:
        """生成项目周报。"""
        nodes = await asyncio.to_thread(self._node_repo.list_all)

        now = datetime.now(timezone.utc)
        # 一周前的周一
        week_start = now - timedelta(days=now.weekday() + 7)

        if not nodes:
            return "⚠️ 无法生成周报：sm_nodes 表为空，请先导入全景节点数据。"

        total = len(nodes)
        completed = sum(1 for n in nodes if n.status == "COMPLETED")
        in_progress = sum(1 for n in nodes if n.status == "IN_PROGRESS")
        blocked = sum(1 for n in nodes if n.status == "BLOCKED")
        delayed = sum(1 for n in nodes if n.status == "DELAYED")

        # 最近完成的节点
        recent_completed = [
            n for n in nodes
            if n.status == "COMPLETED" and n.updated_at and str(n.updated_at) >= week_start.isoformat()
        ]

        # 里程碑
        milestones = [n for n in nodes if n.is_milestone]
        milestones_done = sum(1 for n in milestones if n.status == "COMPLETED")

        report_lines = [
            f"# 项目周报",
            f"",
            f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M')}",
            f"> 统计周期：{week_start.strftime('%Y-%m-%d')} ~ {now.strftime('%Y-%m-%d')}",
            f"",
            f"## 整体进度",
            f"",
            f"- 总节点数：{total}",
            f"- 已完成：{completed}（{round(completed/total*100, 1) if total else 0}%）",
            f"- 进行中：{in_progress}",
            f"- 已阻塞：{blocked}",
            f"- 已延期：{delayed}",
            f"- 里程碑完成：{milestones_done}/{len(milestones)}",
            f"",
        ]

        if recent_completed:
            report_lines.append("## 本周完成节点")
            report_lines.append("")
            for n in recent_completed:
                report_lines.append(f"- {n.node_id} {n.node_name}")
            report_lines.append("")

        if blocked > 0:
            report_lines.append("## ⚠️ 阻塞节点")
            report_lines.append("")
            for n in nodes:
                if n.status == "BLOCKED":
                    reason = n.block_reason or "未填写原因"
                    report_lines.append(f"- {n.node_id} {n.node_name}：{reason}")
            report_lines.append("")

        # 保存到文件
        import os
        log_dir = getattr(self._config, "log_dir", "logs/")
        os.makedirs(log_dir, exist_ok=True)
        filename = f"weekly_{now.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = os.path.join(log_dir, filename)

        content = "\n".join(report_lines)
        await asyncio.to_thread(lambda: open(filepath, "w", encoding="utf-8").write(content))

        return f"✅ 周报已生成并保存到：{filepath}\n\n{content}"

    async def _tool_show_system_info(self, args: dict) -> str:
        """显示系统运行信息。"""
        cfg = self._config

        # 节点统计
        try:
            node_count = await asyncio.to_thread(self._node_repo.count)
        except Exception:
            node_count = -1

        # LLM 配置（脱敏）
        api_key_masked = ""
        if cfg.llm_api_key:
            ak = cfg.llm_api_key
            api_key_masked = ak[:4] + "****" + ak[-4:] if len(ak) > 8 else "****"

        lines = [
            "🔧 系统运行信息",
            "",
            f"  实例 ID：{self._instance_id or 'N/A'}",
            f"  LLM 模型：{cfg.llm_model}",
            f"  LLM 端点：{cfg.llm_base_url}",
            f"  API Key：{api_key_masked}",
            f"  Temperature：{cfg.llm_temperature}",
            f"  Max Tokens：{cfg.llm_max_tokens}",
            f"  DB URL：{cfg.database_url[:50] + '...' if len(cfg.database_url) > 50 else cfg.database_url}",
            f"  节点总数：{node_count if node_count >= 0 else 'DB 不可达'}",
        ]

        return "\n".join(lines)


def get_tool_definitions() -> list[dict]:
    """返回所有工具的 OpenAI function-calling 定义。"""
    return TOOL_DEFINITIONS
