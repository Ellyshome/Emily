"""Tool 组装工厂。

创建 ToolRegistry（LLM 工具）+ BusinessFlowToolRegistry（业务流工具）。
每个请求创建新的注册表（部分工具需捕获 user_id / message_id）。

M9 架构重构（2026-06-19）：
  - invoke_business_flow → MasterAgent._dispatch_specialist()（框架内置方法）
  - invoke_guardian → 已移除（原 Pipeline Hook 已删除）
  - list_sop_catalog → 删除（已通过 SOPIntentRegistry.dump_as_text() 注入 system prompt）
  - read_flow_diagram / list_flow_diagrams → 删除（子图全部注入 prompt 的 {SUB_FLOWS}）

M14 架构重构（2026-06-21）：
  - record_event / record_task / record_meeting / record_file 从 LLM ToolRegistry 移除
  - 迁移至 BusinessFlowToolRegistry：LLM 结构化输出参数 → 框架直接调用 handler
  - query_data 保留在 LLM ToolRegistry（供 unmatched 兜底只读查询）
  - 条件工具（pending_issues / user_memory / knowledge_search 等）保持不变

M7.1: FlowMapManager 决策树已全部注入 system prompt，仅保留 create_flow_diagram 管理员工具。
M8a: PendingIssuesService 待解决问题清单。
M8c: write_user_memory 长期记忆工具。
"""

import logging
from functools import partial

from ..agent.tool_registry import ToolRegistry
from ..agent.tool_registry import ToolDefinition

from .business_flow_tools import BusinessFlowTool, BusinessFlowToolRegistry
from .event_tool import handle_record_event, _EVENT_TOOL_SCHEMA, _EVENT_TOOL_DESCRIPTION
from .task_tool import handle_record_task, _TASK_TOOL_SCHEMA, _TASK_TOOL_DESCRIPTION
from .meeting_tool import handle_record_meeting, _MEETING_TOOL_SCHEMA, _MEETING_TOOL_DESCRIPTION
from .file_tool import handle_record_file, _FILE_TOOL_SCHEMA, _FILE_TOOL_DESCRIPTION
from .file_tool import create_send_file_tool, create_read_local_file_tool
from .query_tool import handle_query_data, create_query_tool, _QUERY_TOOL_SCHEMA, _QUERY_TOOL_DESCRIPTION
from .plan_task_tool import (
    handle_record_plan_task,
    handle_submit_plan_task,
    handle_review_plan_task,
    handle_query_plan_tasks,
    _RECORD_PLAN_TASK_SCHEMA,
    _SUBMIT_PLAN_TASK_SCHEMA,
    _REVIEW_PLAN_TASK_SCHEMA,
    _QUERY_PLAN_TASKS_SCHEMA,
)

logger = logging.getLogger(__name__)


