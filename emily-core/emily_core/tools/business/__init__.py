"""业务工具 —— 受 SOP §3.2 白名单约束，AuthHook 前置拦截。"""

from ..event_tool import handle_record_event
from ..task_tool import handle_record_task
from ..meeting_tool import handle_record_meeting
from ..file_tool import handle_record_file

from ..plan_task_tool import (
    handle_record_plan_task,
    handle_submit_plan_task,
    handle_review_plan_task,
    handle_query_plan_tasks,
    _RECORD_PLAN_TASK_SCHEMA,
    _SUBMIT_PLAN_TASK_SCHEMA,
    _REVIEW_PLAN_TASK_SCHEMA,
    _QUERY_PLAN_TASKS_SCHEMA,
)

from ..memory_tool import create_memory_tool


async def handle_write_user_memory(params: dict, user_memory_service=None, **kw) -> dict:
    if user_memory_service is None:
        return {"success": False, "message": "长期记忆服务未启用"}
    tool = create_memory_tool(user_memory_service, user_name=kw.get("user_name", ""))
    return await tool.execute(params)


__all__ = [
    "handle_record_event", "handle_record_task", "handle_record_meeting", "handle_record_file",
    "handle_record_plan_task", "handle_submit_plan_task", "handle_review_plan_task", "handle_query_plan_tasks",
    "handle_write_user_memory",
    "_RECORD_PLAN_TASK_SCHEMA", "_SUBMIT_PLAN_TASK_SCHEMA",
    "_REVIEW_PLAN_TASK_SCHEMA", "_QUERY_PLAN_TASKS_SCHEMA",
]
