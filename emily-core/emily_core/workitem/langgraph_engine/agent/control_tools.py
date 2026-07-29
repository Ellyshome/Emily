# emily-core/emily_core/workitem/langgraph_engine/agent/control_tools.py
"""agent loop 控制工具 spec —— complete_work + ask_user。

这两个工具是 agent loop 的控制信号（非业务工具）：
- complete_work: LLM 完成工作后显式返回结构化成果 → 路由 summarizing
- ask_user: 信息不足时挂起反馈 → interrupt WAITING_FOR_INPUT

不经过 BusinessFlowToolRegistry / 权限过滤，由 build_tool_specs 直接追加给 LLM。
"""
from __future__ import annotations

# complete_work: 成果返回控制工具
COMPLETE_WORK_SPEC = {
    "type": "function",
    "function": {
        "name": "complete_work",
        "description": (
            "完成工作，向系统返回结构化成果。当所有必要工具调用已完成、工作要求已满足时，"
            "必须调用此工具返回成果，由上层组织给用户的回复。禁止用纯文本回复用户。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["success", "partial", "failed"],
                    "description": "工作完成状态",
                },
                "summary": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "关键成果事实（上层据此组织回复，每条≤100字）",
                },
                "data": {
                    "type": "object",
                    "description": "结构化成果数据（如 event_no、project_id 等）",
                },
                "business_object_no": {
                    "type": "string",
                    "description": "业务编号（如 EVT-xxx / TSK-xxx），无则空",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "执行中遇到的问题（无则空数组）",
                },
                "needs_confirm": {
                    "type": "boolean",
                    "description": "成果是否需要用户确认（如拟录入单待确认）",
                },
            },
            "required": ["status", "summary"],
        },
    },
}

# ask_user: checkpoint 反馈控制工具
ASK_USER_SPEC = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "信息不足无法继续工作时调用此工具向用户提问（由上层转达）。"
            "仅用于缺少必填信息需用户补充的场景，不用于返回成果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要问用户的问题（清晰具体）",
                },
            },
            "required": ["question"],
        },
    },
}

CONTROL_TOOL_SPECS = [COMPLETE_WORK_SPEC, ASK_USER_SPEC]
CONTROL_TOOL_NAMES = {"complete_work", "ask_user"}
