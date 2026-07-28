"""InsightGenerator — 问题分析报告生成服务。

基于快照数据，生成结构化问题分析报告（供管理员日报后续分发）：
1. 采集快照 → snapshot dict
2. detect_anomalies(snapshot, days) → anomaly list
3. load_prompt("evolution_problem_report") → template
4. 组装 LLM 输入变量（快照各区块 + 异常信号）
5. llm.chat_json(system_prompt, user_message) → report JSON
6. 写 DB（EvolutionRepo.create_insight）
7. 写 MD（emily-data/evolution/insights/YYYY-MM-DD.md）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("emily.insight_generator")


class InsightGenerator:
    """问题分析报告生成器。"""

    def __init__(self, llm_client=None, data_dir: str = ""):
        self._llm = llm_client
        self._data_dir = data_dir or os.environ.get("EMILY_DATA_DIR", "")

    async def generate(self, end_date: str, *, days: int = 1, dry_run: bool = False) -> dict:
        """完整问题分析报告生成流水线。

        Args:
            end_date: 复盘结束日期 YYYY-MM-DD
            days: 复盘天数（默认 1）
            dry_run: 预览模式，不调 LLM 不写 DB

        Returns:
            {"end_date": ..., "days": ..., "snapshot": ..., "anomalies": ..., "report": ..., "status": ...}
        """
        # 1. 采集快照
        from emily_core.snapshot import collect_snapshot

        snapshot = await collect_snapshot(end_date, days=days)

        # 2. 异常检测
        import sys as _sys
        _HERE = Path(__file__).resolve().parent
        _SCRIPTS_DIR = _HERE.parent.parent.parent.parent.parent / "scripts"
        if str(_SCRIPTS_DIR) not in _sys.path:
            _sys.path.insert(0, str(_SCRIPTS_DIR))

        from evolution_anomaly import detect_anomalies

        anomalies = detect_anomalies(snapshot, days=days)

        start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
        start_date = start_dt.strftime("%Y-%m-%d")

        if self._llm is None or dry_run:
            return {
                "end_date": end_date,
                "start_date": start_date,
                "days": days,
                "snapshot": snapshot,
                "anomalies": anomalies,
                "report": None,
                "status": "preview",
            }

        # 3-5. LLM 生成报告
        try:
            from emily_core.infrastructure.llm.prompt_loader import load_prompt

            template = load_prompt("evolution_problem_report")
            analysis_period = f"{start_date} ~ {end_date}（{days}天）" if days > 1 else end_date

            user_message = template.replace("{analysis_period}", analysis_period)
            user_message = user_message.replace(
                "{projects_json}",
                json.dumps(snapshot.get("projects", {}).get("aggregate", {}), ensure_ascii=False, default=str)
            )
            user_message = user_message.replace(
                "{chat_samples_json}",
                json.dumps(snapshot.get("chat_samples", {}).get("conversations", []), ensure_ascii=False, default=str)
            )
            user_message = user_message.replace(
                "{system_errors_json}",
                json.dumps(snapshot.get("system_errors", {}), ensure_ascii=False, default=str)
            )
            user_message = user_message.replace(
                "{session_anomalies_json}",
                json.dumps(snapshot.get("session_anomalies", {}), ensure_ascii=False, default=str)
            )
            user_message = user_message.replace(
                "{anomaly_flags}",
                json.dumps(anomalies, ensure_ascii=False, default=str)
            )

            report = await self._llm.chat_json(template, user_message)
        except Exception as e:
            logger.error("LLM report generation failed: %s", e, exc_info=True)
            return {
                "end_date": end_date,
                "start_date": start_date,
                "days": days,
                "snapshot": snapshot,
                "anomalies": anomalies,
                "report": None,
                "status": "llm_error",
                "error": str(e),
            }

        # 6. 写 DB
        insight_date_key = f"{start_date}~{end_date}" if days > 1 else end_date
        try:
            from emily_core.repositories.evolution_repo import EvolutionRepo

            # 从快照提取 DB 所需字段
            chat = snapshot.get("chat_samples", {})
            sys_err = snapshot.get("system_errors", {})

            await asyncio.to_thread(
                EvolutionRepo.create_insight,
                insight_date_key,
                analysis_days=days,
                total_messages=chat.get("total_messages", 0),
                total_pipeline_runs=0,
                sop_hit_rate=0.0,
                fallback_rate=0.0,
                top_sop_ids="[]",
                feedback_summary="{}",
                anomaly_flags=json.dumps([a["flag"] for a in anomalies], ensure_ascii=False),
                insight_text=json.dumps(report, ensure_ascii=False),
                metrics_json=json.dumps(snapshot, ensure_ascii=False, default=str),
                health_score=report.get("health_score", 0),
            )
        except Exception as e:
            logger.error("Failed to write report to DB: %s", e, exc_info=True)

        # 7. 写 MD
        try:
            self._write_md(start_date, end_date, days, report, snapshot, anomalies)
        except Exception as e:
            logger.error("Failed to write report MD: %s", e, exc_info=True)

        return {
            "end_date": end_date,
            "start_date": start_date,
            "days": days,
            "snapshot": snapshot,
            "anomalies": anomalies,
            "report": report,
            "status": "generated",
        }

    def _write_md(self, start_date: str, end_date: str, days: int,
                  report: dict, snapshot: dict, anomalies: list) -> None:
        """写 emily-data/evolution/insights/YYYY-MM-DD.md"""

        data_dir = self._data_dir
        if not data_dir:
            candidate = Path(__file__).resolve().parent.parent.parent.parent.parent / "emily-data"
            if candidate.exists():
                data_dir = str(candidate)

        if not data_dir:
            logger.warning("Cannot determine emily-data directory, skipping MD output")
            return

        insights_dir = Path(data_dir) / "evolution" / "insights"
        insights_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{end_date}.md" if days == 1 else f"{start_date}_{end_date}.md"
        period_label = f"{end_date}（{days}天）" if days > 1 else end_date

        chat = snapshot.get("chat_samples", {})
        sys_err = snapshot.get("system_errors", {})

        lines = []
        lines.append(f"# 问题分析报告 — {period_label}")
        lines.append("")
        lines.append("## 运行概览")
        lines.append(f"- 聊天消息：{chat.get('total_messages', 0)} 条 | {chat.get('total_conversations', 0)} 个会话")
        lines.append(f"- 系统报错：{sys_err.get('error_count_dedup', 0)} 条")
        lines.append(f"- 健康评分：{report.get('health_score', 0)}/100")
        lines.append(f"- 总结：{report.get('summary', '')}")
        lines.append("")

        problems = report.get("problems", [])
        if problems:
            lines.append("## 发现问题")
            for i, p in enumerate(problems, 1):
                sev = {"critical": "严重", "warning": "警告", "info": "提示"}.get(p.get("severity", ""), "?")
                lines.append(f"### {i}. [{sev}] {p.get('title', '')}")
                lines.append(f"- 类别：{p.get('category', '')}")
                lines.append(f"- 描述：{p.get('description', '')}")
                lines.append(f"- 证据：{p.get('evidence', '')}")
                lines.append(f"- 根因：{p.get('root_cause', '')}")
                lines.append(f"- 建议：{p.get('suggestion', '')}")
                lines.append("")

        if report.get("chat_overview"):
            co = report["chat_overview"]
            lines.append("## 聊天质量评估")
            lines.append(f"- 活跃用户：{', '.join(co.get('active_users', []))}")
            lines.append(f"- 互动质量：{co.get('interaction_quality', 'N/A')}")
            if co.get("quality_notes"):
                lines.append(f"- 评估说明：{co['quality_notes']}")
            lines.append("")

        if anomalies:
            lines.append("## 异常信号")
            for a in anomalies:
                sev = "HIGH" if a["severity"] == "high" else "MED"
                lines.append(f"- [{sev}] {a['flag']}：{a['message']}")
            lines.append("")

        filepath = insights_dir / filename
        filepath.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Report MD written to %s", filepath)
