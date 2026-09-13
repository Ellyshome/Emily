"""ConsoleLogRepo —— 控制台「日志聚合」只读查询（多表统一出口）。

只读展示能力下沉到 repo 层，避免 API 路由层直接拼 SQL。
模块白名单 table/time/user/summary 均为硬编码，杜绝注入。
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.console_log_repo")

# 模块白名单：key 供 API/前端使用；user=None 表示该日志不归属具体用户（按人过滤时跳过）。
LOG_MODULES = [
    dict(key="node_events", label="节点事件", table="node_events",
         id_field="id", time="created_at", user="operator_id",
         summary="CONCAT_WS(' | ', event_type, remark)"),
    dict(key="business_event_logs", label="业务事件", table="business_event_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', event_action, event_category, summary, target_no, user_name)"),
    dict(key="pipeline_execution_logs", label="Pipeline 执行", table="pipeline_execution_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', matched_sop_id, final_status, abort_reason)"),
    dict(key="hook_execution_logs", label="Hook 执行", table="hook_execution_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', hook_name, mount_point, decision, block_reason)"),
    dict(key="sop_routing_logs", label="SOP 路由", table="sop_routing_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', matched_sop_id, match_confidence, execution_result, message_content)"),
    dict(key="agent_reasoning_logs", label="Agent 推理", table="agent_reasoning_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', matched_sop_id, execution_result, reply_preview)"),
    dict(key="llm_interaction_logs", label="LLM 交互", table="llm_interaction_logs",
         id_field="id", time="created_at", user=None,
         summary="CONCAT_WS(' | ', model, call_type, response_type, finish_reason, prompt_summary)"),
    dict(key="evolution_llm_interaction_logs", label="进化 LLM 交互", table="evolution_llm_interaction_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', call_category, model, response_type, error_summary)"),
    dict(key="tool_call_logs", label="工具调用", table="tool_call_logs",
         id_field="id", time="created_at", user=None,
         summary="CONCAT_WS(' | ', tool_name, CASE WHEN is_success THEN '成功' ELSE '失败' END, error_message)"),
    dict(key="rag_retrieval_logs", label="RAG 检索", table="rag_retrieval_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', query_text, provider, '命中' || hit_count, error_summary)"),
    dict(key="session_lifecycle_logs", label="Session 生命周期", table="session_lifecycle_logs",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', event_type, '消息数' || message_count)"),
    dict(key="session_archives", label="会话归档", table="session_archives",
         id_field="id", time="archived_at", user="user_id",
         summary="CONCAT_WS(' | ', archive_reason, '轮次' || turn_count, user_name)"),
    dict(key="scheduler_job_logs", label="调度器作业", table="scheduler_job_logs",
         id_field="id", time="created_at", user=None,
         summary="CONCAT_WS(' | ', action_type, CASE WHEN success THEN '成功' ELSE '失败' END, summary)"),
    dict(key="permission_audit_log", label="权限审计", table="permission_audit_log",
         id_field="log_id", time="event_time", user="grantor_id",
         summary="CONCAT_WS(' | ', operation_type, perm_code, grant_type, remark)"),
    dict(key="user_feedback_signals", label="用户反馈信号", table="user_feedback_signals",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', signal_type, trigger_message)"),
    dict(key="events", label="事件 Event", table="events",
         id_field="id", time="created_at", user="user_id",
         summary="CONCAT_WS(' | ', event_type, title, status)"),
]


class ConsoleLogRepo:
    """聚合查看各类日志的只读 Repository。"""

    @staticmethod
    def _query_module(session, m: dict, user_id: str, limit: int) -> list[dict]:
        user_clause = ""
        params: dict = {"lim": limit}
        if user_id and m["user"]:
            user_clause = f"WHERE {m['user']} = :uid"
            params["uid"] = user_id
        uid_expr = m["user"] or "NULL"
        sql = text(
            f"SELECT {m['id_field']} AS id, {m['time']} AS t, {uid_expr} AS uid, "
            f"({m['summary']}) AS summary "
            f"FROM {m['table']} {user_clause} ORDER BY {m['time']} DESC LIMIT :lim"
        )
        rows = []
        for r in session.execute(sql, params).mappings():
            rows.append({
                "id": f"{m['key']}:{r['id']}",
                "module": m["key"],
                "module_name": m["label"],
                "time": r["t"] or "",
                "user_id": r["uid"] or "",
                "summary": (r["summary"] or "").strip(),
            })
        return rows

    @staticmethod
    def query_aggregated(user_id: str, module: str, limit: int) -> list[dict]:
        """按「人 / 记录模块」聚合各类日志，返回按时间倒序的扁平列表。"""
        modules = [m for m in LOG_MODULES if not module or m["key"] == module]
        collected: list[dict] = []
        with get_session() as session:
            for m in modules:
                if user_id and not m["user"]:
                    continue
                try:
                    collected.extend(ConsoleLogRepo._query_module(session, m, user_id, limit))
                except Exception as ex:
                    logger.warning("console_log_repo query failed module=%s: %s", m["key"], ex)
        collected.sort(key=lambda r: r["time"] or "", reverse=True)
        return collected[:limit]
