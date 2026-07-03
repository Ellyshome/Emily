"""全景节点图 V2 LLM 口述引导录入工具 —— 需求文档 §5.4（Phase 2）。

通过 LLM 对话式引导用户逐项填写节点信息：
  - 解析用户自然语言输入 → 提取节点字段
  - 追问缺失的必要信息
  - 支持"先创建父节点，再逐步加子节点"的渐进式录入
  - 录入完成后自动生成「启动文档记录」

作为 BusinessFlowTool 注册到 ToolRegistry，在 SOP-000-SYS-add-node 场景触发。

工具函数：
  - voice_create_node: 接收用户口述文本，LLM 解析 + 追问
  - voice_add_child: 口述添加子节点
  - voice_add_deliverable: 口述添加成果
"""

from __future__ import annotations

import json
import logging
import hashlib
import re
from dataclasses import dataclass, field

logger = logging.getLogger("emily.tool.node_voice_entry")


# ══════════════════════════════════════════════════════════════════════════════
# LLM 结构化提取 Schema
# ══════════════════════════════════════════════════════════════════════════════

VOICE_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create_node", "add_child", "add_deliverable", "add_dependency", "query", "unknown"],
            "description": "用户意图"
        },
        "extracted_data": {
            "type": "object",
            "properties": {
                "node_name": {"type": "string", "description": "节点名称/工作项描述"},
                "deadline": {"type": "string", "description": "截止时间（ISO8601）"},
                "owner_dept_id": {"type": "string", "description": "主责部门/条线"},
                "parent_node_id": {"type": "string", "description": "父节点ID（如果是子节点）"},
                "stage_id": {"type": "integer", "description": "阶段ID"},
                "deliverable_name": {"type": "string", "description": "成果/产出物名称"},
                "deliverable_amount": {"type": "number", "description": "成果数量"},
                "deliverable_unit": {"type": "string", "description": "成果单位"},
                "depends_on_node": {"type": "string", "description": "依赖于哪个节点的成果"},
            },
        },
        "missing_fields": {
            "type": "array",
            "items": {"type": "string"},
            "description": "缺失的必要字段（如 ['deadline', 'owner_dept_id']）"
        },
        "follow_up_question": {
            "type": "string",
            "description": "用自然语言询问用户缺失的信息"
        },
        "confidence": {
            "type": "number",
            "description": "提取置信度 0.0-1.0"
        },
    },
    "required": ["action", "extracted_data", "confidence"],
}

VOICE_ENTRY_SYSTEM_PROMPT = """你是 Emily 项目计划助手的对话理解模块。用户在通过口述/自然语言录入项目节点信息。

你的任务：
1. 理解用户的自然语言输入，提取节点相关的结构化字段
2. 识别缺失的必要字段（节点名称和截止时间是必填的）
3. 生成友好的追问，引导用户补充缺失信息
4. 识别用户意图：创建节点、添加子节点、添加成果、添加依赖、查询

节点字段说明：
- node_name: 节点名称/工作描述（如"主体结构施工"）
- deadline: 截止时间（如"下周五"→转换为ISO8601）
- owner_dept_id: 负责部门（如"工程部"→dept-eng）
- parent_node_id: 如果是子节点，指定父节点
- stage_id: 0=立项 1=规划 2=施工 3=交付
- deliverable_name: 成果物（如"施工图"）
- deliverable_amount: 数量（如 1）
- deliverable_unit: 单位（如"份"、"平方米"）

回复规则：
- confidence ≥ 0.8 且无缺失字段 → action=create_node/add_child 等，follow_up_question 为空
- 有缺失字段 → action 仍为推断的意图，但 follow_up_question 要有引导性追问
- 无法理解 → action=unknown，follow_up_question 请用户重新描述"""


# ══════════════════════════════════════════════════════════════════════════════
# 口述录入对话状态
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class VoiceEntryState:
    """口述录入对话状态（存储每个用户当前正在创建的节点草稿）。"""
    user_id: str = ""
    project_id: str = ""
    draft: dict = field(default_factory=dict)
    step: str = "idle"  # idle / collecting_info / confirming / done
    created_node_ids: list[str] = field(default_factory=list)


