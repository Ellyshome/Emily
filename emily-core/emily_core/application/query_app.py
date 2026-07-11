"""QueryApplication —— 查询编排层。

负责从 RouteResult 构建 QueryCommand，调用 QueryService 执行查询，
并返回格式化的回复。
"""

import logging
from typing import Optional

from ..adapters.standard.result import RouteResult, HandlerResult
from ..adapters.standard.command import QueryCommand
from ..services.query_service import QueryService

logger = logging.getLogger("emily.app.query")


class QueryApplication:
    """查询应用层编排。

    Args:
        query_service: 查询服务
    """

    def __init__(self, query_service: QueryService):
        self.query_service = query_service

    async def handle_query(
        self, route_result: RouteResult, user_id: str, message_id: str
    ) -> HandlerResult:
        """处理查询请求。

        从 RouteResult 的 data 字段提取查询参数，
        构建 QueryCommand，执行查询并返回格式化回复。

        Args:
            route_result: 路由结果（含 intent, project_id, data）
            user_id: 用户 ID（未使用，预留）
            message_id: 消息 ID（未使用，预留）

        Returns:
            HandlerResult: 处理结果（含格式化回复文本）
        """
        try:
            data = route_result.data or {}

            cmd = QueryCommand(
                query_type=data.get("query_type", "event"),
                project_id=route_result.project_id,
                project_name=route_result.project_name,
                time_range=data.get("time_range", "all"),
                status_filter=data.get("status_filter"),
                assignee=data.get("assignee"),
                sender_name=data.get("sender_name"),
                keyword=data.get("keyword"),
                intent=data.get("intent"),
                file_type=data.get("file_type"),
                conversation_id=data.get("conversation_id"),
                limit=data.get("limit", 50),
            )

            logger.info(
                "Query: type=%s, project=%s, time=%s, limit=%d",
                cmd.query_type, cmd.project_id, cmd.time_range, cmd.limit,
            )

            results = self.query_service.execute(cmd)
            reply = self.query_service.format_reply(cmd.query_type, results)

            logger.info(
                "Query result: type=%s, total=%s",
                cmd.query_type, results.get("total", "N/A"),
            )

            return HandlerResult(
                success=True,
                object_type="query",
                reply=reply,
            )

        except Exception as e:
            logger.error("Query failed: %s", e, exc_info=True)
            return HandlerResult(
                success=False,
                error_code="query_failed",
                reply=f"查询失败：{e}",
            )
