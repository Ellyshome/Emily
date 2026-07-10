"""RuleInductor — 进化规则归纳服务。

从近 N 天洞察中归纳进化规则。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("emily.rule_inductor")


class RuleInductor:
    """进化规则归纳器。"""

    CATEGORY_TARGET_MAP = {
        "prompt": "prompts/session.md",
        "sop": "sops/",
        "skill": "skills/",
        "hook": "config/hook_config.json",
        "user_memory": "user_memory/",
        "routing": "prompts/session.md",
    }

    def __init__(self, llm_client=None, data_dir: str = ""):
        self._llm = llm_client
        self._data_dir = data_dir or os.environ.get("EMILY_DATA_DIR", "")

    async def induct(self, end_date: str, *, days: int = 7, dry_run: bool = False) -> list[dict]:
        """规则归纳流水线。

        Args:
            end_date: 分析截止日期
            days: 回顾天数（默认 7）
            dry_run: 预览模式

        Returns:
            归纳出的规则列表
        """
        from emily_core.repositories.evolution_repo import EvolutionRepo

        start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
        start_date = start_dt.strftime("%Y-%m-%d")

        # 1. 查近 N 天 Insight
        insights = await asyncio.to_thread(
            EvolutionRepo.get_insights_range, start_date, end_date
        )

        if not insights:
            logger.info("No insights found in range %s ~ %s", start_date, end_date)
            return []

        # 2. 计算趋势数据
        trend_data = self._compute_trends(insights)
        recurring_anomalies = self._find_recurring_anomalies(insights)

        insights_data = []
        for ins in insights:
            insight_json = {}
            try:
                insight_json = json.loads(ins.insight_text) if ins.insight_text else {}
            except json.JSONDecodeError:
                pass
            insights_data.append({
                "date": ins.insight_date,
                "sop_hit_rate": ins.sop_hit_rate,
                "fallback_rate": ins.fallback_rate,
                "health_score": ins.health_score,
                "summary": insight_json.get("summary", ""),
                "key_findings": insight_json.get("key_findings", []),
            })

        if not self._llm or dry_run:
            logger.info("Preview mode: %d insights loaded, %d recurring anomalies", len(insights), len(recurring_anomalies))
            return [{"status": "preview", "trend_data": trend_data, "recurring_anomalies": recurring_anomalies}]

        # 3. LLM 归纳
        try:
            from emily_core.infrastructure.llm.prompt_loader import load_prompt

            template = load_prompt("evolution_rule")
            date_range = f"{start_date} ~ {end_date}（{days}天）"

            user_message = template.replace("{date_range}", date_range)
            user_message = user_message.replace("{insights_json}", json.dumps(insights_data, ensure_ascii=False, default=str))
            user_message = user_message.replace("{trend_data}", json.dumps(trend_data, ensure_ascii=False))
            user_message = user_message.replace("{recurring_anomalies}", json.dumps(recurring_anomalies, ensure_ascii=False))
            # Also handle the {days} variable in the template
            user_message = user_message.replace("{days}", str(days))

            result = await self._llm.chat_json(template, user_message)
            rules = result.get("rules", [])
        except Exception as e:
            logger.error("LLM rule induction failed: %s", e, exc_info=True)
            return [{"status": "llm_error", "error": str(e)}]

        # 4. 写 DB + YAML
        saved_rules = []
        for rule in rules:
            try:
                rule_no = await asyncio.to_thread(EvolutionRepo.generate_rule_no)
                await asyncio.to_thread(
                    EvolutionRepo.create_rule,
                    rule_no,
                    rule.get("title", ""),
                    description=rule.get("description", ""),
                    evidence_insight_ids=json.dumps(rule.get("evidence_dates", []), ensure_ascii=False),
                    category=rule.get("category", ""),
                    confidence=rule.get("confidence", 0.0),
                    suggested_action=rule.get("suggested_action", ""),
                    impact_estimate=rule.get("impact_estimate", ""),
                )
                rule["rule_no"] = rule_no
                saved_rules.append(rule)

                # 写 YAML
                self._write_yaml(rule_no, rule)
            except Exception as e:
                logger.error("Failed to save rule: %s", e, exc_info=True)

        return saved_rules

    def _compute_trends(self, insights) -> dict:
        """计算关键指标趋势。"""
        if not insights:
            return {}
        dates = [i.insight_date for i in insights]
        sop_rates = [i.sop_hit_rate for i in insights]
        fallback_rates = [i.fallback_rate for i in insights]
        health_scores = [i.health_score for i in insights]

        return {
            "period_dates": dates,
            "sop_hit_rate_trend": sop_rates,
            "fallback_rate_trend": fallback_rates,
            "health_score_trend": health_scores,
            "sop_hit_rate_change": round(sop_rates[-1] - sop_rates[0], 4) if len(sop_rates) > 1 else 0,
        }

    def _find_recurring_anomalies(self, insights) -> list[dict]:
        """找重复出现的异常信号。"""
        anomaly_map = {}
        for ins in insights:
            flags = []
            try:
                flags = json.loads(ins.anomaly_flags) if ins.anomaly_flags else []
            except json.JSONDecodeError:
                pass
            for flag in flags:
                if flag not in anomaly_map:
                    anomaly_map[flag] = []
                anomaly_map[flag].append(ins.insight_date)

        recurring = []
        for flag, dates in anomaly_map.items():
            if len(dates) >= 2:
                recurring.append({"flag": flag, "dates": dates, "count": len(dates)})

        return recurring

    def _write_yaml(self, rule_no: str, rule: dict) -> None:
        """写 emily-data/evolution/rules/R-NNN.yaml"""
        data_dir = self._data_dir
        if not data_dir:
            candidate = Path(__file__).resolve().parent.parent.parent.parent.parent / "emily-data"
            if candidate.exists():
                data_dir = str(candidate)

        if not data_dir:
            return

        rules_dir = Path(data_dir) / "evolution" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)

        yaml_lines = [
            f"rule_no: {rule_no}",
            f"title: \"{rule.get('title', '')}\"",
            f"description: |",
        ]
        desc = rule.get("description", "")
        for line in desc.split("\n"):
            yaml_lines.append(f"  {line}")
        yaml_lines.append(f"category: {rule.get('category', '')}")
        yaml_lines.append(f"confidence: {rule.get('confidence', 0.0)}")
        yaml_lines.append(f"evidence:")
        for d in rule.get("evidence_dates", []):
            yaml_lines.append(f"  - date: \"{d}\"")
        yaml_lines.append(f"suggested_action: \"{rule.get('suggested_action', '')}\"")
        yaml_lines.append(f"impact_estimate: \"{rule.get('impact_estimate', '')}\"")
        yaml_lines.append(f"status: DRAFT")
        yaml_lines.append(f"created_at: \"{datetime.now().isoformat()}\"")

        filepath = rules_dir / f"{rule_no}.yaml"
        filepath.write_text("\n".join(yaml_lines), encoding="utf-8")
