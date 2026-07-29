# emily-core/emily_core/workitem/langgraph_engine/agent/prompt_builder.py
"""Agent loop system prompt 构建器。

system prompt = 角色 + SOP .md 全文（指导）+ 可见工具表 + session 上下文 + 行为规则。
指导不控制：SOP .md 告诉 LLM 该做什么，不强制 exact steps（Anthropic harness 理念）。
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("emily.langgraph.prompt_builder")


def build_system_prompt(
    sop_text: str,
    tool_specs: list[dict],
    session_ctx: Any,
    work_spec: dict | None = None,
    user_input: str = "",
    additional_input: str = "",
) -> str:
    """构建 agent loop system prompt。

    Args:
        sop_text: 匹配到的 SOP .md 全文（由 routing 节点加载）
        tool_specs: LLM 可见 tool spec 列表（build_tool_specs 产出）
        session_ctx: SessionContext（取 user_name/project_name 等上下文）
        work_spec: SessionAgent 组装的工作要求（替代 result_constraints + user_input 为主指令）
        user_input: 用户原始输入（当 work_spec 为 None 时的兜底）
        additional_input: 续接时用户上一轮补充的信息

    Returns:
        str: 完整 system prompt
    """
    # ── 工具表（从 tool_specs 提取 name + description + resolver hint）──
    tool_lines: list[str] = []
    for spec in tool_specs:
        fn = spec.get("function", {})
        name = fn.get("name", "")
        desc = (fn.get("description") or "").split("\n")[0][:120]
        tool_lines.append(f"- {name}: {desc}")
    tools_text = "\n".join(tool_lines) if tool_lines else "（无可用工具）"

    # ── session 上下文 ──
    ctx_text = ""
    if session_ctx is not None:
        ctx_text = (
            f"当前用户：{getattr(session_ctx,'user_name','') or '未知'}"
            f"（L{getattr(session_ctx,'level',0)}）\n"
            f"当前项目：{getattr(session_ctx,'project_name','') or '未指定'}\n"
            f"可访问项目数：{len(getattr(session_ctx,'project_ids',[]) or [])}"
        )

    # ── 工作要求（来自 SessionAgent，agent loop 的执行指令）──
    ws = work_spec or {}
    objective = ws.get("objective", "")
    constraints = ws.get("constraints", {})
    output_spec = ws.get("output_spec", {})
    user_request = ws.get("user_request", "") or user_input

    rc_text = ""
    if constraints:
        rc_text = f"\n\n【成果约束】\n{json.dumps(constraints, ensure_ascii=False, indent=2)}"

    cont_text = ""
    if additional_input:
        cont_text = (
            f"\n\n【续接上下文】\n用户上一轮补充：{additional_input}\n"
            f"请基于新信息继续，跳过已收集的字段。"
        )

    data_fields = output_spec.get("data_fields", [])
    data_fields_text = f"\n- 成果数据字段：{data_fields}" if data_fields else ""

    prompt = f"""你是 Emily 的工作执行引擎。你的职责是按工作要求调用工具获取/写入数据，**不要直接回复用户**。

# 你的工作方式（agent loop）
1. 阅读下方工作要求与 SOP 指导，明确要完成的目标
2. 查看可用工具表，选择合适工具
3. 若工具参数需要 UUID（如 project_id）但你只有名称，**必须先调 resolve_project 解析**，再填入业务工具
4. 调用工具后，查看 tool_result：成功则继续下一步；失败则根据错误自行调整重试
5. **完成工作要求后，必须调用 `complete_work` 工具返回结构化成果**（status/summary/data/business_object_no）——禁止用纯文本回复用户，回复由上层组织
6. **信息不足无法继续时，调用 `ask_user` 工具提问**（由上层转达用户）——不要用 complete_work 返回疑问

# 工作要求
- 目标：{objective or '（按 SOP 指导处理）'}
- 用户原始请求：{user_request}
- 成果规格：detail={output_spec.get('detail', 'standard')}, format={output_spec.get('format', 'natural')}{data_fields_text}

# 业务流指导（SOP）
{sop_text or '（未匹配到 SOP，按通用方式处理）'}

# 可用工具
{tools_text}

# 会话上下文
{ctx_text}

# 行为规则
- 工具参数中的 UUID 字段必须先调 resolver 解析，禁止把名称塞进 UUID 字段
- 看到 tool_result 报错时，分析原因并调整参数重试，不要原样重试
- **完成工作必调 complete_work，信息不足必调 ask_user，二者必居其一，不要返回纯文本**
- summary 字段是给上层组织回复的关键事实，应包含业务编号（如 EVT-xxx）和核心结论
{rc_text}{cont_text}
"""
    return prompt
