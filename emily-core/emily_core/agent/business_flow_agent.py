"""BusinessFlowAgent —— 业务流专属执行 Agent（Specialist）。

M9 核心组件。当 Orchestrator (MasterAgent) 通过 LLM 语义匹配命中某个 SOP 后，
BusinessFlowAgent 按需加载该 SOP 的 §1-§7 全文为 system prompt，
使用限定工具集按 SOP 逐步执行，返回结构化 FlowResult。

M14 结构化输出模式：
  - 核心业务工具（record_* / query_data）已迁移至 BusinessFlowToolRegistry
  - BusinessFlowAgent 检测到 allowed_tool_names 中的工具都在 business_flow_tools
    中时，切换为结构化输出模式：LLM 输出 JSON 参数 → 框架直接调用 handler
  - 否则回退到传统 ReAct + tool calling 模式（用于非核心工具如 knowledge_search）

设计原则：
  - 按需创建、用完即弃：每个 Specialist 实例处理一个 SOP 子任务
  - System prompt = SOP 全文（§1-§7），不做摘要
  - 工具集由 SOP §3.2 声明，精确限定（≤4 个）
  - 返回结构化 FlowResult，由 Orchestrator 合成自然语言

与 Orchestrator 的职责边界：
  ┌──────────────┬──────────────────────┬──────────────────────────┐
  │              │ MasterAgent (Orch.)  │ BusinessFlowAgent (Spec.)│
  ├──────────────┼──────────────────────┼──────────────────────────┤
  │ 读取内容     │ prompts/flows/ (路由)│ SOP §1-§7 全文           │
  │ System Prompt│ 路由流程 + SOP 目录  │ 单个 SOP 全文            │
  │ 工具集       │ 全部工具             │ 由 SOP §3.2 声明 ≤4 个   │
  │ 迭代上限     │ 10 轮                │ 由 SOP 配置 (默认 8)     │
  │ 对话上下文   │ 滑动窗口 (长期)      │ 不保留 (用完即弃)        │
  │ 输出         │ 自然语言回复          │ 结构化 FlowResult        │
  │ M14 执行模式 │ ReAct + tool calling │ 结构化输出 / ReAct 自适应 │
  └──────────────┴──────────────────────┴──────────────────────────┘
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.standard.result import AgentResult, AgentStep
from .tool_registry import ToolRegistry

logger = logging.getLogger("emily.agent.specialist")

DEFAULT_MAX_ITERATIONS = 8


# ══════════════════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class FlowTask:
    """派发给 BusinessFlowAgent 的子任务。"""
    sop_id: str                     # SOP 编号
    user_input: str                 # 用户的原始输入（该子任务对应的部分）
    intent: str = ""                # 意图描述
    action: str = "execute"         # execute / confirm / modify / cancel
    context: dict = field(default_factory=dict)  # 附加上下文
    priority: int = 1               # 优先级（1=最高）


@dataclass
class FlowResult:
    """BusinessFlowAgent 执行完成后的结构化输出。"""
    success: bool
    sop_id: str
    result_data: dict = field(default_factory=dict)     # 执行结果数据
    compliance_report: str = ""                          # 合规报告
    actions_taken: list = field(default_factory=list)    # 执行的操作清单
    needs_user_input: bool = False                       # 是否需要用户进一步输入
    suggested_reply: str = ""                            # 建议的自然语言回复
    error_message: str = ""                              # 错误信息


# ══════════════════════════════════════════════════════════════════════════════
# SOPLoader — 按需加载 SOP 全文
# ══════════════════════════════════════════════════════════════════════════════


class SOPLoader:
    """按需加载 SOP 全文（§1-§7）。

    从 SOPrepository/ 目录中读取指定 SOP 的完整 Markdown 文件。
    不做摘要、不裁剪 —— Specialist 需要 SOP 全部内容来正确执行。
    """

    def __init__(self, sop_directory: str):
        self.sop_directory = Path(sop_directory)

    def load_full_text(self, sop_id: str) -> str | None:
        """加载指定 SOP 的全文。

        Args:
            sop_id: SOP 编号，如 "SOP-001-REC"

        Returns:
            SOP 文件全文，未找到返回 None
        """
        if not self.sop_directory.exists():
            logger.error("SOP directory not found: %s", self.sop_directory)
            return None

        # 按 sop_id 前缀匹配文件
        for file_path in sorted(self.sop_directory.glob(f"{sop_id}*.md")):
            try:
                return file_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error("Failed to read SOP file %s: %s", file_path, e)
                return None

        logger.warning("SOP file not found for sop_id=%s in %s", sop_id, self.sop_directory)
        return None

    def load_unmatched(self) -> str | None:
        """加载 unmatched.md 兜底指引。"""
        unmatched_path = (
            Path(__file__).parent.parent / "prompts" / "flows" / "unmatched.md"
        )
        try:
            return unmatched_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("unmatched.md not found at %s", unmatched_path)
            return None


# ══════════════════════════════════════════════════════════════════════════════
# BusinessFlowAgent
# ══════════════════════════════════════════════════════════════════════════════


class BusinessFlowAgent:
    """业务流专属执行 Agent（Specialist）。

    用法示例：
        loader = SOPLoader(sop_directory=".../SOPrepository")
        sop_text = loader.load_full_text("SOP-001-REC")
        agent = BusinessFlowAgent(
            llm_client=llm,
            sop_full_text=sop_text,
            allowed_tool_names=["record_meeting", "query_data"],
            tool_registry=registry,
        )
        result = await agent.execute(FlowTask(
            sop_id="SOP-001-REC",
            user_input="帮我录一份会议纪要...",
        ))
    """

    def __init__(
        self,
        llm_client,
        sop_full_text: str,
        allowed_tool_names: list[str],
        tool_registry: ToolRegistry,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        business_flow_tools=None,     # M14: BusinessFlowToolRegistry
    ):
        self.llm = llm_client
        self.sop_full_text = sop_full_text
        self.allowed_tool_names = allowed_tool_names
        self.tool_registry = tool_registry
        self._max_iterations = max_iterations
        self._business_flow_tools = business_flow_tools  # M14

    async def execute(self, task: FlowTask) -> FlowResult:
        """按 SOP 执行一个子任务。

        M14: 自动选择执行模式。
          - 如果 allowed_tool_names 中所有工具都在 business_flow_tools 中注册 →
            使用结构化输出模式（LLM 提取参数 → 框架直接执行）
          - 否则回退到传统 ReAct + tool calling 模式

        Args:
            task: 子任务描述

        Returns:
            FlowResult: 结构化的执行结果
        """
        # M14: 检测是否可用结构化输出模式
        if self._can_use_structured_mode():
            logger.info(
                "BusinessFlowAgent using structured output mode for SOP %s, tools=%s",
                task.sop_id, self.allowed_tool_names,
            )
            return await self._execute_structured(task)

        # 传统 ReAct 模式
        start_time = time.time()
        actions: list[str] = []

        # 1. 构建 system prompt
        system_prompt = self._build_specialist_prompt(task)

        # 2. 构建消息列表
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task.user_input},
        ]

        # 3. 获取限定工具列表
        tools = self._get_allowed_tools()

        # 4. ReAct 循环
        iteration = 0
        final_reply = ""

        try:
            while iteration < self._max_iterations:
                iteration += 1

                try:
                    llm_response = await self.llm.chat_with_tools(
                        messages=messages,
                        tools=tools,
                        temperature=0.3,
                    )
                except Exception as e:
                    logger.error("Specialist LLM call failed in iteration %d: %s", iteration, e)
                    return FlowResult(
                        success=False,
                        sop_id=task.sop_id,
                        error_message=f"LLM error: {e}",
                        actions_taken=actions,
                    )

                # ── 文本回复：完成 ──
                if llm_response.get("type") == "text":
                    final_reply = llm_response.get("content", "")
                    actions.append(f"reply: {final_reply[:100]}")
                    break

                # ── 工具调用：执行 ──
                if llm_response.get("type") == "tool_call":
                    tool_name = llm_response.get("tool_name", "")
                    tool_args = llm_response.get("tool_arguments", {})
                    tool_call_id = llm_response.get("tool_call_id", "")
                    reasoning_content = llm_response.get("reasoning_content", "")

                    actions.append(f"tool_call: {tool_name}")

                    # 检查工具是否在允许列表中（空列表 = 不限制）
                    if self.allowed_tool_names and tool_name not in self.allowed_tool_names:
                        logger.warning(
                            "Specialist attempted unauthorized tool: %s (allowed: %s)",
                            tool_name, self.allowed_tool_names,
                        )
                        tool_result = {
                            "success": False,
                            "error": f"工具 {tool_name} 不在当前 SOP 允许的工具集中",
                        }
                    else:
                        tool_result = await self._execute_tool(tool_name, tool_args)

                    # 序列化结果
                    try:
                        result_json = json.dumps(tool_result, ensure_ascii=False, default=str)
                    except (TypeError, ValueError):
                        result_json = json.dumps(
                            {"success": tool_result.get("success"),
                             "reply": str(tool_result.get("reply", ""))[:500]},
                            ensure_ascii=False,
                        )

                    # 添加到消息历史
                    assistant_msg = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False),
                            },
                        }],
                    }
                    if reasoning_content:
                        assistant_msg["reasoning_content"] = reasoning_content
                    messages.append(assistant_msg)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": result_json,
                    })

                    continue

        except Exception as e:
            logger.error("Specialist execution error: %s", e, exc_info=True)
            return FlowResult(
                success=False,
                sop_id=task.sop_id,
                error_message=str(e),
                actions_taken=actions,
            )

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "BusinessFlowAgent completed: sop_id=%s, %d iterations, %d ms",
            task.sop_id, iteration, elapsed_ms,
        )

        return FlowResult(
            success=True,
            sop_id=task.sop_id,
            result_data={"reply": final_reply},
            actions_taken=actions,
            suggested_reply=final_reply,
        )

    # ── M14: 结构化输出模式 ──

    def _can_use_structured_mode(self) -> bool:
        """检测是否可用结构化输出模式。

        条件：business_flow_tools 不为空，且 allowed_tool_names 中
        所有工具都在 business_flow_tools 中注册。
        """
        if self._business_flow_tools is None:
            return False
        if not self.allowed_tool_names:
            return False
        return all(
            self._business_flow_tools.has(name)
            for name in self.allowed_tool_names
        )

    async def _execute_structured(self, task: FlowTask) -> FlowResult:
        """结构化输出模式：LLM 提取参数 → 框架直接执行业务流工具。

        不经过 LLM function calling，而是要求 LLM 输出包含工具名和参数的 JSON，
        框架验证后直接调用 BusinessFlowTool.handler()。
        """
        start_time = time.time()
        actions: list[str] = []

        # 1. 获取业务流工具的 JSON Schema（注入 prompt）
        tool_schemas = self._business_flow_tools.get_tools_schema(
            self.allowed_tool_names,
        )
        tools_desc = self._format_tools_for_structured_prompt(tool_schemas)

        # 2. 构建 system prompt：SOP 全文 + 结构化输出指令
        system_prompt = self._build_structured_prompt(task, tools_desc)

        # 3. 调用 LLM（使用 chat_json 强制 JSON 输出）
        try:
            command = await self.llm.chat_json(
                system_prompt=system_prompt,
                user_message=task.user_input,
            )
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("Failed to parse structured output JSON: %s", e)
            # 回退：尝试用普通 chat 获取文本回复
            try:
                text_reply = await self.llm.chat(system_prompt, task.user_input)
            except Exception:
                text_reply = "抱歉，处理请求时遇到问题，请稍后重试。"
            return FlowResult(
                success=True,
                sop_id=task.sop_id,
                result_data={"reply": text_reply},
                actions_taken=["structured_parse_failed: fallback to text"],
                suggested_reply=text_reply,
            )
        except Exception as e:
            logger.error("Structured output LLM call failed: %s", e)
            return FlowResult(
                success=False,
                sop_id=task.sop_id,
                error_message=f"LLM error: {e}",
                actions_taken=actions,
            )

        tool_name = command.get("tool", "")
        params = command.get("params", {})
        user_message = command.get("user_message", "")
        needs_confirmation = command.get("needs_confirmation", False)

        actions.append(f"structured_tool: {tool_name}")

        # 5. 白名单校验
        if self.allowed_tool_names and tool_name not in self.allowed_tool_names:
            logger.warning(
                "Structured output requested unauthorized tool: %s (allowed: %s)",
                tool_name, self.allowed_tool_names,
            )
            return FlowResult(
                success=False,
                sop_id=task.sop_id,
                error_message=f"工具 {tool_name} 不在当前 SOP 允许的工具集中",
                actions_taken=actions,
            )

        # 6. 执行业务流工具
        tool = self._business_flow_tools.get(tool_name)
        if tool is None:
            return FlowResult(
                success=False,
                sop_id=task.sop_id,
                error_message=f"业务流工具未注册: {tool_name}",
                actions_taken=actions,
            )

        try:
            result = await tool.handler(params)
        except Exception as e:
            logger.error("Business flow tool '%s' failed: %s", tool_name, e)
            return FlowResult(
                success=False,
                sop_id=task.sop_id,
                error_message=str(e),
                actions_taken=actions,
            )

        actions.append(f"tool_result: {tool_name} success={result.get('success')}")

        # 7. 构建回复
        reply = result.get("reply", "")
        if user_message and not result.get("needs_review"):
            reply = user_message + ("\n" + reply if reply else "")

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "BusinessFlowAgent structured: sop_id=%s tool=%s %dms",
            task.sop_id, tool_name, elapsed_ms,
        )

        return FlowResult(
            success=result.get("success", True),
            sop_id=task.sop_id,
            result_data={
                "tool": tool_name,
                "tool_result": result,
                "user_message": user_message,
                "needs_confirmation": needs_confirmation,
            },
            actions_taken=actions,
            suggested_reply=reply,
            needs_user_input=needs_confirmation,
        )

    def _build_structured_prompt(self, task: FlowTask, tools_desc: str) -> str:
        """构建结构化输出的 system prompt。

        SOP 全文 + 可用工具 schema + 严格 JSON 输出指令。
        """
        return (
            f"{self.sop_full_text}\n\n"
            f"---\n\n"
            f"## 当前任务\n\n"
            f"用户请求：\n> {task.user_input}\n\n"
            f"---\n\n"
            f"## 可用业务流工具\n\n"
            f"{tools_desc}\n\n"
            f"---\n\n"
            f"## 输出要求（严格 JSON）\n\n"
            f"你必须**只输出**一个 JSON 对象，不要有任何其他文字、Markdown 标记或代码块。\n\n"
            f"JSON 格式：\n"
            f"```\n"
            f'{{"tool": "<工具名>", "params": {{...}}, "user_message": "<展示给用户的确认/回复>", "needs_confirmation": false}}\n'
            f"```\n\n"
            f"字段说明：\n"
            f"- tool: 要调用的工具名（必须是上面列出的工具之一）\n"
            f"- params: 工具的输入参数（严格按 JSON Schema 填写）\n"
            f"- user_message: 展示给用户的自然语言消息（如确认提示或操作结果说明）\n"
            f"- needs_confirmation: 是否需要用户确认再执行（true/false）\n\n"
            f"⚠️ 如果用户请求不明确、缺少必有字段、或需要用户确认，设置 needs_confirmation=true\n"
            f"   并在 user_message 中询问缺失信息。\n"
            f"⚠️ 只输出 JSON，不要加 ```json 代码块标记。"
        )

    @staticmethod
    def _format_tools_for_structured_prompt(tool_schemas: list[dict]) -> str:
        """将工具 schema 格式化注入 prompt。"""
        parts = []
        for ts in tool_schemas:
            parts.append(
                f"### {ts['name']}\n"
                f"{ts['description']}\n"
                f"参数 schema:\n```json\n{json.dumps(ts['parameters'], ensure_ascii=False, indent=2)}\n```\n"
            )
        return "\n".join(parts)

    @staticmethod
    def _parse_command_json(content: str) -> dict:
        """从 LLM 输出中解析命令 JSON。

        支持：纯 JSON、Markdown 代码块包裹的 JSON。
        """
        import re

        content = content.strip()

        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试从 Markdown 代码块提取
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if match:
            return json.loads(match.group(1).strip())

        # 尝试找到第一个 JSON 对象
        match = re.search(r'\{[^{}]*"tool"[^{}]*\}', content, re.DOTALL)
        if match:
            return json.loads(match.group(0))

        raise ValueError(f"无法从 LLM 输出中解析 JSON 命令: {content[:200]}")

    # ── 内部方法 ──

    def _build_specialist_prompt(self, task: FlowTask) -> str:
        """构建 Specialist 的 system prompt。

        System prompt = SOP 全文（§1-§7）+ 执行指令。
        """
        return (
            f"{self.sop_full_text}\n\n"
            f"---\n\n"
            f"## 当前任务\n\n"
            f"你正在处理以下用户请求，请严格按照上述 SOP 手册执行：\n\n"
            f"> {task.user_input}\n\n"
            f"执行完成后用自然语言回复结果。"
        )

    def _get_allowed_tools(self) -> list[dict]:
        """获取限定工具集的 OpenAI function calling 格式。"""
        tools = []
        for tool_name in self.allowed_tool_names:
            tool = self.tool_registry.get(tool_name)
            if tool is not None:
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                })
        return tools

    async def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """执行一个工具。"""
        tool = self.tool_registry.get(tool_name)
        if tool is None:
            return {"success": False, "error": f"未知工具: {tool_name}"}

        try:
            return await tool.execute(arguments)
        except Exception as e:
            logger.error("Specialist tool '%s' failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}
