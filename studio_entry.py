# studio_entry.py
"""LangGraph Studio 观察入口 —— 会话编排图（桩件模式，零副作用）。

定位：
  - 给开发期"看内核怎么跑"用：在 LangGraph Studio 里观察 session 编排图的节点流转、
    条件路由与状态快照（KernelState 全字段）。
  - 行为全部由桩件驱动：不接数据库、不调 LLM、不写业务库，可反复重放。

桩件触发词（写在 Studio 输入 JSON 的 `user_text` 里）：
  - "你好/您好/谢谢" → `fast_reply` 零模型短路
  - "查/搜索/数据/工具" → understand → gate → execute → understand 的工具回环
  - "越权"            → gate 拒绝（能力不在可见集，fail-closed）
  - "计划"            → understand → plan（计划子图，s1/s2 并行 + s3 依赖收口）→ understand
  - "循环"            → 能力反复调用直到迭代上限 → error_analysis 收口

Studio 输入示例（`_gate_active` 必须显式置 true，否则路由会跳过 gate 节点）：
  {"user_text": "帮我查一下项目数据", "conversation_id": "studio-1", "_gate_active": true}

启动：
  langgraph dev --allow-blocking
  # Studio: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024

说明：本入口不传 checkpointer（Studio/Agent Server 自带持久化，图内自带 saver 会被覆盖），
因此挂起/续接（M5）路径不在本图内，生产语义请走容器内真实链路验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

from emily_core.session.capability_contract import CapabilitySpec
from emily_core.session.graph_gate import build_gate_evaluator
from emily_core.session.session_graph import build_session_graph

#: 门禁可见能力集（demo_forbidden 不在其中 → 命中拒绝路径）
VISIBLE_CAPABILITIES = ("demo_query",)

_QUERY_SCHEMA = {
    "type": "object",
    "properties": {"keyword": {"type": "string"}},
    "required": ["keyword"],
}

_FAST_WORDS = ("你好", "您好", "谢谢", "再见")
_TOOL_WORDS = ("查", "搜索", "数据", "工具")


class StudioConfig:
    """最小配置桩（仅被 _max_iterations 兜底读取）。"""

    agent_loop_max_iterations = 6


def _tool_call(name: str, params: dict, call_id: str = "stub-call") -> dict:
    return {"type": "tool_call", "tool_name": name, "tool_arguments": params,
            "tool_call_id": call_id, "content": ""}


def _text(content: str) -> dict:
    return {"type": "text", "content": content}


def _user_text(messages: list) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _has_tool_result(messages: list) -> bool:
    return any(isinstance(m, dict) and m.get("role") == "tool" for m in (messages or []))


async def _stub_llm(messages: list, tool_specs: list) -> dict:
    """按触发词产出模型结果（与既有 agent loop 的调用结果同形）。"""
    text = _user_text(messages)
    if text.startswith("[系统]"):
        return _text("[桩件] 计划已执行完毕（plan 子图收口），据此回复。")
    if "循环" in text:
        return _tool_call("demo_query", {"keyword": "循环"})
    if "越权" in text:
        if not _has_tool_result(messages):
            return _tool_call("demo_forbidden", {"reason": text})
        return _text("门禁已拒绝 demo_forbidden（fail-closed 生效）。")
    if any(w in text for w in _TOOL_WORDS):
        if not _has_tool_result(messages):
            return _tool_call("demo_query", {"keyword": text})
        return _text("[桩件] demo_query 结果已回灌，据此收口回复。")
    return _text(f"[桩件回复] 收到：{text or '(空)'}")


async def _stub_executor(capability: str, params: dict) -> dict:
    """能力执行桩：结构化成功载荷，不触网、不写库。"""
    return {"success": True, "status": "success",
            "reply": f"[桩件能力] {capability} 执行完成", "echo": dict(params or {})}


async def _stub_plan_builder(user_text: str, specs: list) -> dict:
    """计划桩：s1/s2 无依赖（同层并行），s3 依赖 s1+s2（收口层）。"""
    return {"steps": [
        {"step_id": "s1", "capability": "demo_query", "params": {"keyword": "项目A"},
         "depends_on": []},
        {"step_id": "s2", "capability": "demo_query", "params": {"keyword": "项目B"},
         "depends_on": []},
        {"step_id": "s3", "capability": "demo_query", "params": {"keyword": "汇总"},
         "depends_on": ["s1", "s2"]},
    ]}


def _capability_specs() -> list:
    return [CapabilitySpec(name="demo_query", kind="query", params_schema=_QUERY_SCHEMA,
                           description="桩件查询能力（Studio 观察用）")]


def _tool_specs() -> list:
    return [{"type": "function", "function": {
        "name": "demo_query", "description": "桩件查询能力",
        "parameters": _QUERY_SCHEMA}}]


def make_session_graph(config: dict | None = None):
    """langgraph.json 图入口：返回编译后的会话编排图（桩件依赖）。"""
    return build_session_graph(
        llm_caller=_stub_llm,
        capability_executor=_stub_executor,
        fast_responder=lambda text: (
            "[桩件] 您好，我在（快速短路，未调用模型）。"
            if any(w in str(text or "") for w in _FAST_WORDS) else None
        ),
        tool_specs_provider=_tool_specs,
        prompt_builder=lambda: "你是 Emily 会话编排内核的 Studio 观察台（桩件模式）。",
        history_provider=lambda: [],
        capability_spec_provider=_capability_specs,
        capability_plan_builder=_stub_plan_builder,
        plan_gate=lambda text: "计划" in str(text or ""),
        gate_evaluator=build_gate_evaluator(visible_provider=lambda: list(VISIBLE_CAPABILITIES)),
        config=StudioConfig(),
        checkpointer=False,
    )
