"""MorningReportBuilder — 晨报构建服务。

个性化晨报生成：用户节点 + 待办任务 + 昨日动态 + 截止临近 + 进化摘要（管理员）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("emily.morning_report_builder")


class MorningReportBuilder:
    """晨报构建器。"""

    def __init__(self):
        pass

    async def build_for_user(self, user, date: str, *, is_admin: bool = False) -> dict:
        """为单个用户构建晨报。

        Returns:
            {"user_name": ..., "report_text": ..., "sections": {...}}
        """
        from emily_core.repositories.evolution_repo import EvolutionRepo

        user_id = user.id
        user_name = user.username

        # 1. 用户负责节点
        nodes = await asyncio.to_thread(EvolutionRepo.get_user_nodes, user_id)

        # 2. 待办任务
        tasks = await asyncio.to_thread(EvolutionRepo.get_user_pending_tasks, user_id)

        # 3. 昨日动态
        yesterday = (datetime.fromisoformat(date) - timedelta(days=1)).strftime("%Y-%m-%d")
        events = await asyncio.to_thread(EvolutionRepo.get_user_recent_events, user_id, yesterday)

        # 4. 构建晨报文本
        report_text = self._format_report(user_name, date, nodes, tasks, events, is_admin)

        sections = {
            "nodes": [{"node_id": n.node_id, "name": n.node_name, "progress": n.progress, "deadline": n.deadline, "status": n.status} for n in nodes],
            "tasks": [{"title": t.title, "status": t.status, "due_date": t.due_date} for t in tasks],
            "events": [{"summary": e.summary, "action": e.event_action} for e in events],
        }

        return {"user_name": user_name, "report_text": report_text, "sections": sections}

    async def build_for_date(self, date: str, *, is_admin: bool = False) -> list[dict]:
        """为所有 active 用户构建晨报。"""
        from emily_core.repositories.evolution_repo import EvolutionRepo

        users = await asyncio.to_thread(EvolutionRepo.get_active_users)
        reports = []
        for user in users:
            report = await self.build_for_user(user, date, is_admin=(user.level >= 5))
            reports.append(report)
        return reports

    def _format_report(self, user_name: str, date: str, nodes: list, tasks: list, events: list, is_admin: bool) -> str:
        """格式化晨报文本。"""
        dt = datetime.fromisoformat(date)
        weekdays_cn = ["一", "二", "三", "四", "五", "六", "日"]
        weekday = weekdays_cn[dt.weekday()]

        lines = []
        lines.append(f"\u2600\ufe0f 早啊{user_name}！今天是{dt.month}月{dt.day}号星期{weekday}，新的一天开工啦～")
        lines.append("")

        # 节点进度
        if nodes:
            lines.append("\U0001f4cb 你的节点进度")
            lines.append("")
            for n in nodes:
                try:
                    progress = float(n.progress)
                except (ValueError, TypeError):
                    progress = 0.0
                icon = "\U0001f7e2" if progress >= 70 else ("\U0001f7e1" if progress >= 30 else "\u26aa")
                lines.append(f"{icon} {n.node_name} ({n.node_id}) — {n.progress}%，截止{n.deadline}")
            lines.append("")

        # 待办任务
        if tasks:
            lines.append("\U0001f4cc 待办任务")
            lines.append("")
            for t in tasks:
                due = t.due_date or ""
                urgency = ""
                if due and due <= date:
                    icon = "\U0001f534"
                    urgency = "— 截止今天！"
                elif due and due <= (datetime.fromisoformat(date) + timedelta(days=3)).strftime("%Y-%m-%d"):
                    icon = "\U0001f7e1"
                    urgency = f"— 截止{due}"
                else:
                    icon = "\U0001f7e2"
                    urgency = f"— 截止{due}" if due else ""
                lines.append(f"{icon} {t.title} {urgency}")
            lines.append("")

        # 昨日动态
        if events:
            lines.append("\U0001f4ca 昨日动态")
            lines.append("")
            for e in events[:5]:
                lines.append(f"\u2022 {e.event_action}: {e.summary}")
            lines.append("")

        # 截止临近
        overdue_nodes = [n for n in nodes if n.deadline and n.deadline < date]
        upcoming = [n for n in nodes if n.deadline and date <= n.deadline <= (datetime.fromisoformat(date) + timedelta(days=7)).strftime("%Y-%m-%d")]

        if overdue_nodes or upcoming:
            lines.append("\u26a0\ufe0f 注意")
            lines.append("")
            for n in overdue_nodes:
                lines.append(f"\U0001f534 {n.node_name} 已逾期！截止 {n.deadline}")
            for n in upcoming:
                days_left = (datetime.fromisoformat(n.deadline) - datetime.fromisoformat(date)).days
                lines.append(f"\U0001f7e1 {n.node_name} 距截止仅 {days_left} 天")

        # 收尾语
        urgent_count = len(overdue_nodes) + len([n for n in upcoming if n.deadline <= (datetime.fromisoformat(date) + timedelta(days=3)).strftime("%Y-%m-%d")])
        if urgent_count > 0:
            lines.append(f"\n今天有{urgent_count}件事比较急，盯紧点\U0001f4aa 早上先搞定最紧迫的吧")
        else:
            lines.append(f"\n今天节奏不错，继续保持\U0001f44d")

        return "\n".join(lines)

    async def build_admin_section(self, date: str) -> str:
        """构建管理员专属进化摘要部分。"""
        from emily_core.repositories.evolution_repo import EvolutionRepo

        yesterday = (datetime.fromisoformat(date) - timedelta(days=1)).strftime("%Y-%m-%d")

        lines = []
        lines.append("\n\n\U0001f4ca 系统进化摘要（昨日）")

        try:
            insight = await asyncio.to_thread(EvolutionRepo.get_insight_by_date, yesterday)
            if insight:
                lines.append(f"健康评分: {insight.health_score}/100")
                lines.append(f"SOP 命中率: {insight.sop_hit_rate:.1%}")
                lines.append(f"Fallback 率: {insight.fallback_rate:.1%}")

            draft_patches = await asyncio.to_thread(EvolutionRepo.get_patches_by_status, "DRAFT")
            if draft_patches:
                lines.append("\n\U0001f527 待审批补丁")
                for p in draft_patches:
                    lines.append(f"  {p.patch_no}: {p.rule_no} [{p.risk_level}] — {p.target_path}")
        except Exception as e:
            logger.warning("Failed to build admin section: %s", e)

        return "\n".join(lines)
