"""InsightGenerator — 周期洞察生成服务。

完整洞察生成流水线（支持可变周期）：
1. collect_metrics(end_date, days=days) → metrics dict
2. detect_anomalies(metrics, days=days) → anomaly list
3. load_prompt("evolution_insight") → template
4. 组装 LLM 输入变量
5. llm.chat_json(system_prompt, user_message) → insight JSON
6. 写 DB（EvolutionRepo.create_insight, analysis_days=days）
7. 写 MD（1天=YYYY-MM-DD.md, 多天=YYYY-MM-DD_YYYY-MM-DD.md）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emily.insight_generator")


class InsightGenerator:
    """周期洞察生成器（支持 1-N 天可变周期）。"""

    def __init__(self, llm_client=None, data_dir: str = ""):
        self._llm = llm_client
        self._data_dir = data_dir or os.environ.get("EMILY_DATA_DIR", "")

    async def generate(self, end_date: str, *, days: int = 1, dry_run: bool = False) -> dict:
        """完整洞察生成流水线。

        Args:
            end_date: 复盘结束日期 YYYY-MM-DD
            days: 复盘天数（默认 1，最小 1）
            dry_run: 预览模式，不调 LLM 不写 DB

        Returns:
            {"end_date": ..., "days": ..., "metrics": ..., "anomalies": ..., "insight": ..., "status": ...}
        """
        # 1-2: 聚合 + 检测
        from scripts.evolution_metrics import collect_metrics
        from scripts.evolution_anomaly import detect_anomalies

        import sys
        _HERE = Path(__file__).resolve().parent
        _SCRIPTS_DIR = _HERE.parent.parent.parent.parent.parent / "scripts"
        if str(_SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS_DIR))

        metrics = await collect_metrics(end_date, days=days)
        anomalies = detect_anomalies(metrics, days=days)

        start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
        start_date = start_dt.strftime("%Y-%m-%d")

        if self._llm is None or dry_run:
            return {
                "end_date": end_date,
                "start_date": start_date,
                "days": days,
                "metrics": metrics,
                "anomalies": anomalies,
                "insight": None,
                "status": "preview",
            }

        # 3-5: LLM 生成
        try:
            from emily_core.infrastructure.llm.prompt_loader import load_prompt

            template = load_prompt("evolution_insight")
            analysis_period = f"{start_date} ~ {end_date}（{days}天）" if days > 1 else end_date

            user_message = template.replace("{analysis_period}", analysis_period)
            user_message = user_message.replace("{metrics_json}", json.dumps(metrics, ensure_ascii=False, default=str))
            user_message = user_message.replace("{anomaly_flags}", json.dumps(anomalies, ensure_ascii=False, default=str))
            user_message = user_message.replace("{sop_distribution}", json.dumps(metrics.get("pipeline", {}).get("sop_distribution", []), ensure_ascii=False))
            user_message = user_message.replace("{fallback_messages}", json.dumps(metrics.get("sop_routing", {}).get("miss_samples", [])[:10], ensure_ascii=False))
            user_message = user_message.replace("{correction_details}", json.dumps(metrics.get("feedback", {}).get("correction_samples", []), ensure_ascii=False))
            user_message = user_message.replace("{rag_misses}", json.dumps(metrics.get("rag", {}).get("zero_hit_samples", []), ensure_ascii=False))
            user_message = user_message.replace("{tool_failures}", json.dumps(metrics.get("tool_calls", {}).get("failure_details", []), ensure_ascii=False))
            user_message = user_message.replace("{node_progress_changes}", json.dumps(metrics.get("project_nodes", {}).get("progress_changes", []), ensure_ascii=False))
            user_message = user_message.replace("{business_events_summary}", json.dumps(metrics.get("business_events", {}), ensure_ascii=False, default=str))

            insight = await self._llm.chat_json(template, user_message)
        except Exception as e:
            logger.error("LLM insight generation failed: %s", e, exc_info=True)
            return {
                "end_date": end_date,
                "start_date": start_date,
                "days": days,
                "metrics": metrics,
                "anomalies": anomalies,
                "insight": None,
                "status": "llm_error",
                "error": str(e),
            }

        # 6: 写 DB
        insight_date_key = f"{start_date}~{end_date}" if days > 1 else end_date
        try:
            from emily_core.repositories.evolution_repo import EvolutionRepo

            await asyncio.to_thread(
                EvolutionRepo.create_insight,
                insight_date_key,
                analysis_days=days,
                total_messages=metrics["pipeline"]["total"],
                total_pipeline_runs=metrics["pipeline"]["total"],
                sop_hit_rate=metrics["pipeline"]["sop_hit_rate"],
                fallback_rate=metrics["pipeline"]["fallback_rate"],
                top_sop_ids=json.dumps(metrics["pipeline"]["sop_distribution"][:5], ensure_ascii=False),
                feedback_summary=json.dumps(metrics.get("feedback", {}).get("type_distribution", []), ensure_ascii=False),
                anomaly_flags=json.dumps([a["flag"] for a in anomalies], ensure_ascii=False),
                insight_text=json.dumps(insight, ensure_ascii=False),
                metrics_json=json.dumps(metrics, ensure_ascii=False, default=str),
                health_score=insight.get("health_score", 0),
            )
        except Exception as e:
            logger.error("Failed to write insight to DB: %s", e, exc_info=True)

        # 7: 写 MD
        try:
            self._write_md(start_date, end_date, days, insight, metrics, anomalies)
        except Exception as e:
            logger.error("Failed to write insight MD: %s", e, exc_info=True)

        return {
            "end_date": end_date,
            "start_date": start_date,
            "days": days,
            "metrics": metrics,
            "anomalies": anomalies,
            "insight": insight,
            "status": "generated",
        }

    def _write_md(self, start_date: str, end_date: str, days: int, insight: dict, metrics: dict, anomalies: list) -> None:
        """写 emily-data/evolution/insights/YYYY-MM-DD.md"""

        # 确定数据目录
        data_dir = self._data_dir
        if not data_dir:
            # 自动查找 emily-data
            candidate = Path(__file__).resolve().parent.parent.parent.parent.parent / "emily-data"
            if candidate.exists():
                data_dir = str(candidate)

        if not data_dir:
            logger.warning("Cannot determine emily-data directory, skipping MD output")
            return

        insights_dir = Path(data_dir) / "evolution" / "insights"
        insights_dir.mkdir(parents=True, exist_ok=True)

        if days > 1:
            filename = f"{start_date}_{end_date}.md"
        else:
            filename = f"{end_date}.md"

        p = metrics["pipeline"]

        # 构建 Markdown 内容
        lines = []
        period_label = f"{end_date}（{days}天）" if days > 1 else end_date
        lines.append(f"# 洞察报告 — {period_label}")
        lines.append("")
        lines.append("## 运行概览")
        lines.append(f"- 处理消息：{p['total']} 条")
        lines.append(f"- Pipeline 执行：{p['total']} 次")
        lines.append(f"- SOP 命中率：{p['sop_hit_rate']:.0%} | Fallback 率：{p['fallback_rate']:.0%}")
        lines.append(f"- 健康评分：{insight.get('health_score', 0)}/100")
        lines.append("")

        if anomalies:
            lines.append("## 异常信号")
            for a in anomalies:
                sev = "HIGH" if a["severity"] == "high" else "MED"
                lines.append(f"- [{sev}] {a['flag']}：{a['message']}")
            lines.append("")

        if insight.get("key_findings"):
            lines.append("## 关键发现")
            for i, f in enumerate(insight["key_findings"], 1):
                lines.append(f"{i}. [{f.get('category', '')}] {f.get('finding', '')}")
            lines.append("")

        if insight.get("node_review"):
            nr = insight["node_review"]
            lines.append("## 节点推进")
            if nr.get("progress_highlights"):
                for h in nr["progress_highlights"]:
                    lines.append(f"- {h}")
            if nr.get("overdue_risks"):
                for r in nr["overdue_risks"]:
                    lines.append(f"- {r}")
            lines.append("")

        if insight.get("improvement_suggestions"):
            lines.append("## 改进建议")
            for s in insight["improvement_suggestions"]:
                lines.append(f"- [{s.get('target', '')}|{s.get('priority', '')}] {s.get('suggestion', '')}")
            lines.append("")

        if insight.get("summary"):
            lines.append(f"## 总结")
            lines.append(insight["summary"])
            lines.append("")

        filepath = insights_dir / filename
        filepath.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Insight MD written to %s", filepath)