# 全局对话状态（生产环境应改为 Redis/DB 持久化）
_voice_states: dict[str, VoiceEntryState] = {}


def get_voice_state(user_id: str) -> VoiceEntryState:
    """获取或创建用户的口述录入状态。"""
    if user_id not in _voice_states:
        _voice_states[user_id] = VoiceEntryState(user_id=user_id)
    return _voice_states[user_id]


async def voice_parse_input(user_text: str, user_id: str = "", project_id: str = "") -> dict:
    """解析用户的自然语言输入，返回结构化提取结果 + 可能的追问。

    这是供 LLM Agent 调用的核心函数——Agent 在 workitem 上下文中调用它，
    然后根据返回的 action 和 missing_fields 决定下一步。

    Args:
        user_text: 用户的自然语言输入
        user_id: 当前用户ID（用于对话状态上下文）
        project_id: 当前项目ID

    Returns:
        {
            "action": "create_node" | "add_child" | ... | "unknown",
            "extracted": {...},
            "missing": [...],
            "follow_up": "请补充截止时间...",
            "confidence": 0.85,
        }
    """
    try:
        from ..providers.llm_client import chat_json

        messages = [
            {"role": "system", "content": VOICE_ENTRY_SYSTEM_PROMPT},
            {"role": "user", "content": f"当前项目: {project_id}\n用户输入: {user_text}"},
        ]

        result = await chat_json(
            messages=messages,
            schema=VOICE_NODE_SCHEMA,
            temperature=0.1,
        )

        if result is None:
            return {
                "action": "unknown",
                "extracted": {},
                "missing": [],
                "follow_up": "抱歉，我没能理解您的意思。请再说一遍您要录入的节点信息？",
                "confidence": 0.0,
            }

        return {
            "action": result.get("action", "unknown"),
            "extracted": result.get("extracted_data", {}),
            "missing": result.get("missing_fields", []),
            "follow_up": result.get("follow_up_question", ""),
            "confidence": result.get("confidence", 0.0),
        }

    except Exception as e:
        logger.error("voice_parse_input error: %s", e)
        return {
            "action": "unknown",
            "extracted": {},
            "missing": [],
            "follow_up": "解析出错，请稍后重试。",
            "confidence": 0.0,
        }


async def voice_execute_create(user_id: str, project_id: str, extracted: dict) -> dict:
    """执行节点创建——在 LLM 确认完整信息后调用 NodeService。

    Returns:
        {"success": bool, "node_id": str, "message": str}
    """
    try:
        from ..services.node_commands import CreateNodeCommand
        from ..services.node_service import NodeService
        from ..repositories.permission_repo import PermissionRepository

        # 生成节点编号
        node_id = _generate_node_id(extracted.get("node_name", ""), project_id)

        svc = NodeService(user_repo=PermissionRepository())
        cmd = CreateNodeCommand(
            project_id=project_id,
            node_id=node_id,
            node_name=extracted.get("node_name", "未命名节点"),
            deadline=extracted.get("deadline", ""),
            owner_dept_id=extracted.get("owner_dept_id", "项目总"),
            stage_id=extracted.get("stage_id", 0),
            creator_id=user_id,
        )
        result = await svc.create_node(cmd)

        if result.success:
            state = get_voice_state(user_id)
            state.created_node_ids.append(node_id)

            return {
                "success": True,
                "node_id": node_id,
                "message": f"节点「{cmd.node_name}」创建成功（编号：{node_id}）。"
                          f"您可以继续添加子节点、成果或依赖。",
            }
        else:
            return {"success": False, "node_id": "", "message": result.message}

    except Exception as e:
        return {"success": False, "node_id": "", "message": str(e)}


def _generate_node_id(node_name: str, project_id: str) -> str:
    """生成节点编号（简单规则）。"""
    clean = re.sub(r'[^一-龥a-zA-Z0-9]', '', node_name)
    hash_part = hashlib.md5(clean.encode()).hexdigest()[:4].upper()
    return f"NODE-{hash_part}"
