"""MasterAgent —— 统一对话入口（ReAct 模式 + 发现式路由）。

M7 核心组件。MasterAgent 接收用户消息，通过 ReAct
（Reasoning + Acting）循环进行多步骤推理和工具调用，
最终生成自然语言回复。

M9: SOPIntentRegistry 发现式路由 — 扫描 SOPrepository/ 动态构建目录，
    注入 LLM system prompt，LLM 语义匹配 → SOPMatchDecision。
    sop_routing_logs 记录每次路由决策。

架构角色：
- MasterAgent 是唯一消息处理路径
- SOP 路由由 LLM 语义匹配动态决定（不再是硬编码分支）
- Specialist (BusinessFlowAgent) 按需加载 SOP 全文隔离执行

Dependencies:
- LLMClient (async, OpenAI SDK 封装)
- ToolRegistry (工具注册表)
- FlowMapManager (M7.1: Mermaid 决策树文件管理器)
- SOPIntentRegistry (M9: SOP 发现式路由注册表)
- 内部维护 ConversationContext 字典（按 conversation_id 索引）
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.standard.result import AgentResult, AgentStep
from ..config import Config
from ..infrastructure.llm.client import LLMClient
from .tool_registry import ToolRegistry
from .conversation_context import ConversationContext

logger = logging.getLogger("emily.agent.master")

# 最大 ReAct 循环迭代次数（安全阀，防止无限循环）
DEFAULT_MAX_ITERATIONS = 10


# ══════════════════════════════════════════════════════════════════════════════
# M9: SOPMatchDecision — LLM 语义匹配的结构化输出
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SOPMatchDecision:
    """LLM 对一条用户消息的语义匹配结论（由 Orchestrator 从 LLM 响应中解析）。"""
    sop_id: str | None = None           # 命中的 SOP 编号；未命中则为 None
    display_name: str = ""              # 匹配到的业务名称
    confidence: str = "none"            # "high" / "medium" / "low" / "none"
    reasoning: str = ""                 # LLM 匹配推理简述
    is_compound: bool = False           # 是否复合意图
    sub_tasks: list = field(default_factory=list)  # 子任务列表
    fallback: bool = False              # 是否触发兜底


class MasterAgent:
    """Emy 主 Agent —— ReAct 模式对话引擎 + 发现式路由。

    每个请求创建新实例（因 Tool 需捕获 user_id/message_id）。
    对话上下文按 conversation_id 在类级别共享（_contexts 类变量）。

    M9: SOPIntentRegistry 注入 → 发现式路由 → LLM 语义匹配。

    Args:
        llm_client: LLM 客户端
        tool_registry: 工具注册表
        config: 全局配置
        user_id: 当前用户系统 ID
        message_id: 当前消息 DB ID
        sender_name: 发送者显示名
        sop_intent_registry: M9 SOP 意图注册表
        flow_map_manager: M7.1 Mermaid 决策树文件管理器
        user_memory_service: M8c 用户长期记忆服务
    """

    # 类级别：跨请求共享的对话上下文
    _contexts: dict[str, ConversationContext] = {}

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        config: Config,
        user_id: str = "",
        message_id: str = "",
        sender_name: str = "",
        sop_intent_registry=None,     # M9: SOP 意图注册表
        flow_map_manager=None,        # M7.1: Mermaid 决策树文件管理器
        user_memory_service=None,     # M8c: 用户长期记忆服务
        agent_trace_service=None,     # M11: Agent 追踪服务
        query_service=None,           # M9 refactor: GuardianAgent 需要（替代原 invoke_guardian 工具）
        business_flow_tools=None,     # M14: 业务流工具注册表（框架直接执行）
    ):
        self.llm = llm_client
        self.tool_registry = tool_registry
        self.sop_intent_registry = sop_intent_registry  # M9
        self.flow_map_manager = flow_map_manager          # M7.1
        self.config = config
        self.user_id = user_id
        self.message_id = message_id
        self.sender_name = sender_name
        self.user_memory_service = user_memory_service    # M8c
        self.agent_trace_service = agent_trace_service    # M11
        self.query_service = query_service                # M9 refactor: GuardianAgent 依赖
        self.business_flow_tools = business_flow_tools    # M14

        self._max_iterations = getattr(config, "agent_max_iterations", DEFAULT_MAX_ITERATIONS)
        self._context_max_turns = getattr(config, "agent_context_max_turns", 10)
        self._context_ttl = getattr(config, "agent_context_ttl_seconds", 600)

        # M7.1: 访问追踪（实例级别，避免多用户并发干扰）
        self._visited_flows: set[str] = set()

        # System prompt 缓存
        self._system_prompt_template: str | None = None
        self._prompt_loaded = False

        # M9: 路由日志写入回调（由外部注入）
        self._routing_log_writer: callable | None = None

    def set_routing_log_writer(self, writer: callable) -> None:
        """M9: 注入路由日志写入回调。

        Args:
            writer: async fn(decision: SOPMatchDecision, user_message: str,
                             conversation_id: str, execution_result: str) -> None
        """
        self._routing_log_writer = writer

    # ── 主入口 ──

    async def run(
        self,
        user_message: str,
        user_id: str,
        message_id: str,
        conversation_id: str,
        sender_name: str = "",
        platform: str = "",
    ) -> AgentResult:
        """执行 ReAct 循环，处理一条用户消息。

        M9: 在 system prompt 中注入 SOP 目录，LLM 做语义匹配后路由。
        """
        start_time = time.time()
        steps: list[AgentStep] = []
        step_index = 0

        # M11: 创建推理日志
        trace_id = None
        if self.agent_trace_service:
            trace_id = self.agent_trace_service.create_reasoning_log(
                message_id=message_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )

        # 入口清理：每次请求时清除过期上下文，防止内存泄漏
        MasterAgent.clear_expired_contexts()

        # 更新实例属性
        self.user_id = user_id
        self.message_id = message_id
        self.sender_name = sender_name
        self.platform = platform

        # 1. 获取或创建对话上下文
        ctx = self._get_or_create_context(conversation_id, user_id)

        # 2. 将用户消息添加到上下文
        ctx.add_turn("user", user_message)

        # 3. 检查是否为管理员
        is_admin = self._is_admin(user_id)

        # 4. 构建系统提示词（含 M9 SOP 目录 + M8c 长期记忆）
        system_prompt = self._build_system_prompt(
            is_admin,
            memory_context=getattr(ctx, "user_memory_context", ""),
        )

        # 5. 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(ctx.get_messages_for_llm())

        # 6. ReAct 循环
        iteration = 0
        final_reply = ""
        match_decision: SOPMatchDecision | None = None
        execution_result = ""

        while iteration < self._max_iterations:
            iteration += 1

            try:
                llm_response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=self.tool_registry.get_openai_tools(admin=is_admin),
                    temperature=0.3,
                )
            except Exception as e:
                logger.error("LLM call failed in iteration %d: %s", iteration, e)
                steps.append(AgentStep(
                    step_index=step_index,
                    type="respond",
                    detail=f"LLM error: {e}",
                    timestamp=self._now(),
                ))
                final_reply = "抱歉，处理您的请求时遇到了问题，请稍后重试。"
                execution_result = "failed"
                break

            # ── 文本回复：结束循环 ──
            if llm_response.get("type") == "text":
                content = llm_response.get("content", "")

                # M9: 首轮文本回复可能是 LLM 的匹配决策（JSON），尝试解析
                if iteration == 1 and not match_decision:
                    parsed_decision = self._try_parse_match_decision(content)
                    if parsed_decision:
                        match_decision = parsed_decision
                        logger.info(
                            "SOP match decision: sop_id=%s confidence=%s compound=%s fallback=%s",
                            match_decision.sop_id,
                            match_decision.confidence,
                            match_decision.is_compound,
                            match_decision.fallback,
                        )

                        # M9: 复合请求 → 代码级拆解派发，比 LLM 自己逐一调用更可靠
                        if match_decision.is_compound and match_decision.sub_tasks:
                            compound_results = await self._decompose_and_dispatch(
                                match_decision, user_message, is_admin,
                            )
                            execution_result = "compound_dispatched"
                            # 将结果注入消息上下文，让 LLM 合成最终回复
                            results_text = json.dumps(
                                compound_results, ensure_ascii=False, default=str,
                            )
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"复合请求拆解执行完成。以下是各子任务的执行结果（JSON）：\n"
                                    f"{results_text}\n\n"
                                    f"请根据这些结果，合成一个简洁的自然语言回复给用户，"
                                    f"汇总各子任务的完成情况。"
                                ),
                            })
                            continue  # 继续 ReAct 循环，让 LLM 合成最终回复

                        # 单 SOP 命中（非兜底）→ 框架自动派发 Specialist
                        if (match_decision.sop_id
                            and not match_decision.fallback
                            and match_decision.confidence in ("high", "medium")):
                            # SOP-006 特殊处理：调用 GuardianAgent 而非 Specialist
                            if match_decision.sop_id == "SOP-006-FLOW":
                                specialist_result = await self._invoke_guardian(user_message)
                            else:
                                specialist_result = await self._dispatch_specialist(
                                    sop_id=match_decision.sop_id,
                                    user_input=user_message,
                                    intent=match_decision.reasoning,
                                )
                            specialist_json = json.dumps(
                                specialist_result, ensure_ascii=False, default=str,
                            )
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"SOP 派发完成。执行结果（JSON）：\n"
                                    f"{specialist_json}\n\n"
                                    f"请根据结果合成简洁的自然语言回复给用户。"
                                ),
                            })
                            execution_result = "specialist_dispatched"
                            continue  # 继续 ReAct 循环让 LLM 合成回复

                        # 兜底或低置信度 → 继续循环让 LLM 使用原子工具自由推理
                        continue

                steps.append(AgentStep(
                    step_index=step_index,
                    type="respond",
                    detail=content[:200],
                    timestamp=self._now(),
                ))
                ctx.add_turn("assistant", content)
                final_reply = content
                if not execution_result:
                    execution_result = "success"
                break

            # ── 工具调用：执行 ──
            if llm_response.get("type") == "tool_call":
                tool_name = llm_response.get("tool_name", "")
                tool_args = llm_response.get("tool_arguments", {})
                tool_call_id = llm_response.get("tool_call_id", "")
                reasoning_content = llm_response.get("reasoning_content", "")

                step_index += 1
                steps.append(AgentStep(
                    step_index=step_index,
                    type="tool_call",
                    detail=f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})",
                    timestamp=self._now(),
                ))

                # 执行工具
                tool_result = await self._execute_tool(tool_name, tool_args)

                # 安全序列化
                try:
                    result_json = json.dumps(tool_result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    result_json = json.dumps(
                        {"success": tool_result.get("success"),
                         "reply": str(tool_result.get("reply", ""))[:500]},
                        ensure_ascii=False,
                    )

                steps.append(AgentStep(
                    step_index=step_index,
                    type="tool_result",
                    detail=result_json[:300],
                    timestamp=self._now(),
                ))

                # 将 tool_call 和 result 添加到 messages
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
                # DeepSeek thinking mode: 必须在 assistant msg 中回传 reasoning_content，
                # 否则下一轮 API 调用返回 400
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content

                messages.append(assistant_msg)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_json,
                })

                # 同步到对话上下文
                ctx.add_turn(
                    "assistant", "",
                    tool_name=tool_name, tool_call_id=tool_call_id,
                    reasoning_content=reasoning_content,
                )
                ctx.add_turn(
                    "tool", json.dumps(tool_result, ensure_ascii=False),
                    tool_name=tool_name, tool_call_id=tool_call_id,
                )

                continue

        # ── 超过最大迭代次数 ──
        if iteration >= self._max_iterations and not final_reply:
            logger.warning("Max iterations reached (%d)", self._max_iterations)
            final_reply = "抱歉，处理您的请求花费了太长时间，请简化后重试。"
            ctx.add_turn("assistant", final_reply)
            execution_result = "failed"

        # M9: 写入路由日志（异步，不阻塞回复）
        if self._routing_log_writer is not None and match_decision is not None:
            try:
                await self._routing_log_writer(
                    match_decision, user_message, conversation_id, execution_result,
                )
            except Exception as e:
                logger.warning("Failed to write routing log: %s", e)

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "MasterAgent.run completed: %d iterations, %d ms, reply=%d chars",
            iteration, elapsed_ms, len(final_reply),
        )

        # M11: 最终化推理日志
        if self.agent_trace_service and trace_id:
            self.agent_trace_service.finalize_reasoning_log(
                reasoning_log_id=trace_id,
                iteration_count=iteration,
                elapsed_ms=elapsed_ms,
                matched_sop_id=match_decision.sop_id if match_decision else None,
                match_confidence=match_decision.confidence if match_decision else "none",
                is_compound=match_decision.is_compound if match_decision else False,
                fallback=match_decision.fallback if match_decision else False,
                execution_result=execution_result or "success",
                reply_preview=final_reply[:500] if final_reply else "",
                steps=steps,
                error_message="",
                max_iterations_reached=(iteration >= self._max_iterations and not final_reply),
            )

        return AgentResult(
            success=True,
            reply=final_reply,
            steps=steps,
        )

    # ── M9: 匹配决策解析 ──

    @staticmethod
    def _try_parse_match_decision(content: str) -> SOPMatchDecision | None:
        """尝试从 LLM 文本回复中解析 SOPMatchDecision JSON。

        当 LLM 首轮输出是匹配决策 JSON 时，解析为结构化对象。
        如果内容不是 JSON 或不包含匹配字段，返回 None。
        """
        if not content:
            return None

        # 尝试提取 JSON（可能在文本中嵌入）
        json_str = content.strip()

        # 去掉可能的 markdown 代码块标记
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            json_str = "\n".join(lines).strip()

        # 尝试找到 JSON 对象
        if not json_str.startswith("{"):
            match = re.search(r"\{[^{}]*\"sop_id\"[^{}]*\}", content, re.DOTALL)
            if match:
                json_str = match.group(0)
            else:
                match = re.search(r"\{[^{}]*\"fallback\"[^{}]*\}", content, re.DOTALL)
                if match:
                    json_str = match.group(0)
                else:
                    return None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None

        # 必须有 sop_id 或 fallback 字段才是匹配决策
        if "sop_id" not in data and "fallback" not in data:
            return None

        return SOPMatchDecision(
            sop_id=data.get("sop_id"),
            display_name=data.get("display_name", ""),
            confidence=data.get("confidence", "none"),
            reasoning=data.get("reasoning", ""),
            is_compound=data.get("is_compound", False),
            sub_tasks=data.get("sub_tasks", []),
            fallback=data.get("fallback", False),
        )

    # ── 工具执行 ──

    async def _execute_tool(self, tool_name: str, arguments: dict) -> dict:
        """执行一个工具并返回结果。"""
        tool = self.tool_registry.get(tool_name)
        if tool is None:
            logger.warning("Unknown tool requested: %s", tool_name)
            return {"success": False, "error": f"未知工具: {tool_name}"}

        # M11: 创建工具调用日志
        tool_trace_id = None
        t0 = time.time()
        if self.agent_trace_service:
            try:
                tool_trace_id = self.agent_trace_service.create_tool_call_log(
                    reasoning_log_id=getattr(self, "_current_trace_id", None),
                    llm_interaction_id=getattr(self, "_current_llm_trace_id", None),
                    step_index=getattr(self, "_current_step_index", 0),
                    tool_name=tool_name,
                    tool_arguments=arguments,
                )
            except Exception:
                pass

        try:
            result = await tool.execute(arguments)
            elapsed = int((time.time() - t0) * 1000)

            # M11: 更新工具调用结果
            if self.agent_trace_service and tool_trace_id:
                from json import dumps
                summary = dumps(result, ensure_ascii=False, default=str)[:500]
                try:
                    self.agent_trace_service.update_tool_result(
                        tool_call_log_id=tool_trace_id,
                        result_summary=summary,
                        is_success=result.get("success", True) if isinstance(result, dict) else True,
                        error_message=result.get("error", "") if isinstance(result, dict) else "",
                        elapsed_ms=elapsed,
                    )
                except Exception:
                    pass

            return result
        except Exception as e:
            elapsed = int((time.time() - t0) * 1000)

            # M11: 记录失败
            if self.agent_trace_service and tool_trace_id:
                try:
                    self.agent_trace_service.update_tool_result(
                        tool_call_log_id=tool_trace_id,
                        result_summary=f"Exception: {e}",
                        is_success=False,
                        error_message=str(e)[:500],
                        elapsed_ms=elapsed,
                    )
                except Exception:
                    pass

            logger.error("Tool '%s' execution failed: %s", tool_name, e, exc_info=True)
            return {"success": False, "error": str(e)}

    # ── System Prompt ──

    def _build_system_prompt(
        self, is_admin: bool, memory_context: str = ""
    ) -> str:
        """构建完整的系统提示词。

        加载 master_agent.txt，替换占位符：
        - {TOOL_LIST}: 可用工具列表
        - {SOP_CATALOG}: M9 SOP 意图目录（来自 SOPIntentRegistry.dump_as_text()）
        - {ROOT_FLOW}: M7.1 根决策树图
        - {CURRENT_DATETIME}: 当前时间
        - {USER_MEMORY}: M8c 用户长期记忆上下文
        - {PLATFORM}: 当前 IM 平台
        """
        template = self._load_prompt_template()

        # 工具列表
        tool_descriptions = []
        for tool in self.tool_registry.list_all():
            if tool.require_admin and not is_admin:
                continue
            tool_descriptions.append(f"- **{tool.name}**: {tool.description}")
        tool_list = "\n".join(tool_descriptions) if tool_descriptions else "（无可用工具）"

        # M9: SOP 意图目录（来自 SOPIntentRegistry）
        sop_catalog = ""
        if self.sop_intent_registry is not None:
            try:
                sop_catalog = self.sop_intent_registry.dump_as_text()
                if sop_catalog:
                    logger.debug("SOP catalog loaded: %d chars", len(sop_catalog))
                else:
                    sop_catalog = "（暂无已加载的业务流程）"
            except Exception as e:
                logger.warning("Failed to dump SOP catalog: %s", e)
                sop_catalog = "（SOP 目录加载失败）"

        # M7.1: 根决策树图
        root_flow_text = ""
        if self.flow_map_manager is not None:
            root_flow_text = self.flow_map_manager.get_root_flow_text()
            if root_flow_text:
                logger.debug("Root flow loaded: %d chars", len(root_flow_text))

        # M7.1: 子决策树图（非根图全部注入 prompt，替代原 read_flow_diagram 工具）
        sub_flows_text = ""
        if self.flow_map_manager is not None:
            sub_flows_text = self.flow_map_manager.get_all_sub_flows_text()
            if sub_flows_text:
                logger.debug("Sub-flows loaded: %d chars", len(sub_flows_text))

        # 当前时间（北京时间）
        from ..infrastructure.database.models import BEIJING_TZ
        beijing_time = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

        # M8c: 用户长期记忆上下文
        user_memory_section = ""
        if memory_context:
            user_memory_section = (
                "\n## 用户长期工作要求\n\n"
                "以下是该用户之前表达的长期工作要求，请在工作中记住这些偏好：\n\n"
                + memory_context
                + "\n"
            )

        # M10 L1: 地产领域核心认知（骨架知识）
        domain_knowledge = ""
        try:
            dk_override = getattr(self.config, "domain_knowledge_path", "") if self.config else ""
            if dk_override:
                dk_path = Path(dk_override)
            else:
                dk_path = Path(__file__).parent.parent / "prompts" / "domain_knowledge.md"
            if dk_path.exists():
                domain_knowledge = dk_path.read_text(encoding="utf-8")
                logger.debug("L1 domain knowledge loaded: %d chars", len(domain_knowledge))
            else:
                domain_knowledge = "（领域知识文件未生成，请运行 knowledge_builder.py）"
                logger.warning("L1 domain_knowledge.md not found at %s", dk_path)
        except Exception as e:
            logger.warning("Failed to load L1 domain knowledge: %s", e)
            domain_knowledge = "（领域知识加载失败）"

        return (
            template
            .replace("{TOOL_LIST}", tool_list)
            .replace("{SOP_CATALOG}", sop_catalog)
            .replace("{ROOT_FLOW}", root_flow_text)
            .replace("{SUB_FLOWS}", sub_flows_text)
            .replace("{CURRENT_DATETIME}", beijing_time)
            .replace("{PLATFORM}", getattr(self, "platform", "") or "unknown")
            .replace("{USER_MEMORY}", user_memory_section)
            .replace("{DOMAIN_KNOWLEDGE}", domain_knowledge)
        )

    def _load_prompt_template(self) -> str:
        """加载 MasterAgent System Prompt 模板（缓存）。"""
        if self._prompt_loaded and self._system_prompt_template:
            return self._system_prompt_template

        prompt_path_override = getattr(self.config, "master_agent_prompt_path", "") if self.config else ""
        if prompt_path_override:
            prompt_path = Path(prompt_path_override)
        else:
            prompt_path = Path(__file__).parent.parent / "prompts" / "master_agent.txt"
        try:
            self._system_prompt_template = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("MasterAgent prompt not found: %s", prompt_path)
            self._system_prompt_template = (
                "你是 Emy，一个工程项目管理助手。请友好、简洁地回复用户的消息。"
            )

        self._prompt_loaded = True
        return self._system_prompt_template

    # ── 对话上下文 ──

    def _get_or_create_context(
        self,
        conversation_id: str,
        user_id: str,
    ) -> ConversationContext:
        """获取或创建对话上下文。"""
        ctx = self._contexts.get(conversation_id)

        if ctx is None or ctx.is_expired():
            self._visited_flows.clear()

            # M8c: 加载用户长期记忆
            memory_context = ""
            if self.user_memory_service is not None:
                try:
                    user_name = self.sender_name or ""
                    if user_name:
                        memory_context = self.user_memory_service.load_memory_context(user_name)
                        if memory_context:
                            logger.info(
                                "M8c memory context loaded for user %s: %d chars",
                                user_name, len(memory_context),
                            )
                except Exception as e:
                    logger.warning("M8c failed to load memory context: %s", e)

            ctx = ConversationContext(
                conversation_id=conversation_id,
                user_id=user_id,
                max_turns=self._context_max_turns,
                ttl_seconds=self._context_ttl,
                user_memory_context=memory_context,
            )
            self._contexts[conversation_id] = ctx

        return ctx

    @classmethod
    def clear_expired_contexts(cls) -> int:
        """清理所有过期的对话上下文。"""
        expired_ids = [
            cid for cid, ctx in cls._contexts.items()
            if ctx.is_expired()
        ]
        for cid in expired_ids:
            del cls._contexts[cid]
        if expired_ids:
            logger.debug("Cleared %d expired contexts", len(expired_ids))
        return len(expired_ids)

    # ── M9: 请求分解与编排 ──

    async def _decompose_and_dispatch(
        self,
        match_decision: SOPMatchDecision,
        user_message: str,
        is_admin: bool,
    ) -> list[dict]:
        """将复合请求拆解为子任务并并行/串行派发。

        Args:
            match_decision: LLM 匹配决策（is_compound=True）
            user_message: 用户原始消息
            is_admin: 当前用户是否为管理员

        Returns:
            每个子任务的执行结果列表
        """
        if not match_decision.sub_tasks:
            logger.warning("Compound request with no sub_tasks, treating as single")
            return []

        results = []
        for i, sub in enumerate(match_decision.sub_tasks):
            sub_sop_id = sub.get("sop_id", "")
            sub_input = sub.get("user_input", user_message)
            sub_priority = sub.get("priority", 1)

            if not sub_sop_id:
                logger.warning("Sub-task %d missing sop_id, skipping", i)
                continue

            # 权限检查
            if self.sop_intent_registry is not None:
                spec = self.sop_intent_registry.get_spec(sub_sop_id)
                if spec is not None:
                    user_role = "admin" if is_admin else "all"
                    if (
                        user_role not in spec.allow_roles
                        and "all" not in spec.allow_roles
                    ):
                        logger.warning(
                            "Permission denied for sop_id=%s, user_role=%s, allowed=%s",
                            sub_sop_id, user_role, spec.allow_roles,
                        )
                        results.append({
                            "sub_task_index": i,
                            "sop_id": sub_sop_id,
                            "success": False,
                            "error": f"权限不足：SOP {sub_sop_id} 需要角色 {list(spec.allow_roles)}",
                        })
                        continue

            # 调用 Specialist（框架内置方法，非 LLM 工具）
            result = await self._dispatch_specialist(
                sop_id=sub_sop_id,
                user_input=sub_input,
                intent=sub.get("intent", ""),
                action="execute",
            )

            results.append({
                "sub_task_index": i,
                "sop_id": sub_sop_id,
                **result,
            })

        return results

    # ── M9: Specialist 派发（框架内置能力，替代原 invoke_business_flow 工具）──

    async def _dispatch_specialist(
        self, sop_id: str, user_input: str,
        intent: str = "", action: str = "execute",
    ) -> dict:
        """派发子任务给 BusinessFlowAgent（框架内置能力，非 LLM 工具）。

        M9 架构重构：原 invoke_business_flow 工具降级为 MasterAgent 内置方法。
        LLM 首轮输出 SOPMatchDecision 后，框架据此自动派发 Specialist，
        无需 LLM 通过 tool calling 再次调用 dispatch 工具。
        """
        if not sop_id:
            return {"success": False, "error": "sop_id 不能为空"}
        if not user_input:
            return {"success": False, "error": "user_input 不能为空"}

        # 验证 SOP 是否存在
        if self.sop_intent_registry is None:
            return {"success": False, "error": "SOP 注册表未初始化"}
        spec = self.sop_intent_registry.get_spec(sop_id)
        if spec is None:
            available = self.sop_intent_registry.list_loaded_sops()
            return {
                "success": False,
                "error": f"SOP '{sop_id}' 不存在。当前可用 SOP: {', '.join(available)}",
            }

        # 用 SOPLoader 加载全文
        from .business_flow_agent import BusinessFlowAgent, FlowTask, SOPLoader

        sop_dir = getattr(self.config, "sop_repository_dir", "") if self.config else ""
        if not sop_dir:
            from pathlib import Path
            sop_dir = str(Path(__file__).parent.parent / "SOPrepository")
        loader = SOPLoader(sop_directory=sop_dir)
        sop_text = loader.load_full_text(sop_id)
        if sop_text is None:
            logger.warning("SOP full text not found for %s, using catalog info", sop_id)
            sop_text = (
                f"# {spec.display_name}\n\n"
                f"业务流编号: {spec.sop_id}\n"
                f"版本: {spec.sop_version}\n"
                f"类型: {spec.sop_type}\n\n"
                f"## 触发条件\n\n"
                + "\n".join(f"- {k}" for k in spec.trigger_keywords)
                + "\n\n"
                + "（完整 SOP 文件未找到，请根据触发条件和工具描述自由推理执行）\n"
            )

        # 从 SOP §3.2 表格提取允许的工具名
        from ..tools.business_flow_tool import _extract_allowed_tools_from_sop
        allowed_tool_names = _extract_allowed_tools_from_sop(sop_text)

        # 创建 Specialist
        specialist = BusinessFlowAgent(
            llm_client=self.llm,
            sop_full_text=sop_text,
            allowed_tool_names=allowed_tool_names,
            tool_registry=self.tool_registry,
            business_flow_tools=self.business_flow_tools,  # M14
        )

        # 执行
        task = FlowTask(
            sop_id=sop_id,
            user_input=user_input,
            intent=intent,
            action=action,
        )
        result = await specialist.execute(task)

        return {
            "success": result.success,
            "sop_id": result.sop_id,
            "reply": result.suggested_reply,
            "actions_taken": result.actions_taken,
            "error_message": result.error_message,
        }

    # ── M9: Guardian 调查（框架内置能力，替代原 invoke_guardian 工具）──

    async def _invoke_guardian(self, query: str) -> dict:
        """调用 GuardianAgent 执行深度调查（框架内置能力，非 LLM 工具）。

        M9 架构重构：原 invoke_guardian 工具降级为 MasterAgent 内置方法。
        当 SOP-006 匹配或 deep_audit hook 触发时，框架自动调用此方法，
        无需 LLM 通过 tool calling 决定是否调查。
        """
        if not query or not query.strip():
            return {"success": False, "error": "query 参数不能为空"}

        if self.llm is None:
            return {"success": False, "error": "LLM 未配置，无法执行深度调查"}

        from .guardian_agent import GuardianAgent

        notebook_dir = getattr(self.config, "notebook_dir", "") if self.config else ""
        prompt_path = getattr(self.config, "guardian_prompt_path", "") if self.config else ""

        agent = GuardianAgent(
            llm_client=self.llm,
            query_service=self.query_service,
            config=self.config,
            notebook_dir=notebook_dir,
            prompt_path=prompt_path,
            journal=None,
        )

        try:
            result = await agent.investigate(query)
            return {
                "success": result.success,
                "reply": result.report,
                "error_code": result.error_code,
                "steps_count": len(result.steps),
            }
        except Exception as e:
            logger.error(
                "GuardianAgent investigation failed: %s", e, exc_info=True,
            )
            return {"success": False, "error": str(e)}

    # ── 辅助方法 ──

    def _is_admin(self, user_id: str) -> bool:
        """检查用户是否为管理员。"""
        if not user_id:
            return False

        try:
            from ..repositories.user_repo import UserRepository
            user = UserRepository.get(user_id)
            if user and getattr(user, "is_admin", False):
                return True
        except Exception as e:
            logger.debug("Admin check failed for user=%s: %s", user_id, e)

        return False

    @staticmethod
    def _now() -> str:
        """返回当前 UTC ISO 时间戳字符串。"""
        return datetime.now(timezone.utc).isoformat()

