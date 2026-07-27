"""EvolutionRepo — 进化闭环数据访问层。

提供 3 张进化表的 CRUD + 10 个数据源的聚合查询。
遵循项目约定：纯 @staticmethod，可选 session 参数，_impl 内函数。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, text, or_
from sqlalchemy.orm import Session

from emily_core.infrastructure.database.models import (
    BEIJING_TZ,
    EvolutionDailyInsight,
    EvolutionRule,
    EvolutionPatch,
    PipelineExecutionLog,
    SOPRoutingLog,
    UserFeedbackSignal,
    RAGRetrievalLog,
    BusinessEventLog,
    SessionLifecycleLog,
    AgentReasoningLog,
    ToolCallLog,
    ProjectNode,
    Task,
    User,
    _new_uuid,
    _utc_now,
)
from emily_core.infrastructure.database.session import get_session

logger = logging.getLogger("emily.evolution_repo")


class EvolutionRepo:
    """进化闭环数据访问。"""

    # ════════════════════════════════════════════════════════════════════════════
    # 日洞察 CRUD
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def create_insight(
        insight_date: str,
        *,
        analysis_days: int = 1,
        total_messages: int = 0,
        total_pipeline_runs: int = 0,
        sop_hit_rate: float = 0.0,
        fallback_rate: float = 0.0,
        top_sop_ids: str = "[]",
        feedback_summary: str = "",
        anomaly_flags: str = "[]",
        insight_text: str = "",
        metrics_json: str = "{}",
        health_score: int = 0,
        session: Optional[Session] = None,
    ) -> EvolutionDailyInsight:
        def _impl(sess: Session) -> EvolutionDailyInsight:
            # 若已存在则更新
            existing = sess.query(EvolutionDailyInsight).filter(
                EvolutionDailyInsight.insight_date == insight_date
            ).first()
            if existing:
                existing.analysis_days = analysis_days
                existing.total_messages = total_messages
                existing.total_pipeline_runs = total_pipeline_runs
                existing.sop_hit_rate = sop_hit_rate
                existing.fallback_rate = fallback_rate
                existing.top_sop_ids = top_sop_ids
                existing.feedback_summary = feedback_summary
                existing.anomaly_flags = anomaly_flags
                existing.insight_text = insight_text
                existing.metrics_json = metrics_json
                existing.health_score = health_score
                sess.flush()
                return existing
            row = EvolutionDailyInsight(
                insight_date=insight_date,
                analysis_days=analysis_days,
                total_messages=total_messages,
                total_pipeline_runs=total_pipeline_runs,
                sop_hit_rate=sop_hit_rate,
                fallback_rate=fallback_rate,
                top_sop_ids=top_sop_ids,
                feedback_summary=feedback_summary,
                anomaly_flags=anomaly_flags,
                insight_text=insight_text,
                metrics_json=metrics_json,
                health_score=health_score,
            )
            sess.add(row)
            sess.flush()
            return row

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_insight_by_date(date: str, *, session: Optional[Session] = None) -> Optional[EvolutionDailyInsight]:
        def _impl(sess: Session):
            return sess.query(EvolutionDailyInsight).filter(
                EvolutionDailyInsight.insight_date == date
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_insights_range(start_date: str, end_date: str, *, session: Optional[Session] = None) -> list[EvolutionDailyInsight]:
        def _impl(sess: Session):
            return sess.query(EvolutionDailyInsight).filter(
                EvolutionDailyInsight.insight_date >= start_date,
                EvolutionDailyInsight.insight_date <= end_date,
            ).order_by(EvolutionDailyInsight.insight_date).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ════════════════════════════════════════════════════════════════════════════
    # 进化规则 CRUD
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def generate_rule_no(*, session: Optional[Session] = None) -> str:
        def _impl(sess: Session) -> str:
            count = sess.query(EvolutionRule).filter(
                EvolutionRule.rule_no.like("R-%")
            ).count()
            return f"R-{count + 1:03d}"

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def create_rule(
        rule_no: str,
        title: str,
        *,
        description: str = "",
        evidence_insight_ids: str = "[]",
        category: str = "",
        confidence: float = 0.0,
        status: str = "DRAFT",
        suggested_action: str = "",
        impact_estimate: str = "",
        session: Optional[Session] = None,
    ) -> EvolutionRule:
        def _impl(sess: Session) -> EvolutionRule:
            row = EvolutionRule(
                rule_no=rule_no,
                title=title,
                description=description,
                evidence_insight_ids=evidence_insight_ids,
                category=category,
                confidence=confidence,
                status=status,
                suggested_action=suggested_action,
                impact_estimate=impact_estimate,
            )
            sess.add(row)
            sess.flush()
            return row

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_rule_by_no(rule_no: str, *, session: Optional[Session] = None) -> Optional[EvolutionRule]:
        def _impl(sess: Session):
            return sess.query(EvolutionRule).filter(
                EvolutionRule.rule_no == rule_no
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_rules_by_status(status: str, *, session: Optional[Session] = None) -> list[EvolutionRule]:
        def _impl(sess: Session):
            return sess.query(EvolutionRule).filter(
                EvolutionRule.status == status
            ).order_by(EvolutionRule.created_at).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_rule_status(rule_no: str, status: str, *, confirmed_at: str = "", superseded_by: str = "", session: Optional[Session] = None) -> bool:
        def _impl(sess: Session) -> bool:
            row = sess.query(EvolutionRule).filter(
                EvolutionRule.rule_no == rule_no
            ).first()
            if row is None:
                return False
            row.status = status
            if confirmed_at:
                row.confirmed_at = confirmed_at
            if superseded_by:
                row.superseded_by = superseded_by
            sess.flush()
            return True

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ════════════════════════════════════════════════════════════════════════════
    # 进化补丁 CRUD
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def generate_patch_no(*, session: Optional[Session] = None) -> str:
        def _impl(sess: Session) -> str:
            count = sess.query(EvolutionPatch).filter(
                EvolutionPatch.patch_no.like("EP-%")
            ).count()
            return f"EP-{count + 1:03d}"

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def create_patch(
        patch_no: str,
        rule_no: str,
        *,
        target_type: str = "",
        target_path: str = "",
        patch_content: str = "",
        patch_type: str = "",
        search_anchor: str = "",
        risk_level: str = "",
        risk_reasoning: str = "",
        validation_criteria: str = "",
        expected_effect: str = "",
        status: str = "DRAFT",
        session: Optional[Session] = None,
    ) -> EvolutionPatch:
        def _impl(sess: Session) -> EvolutionPatch:
            row = EvolutionPatch(
                patch_no=patch_no,
                rule_no=rule_no,
                target_type=target_type,
                target_path=target_path,
                patch_content=patch_content,
                patch_type=patch_type,
                search_anchor=search_anchor,
                risk_level=risk_level,
                risk_reasoning=risk_reasoning,
                validation_criteria=validation_criteria,
                expected_effect=expected_effect,
                status=status,
            )
            sess.add(row)
            sess.flush()
            return row

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_patch_by_no(patch_no: str, *, session: Optional[Session] = None) -> Optional[EvolutionPatch]:
        def _impl(sess: Session):
            return sess.query(EvolutionPatch).filter(
                EvolutionPatch.patch_no == patch_no
            ).first()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_patches_by_status(status: str, *, session: Optional[Session] = None) -> list[EvolutionPatch]:
        def _impl(sess: Session):
            return sess.query(EvolutionPatch).filter(
                EvolutionPatch.status == status
            ).order_by(EvolutionPatch.created_at).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def update_patch_status(patch_no: str, status: str, *, applied_at: str = "", validated_at: str = "", validation_result: str = "", rollback_snapshot: str = "", session: Optional[Session] = None) -> bool:
        def _impl(sess: Session) -> bool:
            row = sess.query(EvolutionPatch).filter(
                EvolutionPatch.patch_no == patch_no
            ).first()
            if row is None:
                return False
            row.status = status
            if applied_at:
                row.applied_at = applied_at
            if validated_at:
                row.validated_at = validated_at
            if validation_result:
                row.validation_result = validation_result
            if rollback_snapshot:
                row.rollback_snapshot = rollback_snapshot
            sess.flush()
            return True

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ════════════════════════════════════════════════════════════════════════════
    # 指标聚合查询（10 个数据源）
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _date_range_filter(start_date: str, end_date: str):
        """生成日期范围过滤的 start/end datetime 字符串。"""
        start = f"{start_date}T00:00:00"
        end_dt = datetime.fromisoformat(end_date) + timedelta(days=1)
        end = end_dt.isoformat()
        return start, end

    @staticmethod
    def aggregate_pipeline_logs(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 A：Pipeline 执行日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            total = sess.query(func.count(PipelineExecutionLog.id)).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
            ).scalar() or 0

            hit = sess.query(func.count(PipelineExecutionLog.id)).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
                PipelineExecutionLog.is_fallback == False,
            ).scalar() or 0

            fallback = total - hit
            sop_hit_rate = hit / total if total > 0 else 0.0
            fallback_rate = fallback / total if total > 0 else 0.0

            sop_rows = sess.query(
                PipelineExecutionLog.matched_sop_id,
                func.count(PipelineExecutionLog.id).label("cnt"),
            ).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
                PipelineExecutionLog.is_fallback == False,
                PipelineExecutionLog.matched_sop_id != "",
            ).group_by(PipelineExecutionLog.matched_sop_id).order_by(text("cnt DESC")).limit(10).all()

            status_rows = sess.query(
                PipelineExecutionLog.final_status,
                func.count(PipelineExecutionLog.id).label("cnt"),
            ).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
            ).group_by(PipelineExecutionLog.final_status).all()

            avg_elapsed = sess.query(func.avg(PipelineExecutionLog.elapsed_ms)).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
            ).scalar() or 0

            max_elapsed = sess.query(func.max(PipelineExecutionLog.elapsed_ms)).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
            ).scalar() or 0

            blocked = sess.query(func.count(PipelineExecutionLog.id)).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
                PipelineExecutionLog.was_blocked == True,
            ).scalar() or 0

            conf_rows = sess.query(
                PipelineExecutionLog.match_confidence,
                func.count(PipelineExecutionLog.id).label("cnt"),
            ).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
            ).group_by(PipelineExecutionLog.match_confidence).all()

            failed_rows = sess.query(
                PipelineExecutionLog.final_status,
                PipelineExecutionLog.abort_reason,
            ).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
                PipelineExecutionLog.final_status.in_(["FAILED", "ABORTED"]),
            ).limit(20).all()

            low_conf = sess.query(PipelineExecutionLog.intent_reasoning).filter(
                PipelineExecutionLog.created_at >= start,
                PipelineExecutionLog.created_at < end,
                PipelineExecutionLog.match_confidence.in_(["low", "none"]),
                PipelineExecutionLog.intent_reasoning != "",
            ).limit(10).all()

            return {
                "total": total,
                "hit": hit,
                "fallback": fallback,
                "sop_hit_rate": round(sop_hit_rate, 4),
                "fallback_rate": round(fallback_rate, 4),
                "sop_distribution": [{"sop_id": r.matched_sop_id, "count": r.cnt} for r in sop_rows],
                "status_distribution": {r.final_status: r.cnt for r in status_rows},
                "avg_elapsed_ms": int(avg_elapsed),
                "max_elapsed_ms": int(max_elapsed),
                "blocked": blocked,
                "confidence_distribution": {r.match_confidence: r.cnt for r in conf_rows},
                "abort_reasons": [{"status": r.final_status, "reason": r.abort_reason} for r in failed_rows],
                "low_confidence_reasons": [r.intent_reasoning for r in low_conf],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_sop_routing_logs(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 B：SOP 路由日志聚合（按 log_date 范围过滤）。"""
        def _impl(sess: Session) -> dict:
            not_hit = sess.query(func.count(SOPRoutingLog.id)).filter(
                SOPRoutingLog.log_date >= start_date,
                SOPRoutingLog.log_date <= end_date,
                SOPRoutingLog.is_hit == False,
            ).scalar() or 0

            miss_samples = sess.query(
                SOPRoutingLog.message_content,
                SOPRoutingLog.llm_reasoning,
                SOPRoutingLog.match_confidence,
            ).filter(
                SOPRoutingLog.log_date >= start_date,
                SOPRoutingLog.log_date <= end_date,
                SOPRoutingLog.is_hit == False,
            ).order_by(SOPRoutingLog.created_at.desc()).limit(10).all()

            fallback_rows = sess.query(
                SOPRoutingLog.fallback_action,
                func.count(SOPRoutingLog.id).label("cnt"),
            ).filter(
                SOPRoutingLog.log_date >= start_date,
                SOPRoutingLog.log_date <= end_date,
                SOPRoutingLog.is_hit == False,
            ).group_by(SOPRoutingLog.fallback_action).all()

            return {
                "not_hit_count": not_hit,
                "miss_samples": [
                    {"message": r.message_content, "reasoning": r.llm_reasoning, "confidence": r.match_confidence}
                    for r in miss_samples
                ],
                "fallback_distribution": {r.fallback_action: r.cnt for r in fallback_rows},
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_feedback_signals(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 C：用户反馈信号聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            type_rows = sess.query(
                UserFeedbackSignal.signal_type,
                func.count(UserFeedbackSignal.id).label("cnt"),
                func.avg(UserFeedbackSignal.signal_strength).label("avg_strength"),
            ).filter(
                UserFeedbackSignal.created_at >= start,
                UserFeedbackSignal.created_at < end,
            ).group_by(UserFeedbackSignal.signal_type).all()

            corrections = sess.query(
                UserFeedbackSignal.trigger_message,
                UserFeedbackSignal.context_summary,
            ).filter(
                UserFeedbackSignal.created_at >= start,
                UserFeedbackSignal.created_at < end,
                UserFeedbackSignal.signal_type == "explicit_correction",
            ).order_by(UserFeedbackSignal.signal_strength.desc()).limit(5).all()

            return {
                "type_distribution": [
                    {"type": r.signal_type, "count": r.cnt, "avg_strength": round(float(r.avg_strength or 0), 2)}
                    for r in type_rows
                ],
                "correction_samples": [
                    {"message": r.trigger_message, "context": r.context_summary}
                    for r in corrections
                ],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_rag_logs(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 D：RAG 检索日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            total = sess.query(func.count(RAGRetrievalLog.id)).filter(
                RAGRetrievalLog.created_at >= start,
                RAGRetrievalLog.created_at < end,
            ).scalar() or 0

            hit = sess.query(func.count(RAGRetrievalLog.id)).filter(
                RAGRetrievalLog.created_at >= start,
                RAGRetrievalLog.created_at < end,
                RAGRetrievalLog.hit_count > 0,
            ).scalar() or 0

            avg_top_score = sess.query(func.avg(RAGRetrievalLog.top_score)).filter(
                RAGRetrievalLog.created_at >= start,
                RAGRetrievalLog.created_at < end,
            ).scalar() or 0

            avg_latency = sess.query(func.avg(RAGRetrievalLog.latency_ms)).filter(
                RAGRetrievalLog.created_at >= start,
                RAGRetrievalLog.created_at < end,
            ).scalar() or 0

            zero_hit = sess.query(
                RAGRetrievalLog.query_text,
                RAGRetrievalLog.provider,
            ).filter(
                RAGRetrievalLog.created_at >= start,
                RAGRetrievalLog.created_at < end,
                RAGRetrievalLog.hit_count == 0,
            ).order_by(RAGRetrievalLog.created_at.desc()).limit(10).all()

            return {
                "total": total,
                "hit": hit,
                "zero_hit_count": total - hit,
                "zero_hit_rate": round((total - hit) / total, 4) if total > 0 else 0.0,
                "avg_top_score": round(float(avg_top_score), 4),
                "avg_latency_ms": int(avg_latency),
                "zero_hit_samples": [{"query": r.query_text, "provider": r.provider} for r in zero_hit],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_business_events(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 E：业务事件日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            cat_rows = sess.query(
                BusinessEventLog.event_category,
                func.count(BusinessEventLog.id).label("cnt"),
            ).filter(
                BusinessEventLog.created_at >= start,
                BusinessEventLog.created_at < end,
            ).group_by(BusinessEventLog.event_category).all()

            action_rows = sess.query(
                BusinessEventLog.event_action,
                func.count(BusinessEventLog.id).label("cnt"),
            ).filter(
                BusinessEventLog.created_at >= start,
                BusinessEventLog.created_at < end,
            ).group_by(BusinessEventLog.event_action).all()

            user_rows = sess.query(
                BusinessEventLog.user_name,
                func.count(BusinessEventLog.id).label("cnt"),
            ).filter(
                BusinessEventLog.created_at >= start,
                BusinessEventLog.created_at < end,
                BusinessEventLog.user_name != "",
            ).group_by(BusinessEventLog.user_name).order_by(text("cnt DESC")).limit(10).all()

            return {
                "category_distribution": {r.event_category: r.cnt for r in cat_rows},
                "action_distribution": {r.event_action: r.cnt for r in action_rows},
                "top_users": [{"name": r.user_name, "count": r.cnt} for r in user_rows],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_session_lifecycle(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 F：Session 生命周期日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            created = sess.query(func.count(SessionLifecycleLog.id)).filter(
                SessionLifecycleLog.created_at >= start,
                SessionLifecycleLog.created_at < end,
                SessionLifecycleLog.event_type == "created",
            ).scalar() or 0

            archived_rows = sess.query(
                func.count(SessionLifecycleLog.id).label("cnt"),
                func.sum(SessionLifecycleLog.message_count).label("total_msgs"),
                func.avg(SessionLifecycleLog.duration_ms).label("avg_duration"),
            ).filter(
                SessionLifecycleLog.created_at >= start,
                SessionLifecycleLog.created_at < end,
                SessionLifecycleLog.event_type == "archived",
            ).first()

            return {
                "sessions_created": created,
                "sessions_archived": archived_rows.cnt if archived_rows else 0,
                "total_messages_in_archived": int(archived_rows.total_msgs or 0) if archived_rows else 0,
                "avg_duration_ms": int(archived_rows.avg_duration or 0) if archived_rows else 0,
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_agent_reasoning(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 G：Agent 推理日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            fallback = sess.query(func.count(AgentReasoningLog.id)).filter(
                AgentReasoningLog.created_at >= start,
                AgentReasoningLog.created_at < end,
                AgentReasoningLog.fallback == True,
            ).scalar() or 0

            result_rows = sess.query(
                AgentReasoningLog.execution_result,
                func.count(AgentReasoningLog.id).label("cnt"),
            ).filter(
                AgentReasoningLog.created_at >= start,
                AgentReasoningLog.created_at < end,
            ).group_by(AgentReasoningLog.execution_result).all()

            avg_iterations = sess.query(func.avg(AgentReasoningLog.iteration_count)).filter(
                AgentReasoningLog.created_at >= start,
                AgentReasoningLog.created_at < end,
            ).scalar() or 0

            max_iter_reached = sess.query(func.count(AgentReasoningLog.id)).filter(
                AgentReasoningLog.created_at >= start,
                AgentReasoningLog.created_at < end,
                AgentReasoningLog.max_iterations_reached == True,
            ).scalar() or 0

            errors = sess.query(AgentReasoningLog.error_message).filter(
                AgentReasoningLog.created_at >= start,
                AgentReasoningLog.created_at < end,
                AgentReasoningLog.execution_result == "failed",
                AgentReasoningLog.error_message != "",
            ).limit(10).all()

            return {
                "fallback_count": fallback,
                "result_distribution": {r.execution_result: r.cnt for r in result_rows},
                "avg_iterations": round(float(avg_iterations), 2),
                "max_iterations_reached": max_iter_reached,
                "error_samples": [r.error_message for r in errors],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_tool_calls(start_date: str, end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 H：工具调用日志聚合。"""
        def _impl(sess: Session) -> dict:
            start, end = EvolutionRepo._date_range_filter(start_date, end_date)

            tool_rows = sess.query(
                ToolCallLog.tool_name,
                func.count(ToolCallLog.id).label("cnt"),
            ).filter(
                ToolCallLog.created_at >= start,
                ToolCallLog.created_at < end,
            ).group_by(ToolCallLog.tool_name).order_by(text("cnt DESC")).all()

            fail_rows = sess.query(
                ToolCallLog.tool_name,
                func.count(ToolCallLog.id).label("cnt"),
                ToolCallLog.error_message,
            ).filter(
                ToolCallLog.created_at >= start,
                ToolCallLog.created_at < end,
                ToolCallLog.is_success == False,
            ).group_by(ToolCallLog.tool_name, ToolCallLog.error_message).all()

            return {
                "tool_distribution": {r.tool_name: r.cnt for r in tool_rows},
                "failure_details": [
                    {"tool": r.tool_name, "count": r.cnt, "error": r.error_message}
                    for r in fail_rows
                ],
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def aggregate_project_nodes(date_str: str = "", *, session: Optional[Session] = None) -> dict:
        """数据源 I：项目节点聚合。date_str 仅用于当日进度变化过滤，为空时跳过。"""
        def _impl(sess: Session) -> dict:
            status_rows = sess.query(
                ProjectNode.status,
                func.count(ProjectNode.id).label("cnt"),
            ).filter(
                ProjectNode.is_discarded == False,
            ).group_by(ProjectNode.status).all()

            type_rows = sess.query(
                ProjectNode.node_type,
                func.count(ProjectNode.id).label("cnt"),
            ).filter(
                ProjectNode.is_discarded == False,
            ).group_by(ProjectNode.node_type).all()

            now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
            future_str = (datetime.now(BEIJING_TZ) + timedelta(days=7)).strftime("%Y-%m-%d")

            overdue = sess.query(ProjectNode).filter(
                ProjectNode.status == "IN_PROGRESS",
                ProjectNode.is_discarded == False,
                ProjectNode.deadline < now_str,
            ).all()

            upcoming = sess.query(ProjectNode).filter(
                ProjectNode.status == "IN_PROGRESS",
                ProjectNode.is_discarded == False,
                ProjectNode.deadline >= now_str,
                ProjectNode.deadline < future_str,
            ).all()

            progress_changes = []
            if date_str:
                start = f"{date_str}T00:00:00"
                end_dt = datetime.fromisoformat(date_str) + timedelta(days=1)
                end = end_dt.isoformat()
                updated = sess.query(ProjectNode).filter(
                    ProjectNode.updated_at >= start,
                    ProjectNode.updated_at < end,
                    ProjectNode.is_discarded == False,
                ).all()
                progress_changes = [
                    {"node_id": n.node_id, "name": n.node_name}
                    for n in updated
                ]

            return {
                "status_distribution": {r.status: r.cnt for r in status_rows},
                "type_distribution": {r.node_type: r.cnt for r in type_rows},
                "overdue_nodes": [{"node_id": n.node_id, "name": n.node_name, "deadline": n.deadline} for n in overdue],
                "upcoming_deadlines": [{"node_id": n.node_id, "name": n.node_name, "deadline": n.deadline} for n in upcoming],
                "progress_changes": progress_changes,
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ════════════════════════════════════════════════════════════════════════════
    # 第10数据源：认知偏差聚合
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def aggregate_cognition_drift(end_date: str, *, session: Optional[Session] = None) -> dict:
        """数据源 J：认知偏差聚合 —— 扫描所有项目世界书的 stale 状态。

        对每个有世界书的项目运行 CognitionDriftDetector.detect()，
        汇总项目认知健康度指标。
        """
        from emily_core.services.cognition_drift_detector import CognitionDriftDetector
        from emily_core.infrastructure.database.models import Project, ProjectWorldBook

        def _impl(sess: Session) -> dict:
            detector = CognitionDriftDetector()

            # 查询所有活跃项目
            all_projects = sess.query(Project).filter(
                or_(Project.is_deleted == False, Project.is_deleted == None),
            ).all()
            all_project_ids = [p.id for p in all_projects]

            # 查询所有世界书
            all_wbs = sess.query(ProjectWorldBook).all()
            wb_map = {wb.project_id: wb for wb in all_wbs}

            project_drifts = []
            projects_with_drift = 0
            total_stale_layers = 0
            stale_layer_distribution: dict[str, int] = {
                "ontology": 0,
                "personnel": 0,
                "structure": 0,
                "temporal": 0,
                "relation": 0,
                "knowledge": 0,
                "introspection": 0,
            }

            for project_id in all_project_ids:
                drift_result = detector.detect(project_id)
                if not drift_result.get("has_world_book"):
                    continue

                has_drift = drift_result.get("has_drift", False)
                stale_layers = drift_result.get("stale_layers", [])

                if has_drift:
                    projects_with_drift += 1
                    total_stale_layers += len(stale_layers)
                    for layer_name in stale_layers:
                        if layer_name in stale_layer_distribution:
                            stale_layer_distribution[layer_name] += 1

                project_drifts.append({
                    "project_id": project_id,
                    "project_name": next((p.name for p in all_projects if p.id == project_id), ""),
                    "has_drift": has_drift,
                    "stale_layers": stale_layers,
                    "drift_summary": {
                        k: {"stale": v.get("stale", False), "signals": v.get("signals", [])}
                        for k, v in drift_result.get("drift", {}).items()
                    },
                })

            projects_with_wb = len(wb_map)
            projects_without_wb = len(all_project_ids) - projects_with_wb

            return {
                "total_projects": len(all_project_ids),
                "projects_with_world_book": projects_with_wb,
                "projects_without_world_book": projects_without_wb,
                "world_book_coverage": round(projects_with_wb / len(all_project_ids), 4) if all_project_ids else 0.0,
                "projects_with_drift": projects_with_drift,
                "drift_rate": round(projects_with_drift / projects_with_wb, 4) if projects_with_wb else 0.0,
                "total_stale_layers": total_stale_layers,
                "avg_stale_layers_per_drifted_project": round(total_stale_layers / projects_with_drift, 2) if projects_with_drift else 0.0,
                "stale_layer_distribution": stale_layer_distribution,
                "project_drifts": project_drifts,
            }

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ════════════════════════════════════════════════════════════════════════════
    # 晨报辅助查询
    # ════════════════════════════════════════════════════════════════════════════

    @staticmethod
    def get_active_users(*, session: Optional[Session] = None) -> list[User]:
        def _impl(sess: Session):
            return sess.query(User).filter(
                User.status == "active",
                User.is_deleted == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_user_pending_tasks(user_id: str, *, session: Optional[Session] = None) -> list[Task]:
        def _impl(sess: Session):
            return sess.query(Task).filter(
                Task.owner_id == user_id,
                Task.status.in_(["WAITING", "SUBMITTED", "RETURNED", "todo", "in_progress"]),
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_user_nodes(user_id: str, *, session: Optional[Session] = None) -> list[ProjectNode]:
        def _impl(sess: Session):
            return sess.query(ProjectNode).filter(
                ProjectNode.responsible_user_id == user_id,
                ProjectNode.status.in_(["IN_PROGRESS", "CONDITIONS_NOT_MET"]),
                ProjectNode.is_discarded == False,
            ).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    @staticmethod
    def get_user_recent_events(user_id: str, date_str: str, *, session: Optional[Session] = None) -> list[BusinessEventLog]:
        def _impl(sess: Session):
            start = f"{date_str}T00:00:00"
            end_dt = datetime.fromisoformat(date_str) + timedelta(days=1)
            end = end_dt.isoformat()
            return sess.query(BusinessEventLog).filter(
                BusinessEventLog.user_id == user_id,
                BusinessEventLog.created_at >= start,
                BusinessEventLog.created_at < end,
            ).order_by(BusinessEventLog.created_at.desc()).limit(10).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