def create_all_tools(
    event_app,
    task_app,
    meeting_app,
    file_app,
    query_service,
    flow_map_manager=None,       # M7.1: FlowMapManager（Mermaid 决策树文件管理器）
    user_id: str = "",
    message_id: str = "",
    is_admin: bool = False,
    llm_client=None,            # LLM 客户端
    config=None,                 # 全局配置
    pending_issues=None,         # M8a: 待解决问题清单服务
    user_memory_service=None,    # M8c: 用户长期记忆服务
    user_name: str = "",         # M8c: 当前用户显示名称
    rag_provider=None,           # Ex4: RagProvider（MaxKB 或本地关键词搜索）
    chat_archive_service=None,  # M11: 聊天归档服务
    send_file_callback=None,    # M13: 主动发送文件回调
    file_storage_service=None,  # M13: 文件存储服务（按需读取/下载文件）
    sm_service=None,            # SM: 全局状态机服务（query_sm_status 工具）
) -> ToolRegistry:
    """创建 LLM 工具注册表（仅含条件工具 + query_data 兜底查询）。

    M14: record_event / record_task / record_meeting / record_file 已迁移至
    BusinessFlowToolRegistry，不再注册为 LLM function calling 工具。
    query_data 保留供 unmatched 兜底路径使用。

    Args:
        ...（同 create_business_flow_tools）
        flow_map_manager: M7.1 FlowMapManager 实例
        is_admin: 当前用户是否为管理员
        llm_client: LLM 客户端
        rag_provider: RAG 检索提供者
        chat_archive_service: 聊天归档服务
        send_file_callback: 文件发送回调
        file_storage_service: 文件存储服务

    Returns:
        ToolRegistry（仅含条件工具 + query_data）
    """
    registry = ToolRegistry()

    # M14: 核心写工具已迁移至 BusinessFlowToolRegistry，不再注册为 LLM 工具
    # （原 record_event / record_task / record_meeting / record_file 在此处移除）

    # 查询工具（保留供 unmatched 兜底只读查询）
    registry.register(create_query_tool(query_service))

    # M8a: 待解决问题管理工具（所有用户可查，管理员可处理）
    if pending_issues is not None:
        from .pending_issue_tool import create_pending_issue_tool
        registry.register(create_pending_issue_tool(pending_issues, is_admin=is_admin))

    # M8c: 长期记忆工具（所有用户可用）
    if user_memory_service is not None and user_name:
        from .memory_tool import create_memory_tool
        registry.register(create_memory_tool(user_memory_service, user_name=user_name))

    # M6: 守护调查已移除
    # （原 invoke_guardian 工具不再注册）

    # M9: SOP 业务流派发已迁移为 MasterAgent 内置方法 _dispatch_specialist()
    # （原 invoke_business_flow 工具不再注册——LLM 不再通过 tool calling 派发，
    #   框架根据 SOPMatchDecision 自动派发 Specialist）

    # M7.1: Mermaid 决策树管理工具（根图+子图已注入 prompt，仅保留管理员的创建工具）
    if flow_map_manager is not None:
        # 管理员 + LLM 可用时，可创建新图
        if is_admin and llm_client is not None:
            registry.register(_create_create_flow_diagram_tool(flow_map_manager))

    # Ex4: RAG 知识库搜索工具（需配置 RagProvider 且 kb_enabled）
    if rag_provider is not None and getattr(config, "kb_enabled", False) if config else False:
        try:
            from .knowledge_search_tool import create_knowledge_search_tool
            registry.register(create_knowledge_search_tool(rag_provider))
            logger.info("Ex4 knowledge_search tool registered: provider=%s",
                         type(rag_provider).__name__)
        except Exception as e:
            logger.warning("Ex4 knowledge_search tool registration failed: %s", e)

    # M11: chat_archive 工具（需 ChatArchiveService）
    if chat_archive_service is not None:
        from .chat_archive_tool import create_chat_archive_tool
        registry.register(create_chat_archive_tool(chat_archive_service))
        logger.info("M11 chat_archive tool registered")

    # M13: 文件发送工具（需 send_file_callback）
    if send_file_callback is not None:
        registry.register(create_send_file_tool(
            send_file_callback=send_file_callback,
            file_storage_service=file_storage_service,
        ))
        logger.info("M13 send_file tool registered")

    # M13: 本地文件读取工具（需 file_storage_service）
    if file_storage_service is not None:
        registry.register(create_read_local_file_tool(
            file_storage_service=file_storage_service,
        ))
        logger.info("M13 read_local_file tool registered")

    # SM: 全局状态机查询工具（需 sm_service）
    if sm_service is not None:
        from .sm_tool import create_query_sm_status_tool
        registry.register(create_query_sm_status_tool(sm_service))
        logger.info("SM query_sm_status tool registered")

    return registry


# ==============================================================================
# M14: 业务流工具注册表 — 框架直接执行（不暴露为 LLM function calling）
# ==============================================================================


