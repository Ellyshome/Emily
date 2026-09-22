"""ConsoleLogRepo —— 控制台「日志聚合」只读查询（多表统一出口）。

只读展示能力下沉到 repo 层，避免 API 路由层直接拼 SQL。
模块白名单 table/time/user/summary 均为硬编码，杜绝注入。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.console_log_repo")


def _time_sort_key(value: str) -> float:
    """把各日志表的时间字符串归一为可比较的时间戳。

    各表时间列的时区口径并不统一（会话归档为北京时间 +08:00，其余多为 UTC），
    直接按字符串比较会把不同偏移的记录排错，故统一按「时刻」比较。
    """
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).timestamp()
    return dt.timestamp()

# 模块白名单：key 供 API/前端使用；user=None 表示该日志不归属具体用户（按人过滤时跳过）。
# source：该表是否带"流量性质"列（user/ops/auto/test）；仅这三张表有（见操作留痕治理），
#         有该列的模块才支持"排除测试流量"过滤。
LOG_MODULES = [
    dict(key="node_events", label="节点事件", table="node_events",
         id_field="id", time="created_at", user="operator_id",
         summary="CONCAT_WS(' | ', event_type, remark)"),
    dict(key="business_event_logs", label="业务事件", table="business_event_logs",
         id_field="id", time="created_at", user="user_id", source="source",
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
         id_field="id", time="last_active_at", user="user_id",
         summary="CONCAT_WS(' | ', NULLIF(archive_reason, ''), '轮次' || turn_count, "
                 "user_name, CASE WHEN is_guest THEN '访客' ELSE '' END)"),
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
    def _query_module(session, m: dict, user_id: str, limit: int,
                      exclude_sources: list[str] | None = None) -> list[dict]:
        conditions: list[str] = []
        params: dict = {"lim": limit}
        if user_id and m["user"]:
            conditions.append(f"{m['user']} = :uid")
            params["uid"] = user_id
        src_col = m.get("source") or ""
        if exclude_sources and src_col:
            placeholders = []
            for i, s in enumerate(exclude_sources):
                key = f"ex{i}"
                placeholders.append(f":{key}")
                params[key] = s
            conditions.append(f"{src_col} NOT IN ({', '.join(placeholders)})")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        uid_expr = m["user"] or "NULL"
        src_expr = f"{src_col} AS source" if src_col else "NULL AS source"
        sql = text(
            f"SELECT {m['id_field']} AS id, {m['time']} AS t, {uid_expr} AS uid, "
            f"({m['summary']}) AS summary, {src_expr} "
            f"FROM {m['table']} {where} ORDER BY {m['time']} DESC LIMIT :lim"
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
                "source": (r["source"] or "") if src_col else "",
            })
        return rows

    @staticmethod
    def is_test_record(row: dict) -> bool:
        """统一分辨方法：该条留痕是否为测试流量（读 source 字段，不 grep 前缀）。

        无 source 的模块（13/16）恒返回 False —— 这些表的测试流量当前无法结构化过滤
        （见需求基线 §四.6 与计划 §2.6 的范围外说明）。
        """
        from ..infrastructure.logging.audit import SOURCE_TEST

        return (row.get("source") or "") == SOURCE_TEST

    @staticmethod
    def query_aggregated(user_id: str, module: str, limit: int,
                         exclude_sources: list[str] | None = None) -> list[dict]:
        """按「人 / 记录模块」聚合各类日志，返回按时间倒序的扁平列表。

        Args:
            exclude_sources: 需要排除的流量性质（如 ["test"] 排除测试流量）。
                             仅对带 source 列的模块生效。
        """
        modules = [m for m in LOG_MODULES if not module or m["key"] == module]
        collected: list[dict] = []
        with get_session() as session:
            for m in modules:
                if user_id and not m["user"]:
                    continue
                try:
                    collected.extend(ConsoleLogRepo._query_module(
                        session, m, user_id, limit, exclude_sources,
                    ))
                except Exception as ex:
                    logger.warning("console_log_repo query failed module=%s: %s", m["key"], ex)
        collected.sort(key=lambda r: _time_sort_key(r["time"]), reverse=True)
        return collected[:limit]
