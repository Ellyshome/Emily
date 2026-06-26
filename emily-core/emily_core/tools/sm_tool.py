"""query_sm_status — Agent 状态机查询工具。

由 tools/__init__.py 的 create_all_tools() 注册到 ToolRegistry（LLM 可见）。
"""

import logging

logger = logging.getLogger(__name__)

_SM_STATUS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {
            "type": "string",
            "description": "要查询的节点编号，如 '5.1.3'。留空则按 stage_id 或 keyword 查询。",
        },
        "stage_id": {
            "type": "integer",
            "description": "按阶段筛选：1=立项, 2=设计, 3=报批, 4=招标, 5=施工, 6=验收, 7=交付。0 或不传则不按阶段过滤。",
        },
        "keyword": {
            "type": "string",
            "description": "按节点名称关键词搜索，如'桩基''消防''施工图'。留空则返回全部。",
        },
        "limit": {
            "type": "integer",
            "description": "最大返回节点数，默认 10。",
        },
    },
    "required": [],
}


def create_query_sm_status_tool(sm_service):
    """创建 query_sm_status 工具（注册到 LLM ToolRegistry）。

    Args:
        sm_service: StateMachineService 实例

    Returns:
        ToolDefinition
    """
    from emily_core.agent.tool_registry import ToolDefinition

    async def execute(args: dict) -> dict:
        node_id = args.get("node_id", "") or ""
        stage_id = args.get("stage_id", 0) or 0
        keyword = args.get("keyword", "") or ""
        limit = args.get("limit", 10) or 10

        try:
            result = await sm_service.query_sm_status(
                node_id=node_id, stage_id=stage_id, keyword=keyword, limit=limit,
            )
            return result
        except Exception as e:
            logger.warning("query_sm_status failed: %s", e)
            return {"success": False, "reply": f"状态查询失败：{e}"}

    return ToolDefinition(
        name="query_sm_status",
        description=(
            "查询项目全景状态机中的任意节点状态、前置满足度、里程碑标记、进度信息。维度包括：\n"
            "- node_id — 按节点编号查询" + """
- stage_id — 按阶段批量查询（1=立项,2=设计,3=报批,4=招标,5=施工,6=验收,7=交付）
- keyword — 按节点名称或行业关键词搜索，如"验收""消防""桩基""设计""招标"
- 返回整体进度百分比 + 匹配节点的状态、满足度、依赖等信息

典型使用场景：
- 用户问"桩基做完了吗" → keyword="桩基"
- 用户问"阶段二整体进度" → stage_id=2
- 用户问"施工许可证下来了吗" → node_id="4.14"
"""),
        parameters=_SM_STATUS_TOOL_SCHEMA,
        execute=execute,
        require_admin=False,
    )