def create_business_flow_tools(
    event_app,
    task_app,
    meeting_app,
    file_app,
    query_service,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,         # M8a: 待解决问题清单服务
    config=None,                 # M8a: 配置
    plan_task_app=None,          # 计划任务系统 Application
    sm_service=None,             # SM: 全局状态机服务（事件录入后自动匹配完成节点）
) -> BusinessFlowToolRegistry:
    """创建业务流工具注册表（框架直接执行，不经过 LLM tool calling）。

    LLM 在 BusinessFlowAgent 中输出结构化 JSON 参数，
    框架据此调用对应 handler，直接操作 Application/Service 层。

    Args:
        event_app: EventApplication 实例
        task_app: TaskApplication 实例
        meeting_app: MeetingApplication 实例
        file_app: FileApplication 实例
        query_service: QueryService 实例
        user_id: 当前用户 ID
        message_id: 当前消息 ID
        pending_issues: PendingIssuesService 实例（可选）
        config: 全局配置（可选）
        plan_task_app: PlanTaskApplication 实例（可选，计划任务系统）

    Returns:
        BusinessFlowToolRegistry
    """
    registry = BusinessFlowToolRegistry()

    # record_event — 事件录入
    sm_match_handler = None
    if sm_service is not None:
        async def _sm_match_wrapper(params):
            result = await handle_record_event(
                params, event_app=event_app, user_id=user_id, message_id=message_id,
                pending_issues=pending_issues, config=config,
            )
            # 事件录入成功后尝试匹配全景节点并自动完成
            if result.get("success") and result.get("object_id"):
                sm_result = await sm_service.try_match_and_complete(
                    event_title=params.get("title", ""),
                    event_type=params.get("event_type", ""),
                )
                if sm_result.get("matched") and sm_result.get("completed"):
                    reply = result.get("reply", "")
                    result["reply"] = reply + "\n" + sm_result.get("reply", "")
                    result["sm_matched"] = True
                    result["sm_node_id"] = sm_result.get("node_id")
            return result
        sm_match_handler = _sm_match_wrapper
    else:
        sm_match_handler = lambda params: handle_record_event(
            params, event_app=event_app, user_id=user_id, message_id=message_id,
            pending_issues=pending_issues, config=config,
        )

    registry.register(BusinessFlowTool(
        name="record_event",
        description=_EVENT_TOOL_DESCRIPTION,
        parameters=_EVENT_TOOL_SCHEMA,
        handler=sm_match_handler,
    ))

    # record_task — 任务管理
    registry.register(BusinessFlowTool(
        name="record_task",
        description=_TASK_TOOL_DESCRIPTION,
        parameters=_TASK_TOOL_SCHEMA,
        handler=lambda params: handle_record_task(
            params, task_app=task_app, user_id=user_id, message_id=message_id,
            pending_issues=pending_issues, config=config,
        ),
    ))

    # record_meeting — 会议纪要
    registry.register(BusinessFlowTool(
        name="record_meeting",
        description=_MEETING_TOOL_DESCRIPTION,
        parameters=_MEETING_TOOL_SCHEMA,
        handler=lambda params: handle_record_meeting(
            params, meeting_app=meeting_app, user_id=user_id, message_id=message_id,
            pending_issues=pending_issues, config=config,
        ),
    ))

    # record_file — 文件归档
    registry.register(BusinessFlowTool(
        name="record_file",
        description=_FILE_TOOL_DESCRIPTION,
        parameters=_FILE_TOOL_SCHEMA,
        handler=lambda params: handle_record_file(
            params, file_app=file_app, user_id=user_id, message_id=message_id,
            pending_issues=pending_issues, config=config,
        ),
    ))

    # query_data — 数据查询
    registry.register(BusinessFlowTool(
        name="query_data",
        description=_QUERY_TOOL_DESCRIPTION,
        parameters=_QUERY_TOOL_SCHEMA,
        handler=lambda params: handle_query_data(
            params, query_service=query_service,
        ),
    ))

    # ── 计划任务系统工具 ──
    if plan_task_app is not None:
        # record_plan_task — 创建计划任务
        registry.register(BusinessFlowTool(
            name="record_plan_task",
            description="创建计划任务（一次性或循环）。用于下达工作任务、布置周期性任务（日报/周报等）。",
            parameters=_RECORD_PLAN_TASK_SCHEMA,
            handler=lambda params: handle_record_plan_task(
                params, plan_task_app=plan_task_app, user_id=user_id, message_id=message_id,
                pending_issues=pending_issues, config=config,
            ),
        ))

        # submit_plan_task — 提交计划任务成果
        registry.register(BusinessFlowTool(
            name="submit_plan_task",
            description="提交计划任务成果。执行者在完成任务后提交成果。",
            parameters=_SUBMIT_PLAN_TASK_SCHEMA,
            handler=lambda params: handle_submit_plan_task(
                params, plan_task_app=plan_task_app, user_id=user_id, message_id=message_id,
            ),
        ))

        # review_plan_task — 审核计划任务成果
        registry.register(BusinessFlowTool(
            name="review_plan_task",
            description="审核计划任务成果（确认完成或退回修改）。",
            parameters=_REVIEW_PLAN_TASK_SCHEMA,
            handler=lambda params: handle_review_plan_task(
                params, plan_task_app=plan_task_app, user_id=user_id, message_id=message_id,
            ),
        ))

        # query_plan_tasks — 查询计划任务
        registry.register(BusinessFlowTool(
            name="query_plan_tasks",
            description="查询计划任务列表（按执行人或发起人、按状态过滤）。",
            parameters=_QUERY_PLAN_TASKS_SCHEMA,
            handler=lambda params: handle_query_plan_tasks(
                params, plan_task_app=plan_task_app, user_id=user_id,
            ),
        ))

    logger.info(
        "M14 BusinessFlowToolRegistry created: %d tools registered",
        len(registry),
    )
    return registry


# ==============================================================================
# M7.1: Mermaid 决策树管理工具（根图+子图已注入 prompt，仅保留创建工具）
# ==============================================================================


def _create_create_flow_diagram_tool(flow_map_manager) -> ToolDefinition:
    """创建 create_flow_diagram 工具（仅管理员 + LLM 可用）。"""

    async def execute(args: dict) -> dict:
        description = args.get("description", "")
        file_name = args.get("file_name", "")
        if not description:
            return {"success": False, "error": "请提供业务流程的自然语言描述"}
        if not file_name:
            return {"success": False, "error": "请提供文件名（蛇形命名，不含 .md）"}

        result = await flow_map_manager.from_natural_language(description, file_name)
        if result is None:
            return {
                "success": False,
                "error": "无法创建流程图（LLM 不可用或生成失败）",
            }
        return {
            "success": True,
            "message": f"流程图 '{file_name}.md' 已创建（框架重启后自动注入 system prompt）",
            **result,
        }

    return ToolDefinition(
        name="create_flow_diagram",
        description=(
            "根据自然语言描述创建一个新的业务决策流程图文件。仅管理员可用。"
            "参数 description 为业务流程的自然语言描述。"
            "参数 file_name 为文件名（蛇形命名，不含 .md 后缀，如 'my_flow'）。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "业务流程的自然语言描述",
                },
                "file_name": {
                    "type": "string",
                    "description": "文件名（蛇形命名，不含 .md）",
                },
            },
            "required": ["description", "file_name"],
        },
        execute=execute,
        require_admin=True,
    )
