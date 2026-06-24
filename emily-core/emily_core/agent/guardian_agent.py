"""GuardianAgent —— 一次性深度调查 ReAct Agent。

M6 component. GuardianAgent is invoked by MasterAgent through the
invoke_guardian tool. It performs multi-step data investigation using
query_data and write_notebook tools, then returns a comprehensive report.

与 MasterAgent 的关键区别:
- 无 ConversationContext（无状态，一次性）
- 无 SkillRegistry（只读查询 + 文件写入）
- 无 admin 检查（所有人都能巡检）
- 专属 ToolRegistry: query_data + write_notebook
- Prompt 只从 prompts/守护Agent.md 加载
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.standard.result import AgentStep
from ..config import Config
from ..infrastructure.llm.client import LLMClient
from ..services.query_service import QueryService
from ..agent.tool_registry import ToolRegistry
from ..tools.query_tool import create_query_tool
from ..tools.notebook_tool import create_notebook_tool

logger = logging.getLogger("emily.agent.guardian")

DEFAULT_MAX_ITERATIONS = 5


@dataclass
class GuardianResult:
    """GuardianAgent 调查结果。

    Attributes:
        success: 调查是否完成（LLM 返回了文本回复）
        report: LLM 最终生成的调查报告
        steps: 执行步骤记录（供调试）
        error_code: 失败原因代码
    """

    success: bool
    report: str
    steps: list[AgentStep] = field(default_factory=list)
    error_code: str | None = None


class GuardianAgent:
    """一次性深度调查 ReAct Agent。

    每次调查创建新实例，执行完毕后销毁。
    与 MasterAgent 共享同一个 LLMClient 实例（复用 API Key）。

    Args:
        llm_client: 共享的 LLM 客户端实例
        query_service: 共享的 QueryService 实例
        config: 全局配置
    """

    def __init__(
        self,
        llm_client: LLMClient,
        query_service: QueryService,
        config: Config,
        notebook_dir: str = "",
        prompt_path: str = "",
        journal=None,  # M8c: EventJournal
    ):
        self.llm = llm_client
        self.query_service = query_service
        self.config = config
        self.notebook_dir = notebook_dir
        self.prompt_path_override = prompt_path
        self.journal = journal  # M8c
        self._max_iterations = getattr(config, "guardian_max_iterations", DEFAULT_MAX_ITERATIONS) if config else DEFAULT_MAX_ITERATIONS

        # System prompt 缓存
        self._system_prompt_template: str | None = None
        self._prompt_loaded = False

    # ── 主入口 ──

    async def investigate(self, query: str) -> GuardianResult:
        """对所给查询执行深度调查。

        执行 ReAct 循环: LLM 可多次调用 query_data 或 write_notebook，
        交叉引用不同数据维度，最终生成自然语言报告。

        Args:
            query: 调查问题（用户的原始请求）

        Returns:
            GuardianResult 含调查报告和执行步骤
        """
        start_time = time.time()
        steps: list[AgentStep] = []
        step_index = 0

        # 1. 从 守护Agent.md 构建 System Prompt
        system_prompt = self._build_system_prompt()

        # 2. 构建消息列表（无对话历史 —— 一次性）
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

        # 3. 创建专属 ToolRegistry（仅 query_data + write_notebook）
        tool_registry = ToolRegistry()
        tool_registry.register(create_query_tool(self.query_service))
        tool_registry.register(create_notebook_tool(self.notebook_dir))
        tools = tool_registry.get_openai_tools(admin=False)

        # 4. ReAct 循环
        iteration = 0
        final_report = ""
        _called: set[str] = set()  # 防重复调用 (tool_name + sorted_args)

        while iteration < self._max_iterations:
            iteration += 1

            try:
                llm_response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=0.3,
                )
            except Exception as e:
                logger.error(
                    "GuardianAgent LLM call failed in iteration %d: %s",
                    iteration, e,
                )
                steps.append(AgentStep(
                    step_index=step_index,
                    type="respond",
                    detail=f"LLM error: {e}",
                    timestamp=self._now(),
                ))
                final_report = "抱歉，执行调查时遇到了问题，请稍后重试。"
                break

            # ── 文本回复: 调查完成 ──
            if llm_response.get("type") == "text":
                content = llm_response.get("content", "")
                steps.append(AgentStep(
                    step_index=step_index,
                    type="respond",
                    detail=content[:200],
                    timestamp=self._now(),
                ))
                final_report = content
                break

            # ── 工具调用: 执行 ──
            if llm_response.get("type") == "tool_call":
                tool_name = llm_response.get("tool_name", "")
                tool_args = llm_response.get("tool_arguments", {})
                tool_call_id = llm_response.get("tool_call_id", "")
                reasoning_content = llm_response.get("reasoning_content", "")

                # 重复调用检测：同样工具+同样参数 → 拦截
                call_key = f"{tool_name}:{json.dumps(tool_args, ensure_ascii=False, sort_keys=True)}"
                if call_key in _called:
                    logger.warning(
                        "GuardianAgent duplicate tool call blocked: %s", call_key,
                    )
                    steps.append(AgentStep(
                        step_index=step_index,
                        type="tool_call",
                        detail=f"⛔ DUPLICATE BLOCKED: {tool_name}",
                        timestamp=self._now(),
                    ))
                    # 注入 fake tool result 告知 LLM 这是重复调用
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
                        "content": json.dumps(
                            {"success": False, "error": "重复调用，刚才已获取相同数据，请基于已有结果直接输出报告。"},
                            ensure_ascii=False,
                        ),
                    })
                    continue

                _called.add(call_key)

                step_index += 1
                steps.append(AgentStep(
                    step_index=step_index,
                    type="tool_call",
                    detail=f"{tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})",
                    timestamp=self._now(),
                ))

                tool_result = await self._execute_tool(
                    tool_registry, tool_name, tool_args,
                )

                # 安全序列化 tool_result 用于日志（避免 ORM 对象炸 json.dumps）
                try:
                    result_detail = json.dumps(tool_result, ensure_ascii=False, default=str)[:300]
                except (TypeError, ValueError):
                    result_detail = f"{{success={tool_result.get('success')}, reply={str(tool_result.get('reply', ''))[:200]}}}"

                steps.append(AgentStep(
                    step_index=step_index,
                    type="tool_result",
                    detail=result_detail,
                    timestamp=self._now(),
                ))

                # 将 assistant tool_call + tool 结果追加���消息列表
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
                # 安全序列化 tool_result（避免 ORM 对象炸 json.dumps）
                try:
                    result_content = json.dumps(tool_result, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    result_content = json.dumps(
                        {"success": tool_result.get("success"),
                         "reply": str(tool_result.get("reply", ""))[:500]},
                        ensure_ascii=False)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_content,
                })

                continue

        # ── 超出最大迭代次数 ──
        if iteration >= self._max_iterations and not final_report:
            logger.warning(
                "GuardianAgent max iterations reached (%d)",
                self._max_iterations,
            )
            final_report = "抱歉，调查花费了太长时间，请尝试简化问题后重试。"

        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "GuardianAgent.investigate completed: %d iterations, %d ms, report=%d chars",
            iteration, elapsed_ms, len(final_report),
        )

        return GuardianResult(
            success=bool(final_report),
            report=final_report,
            steps=steps,
            error_code=None if final_report else "max_iterations",
        )

    # ── 工具执行 ──

    async def _execute_tool(
        self,
        tool_registry: ToolRegistry,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        """执行工具并返回结果字典。"""
        tool = tool_registry.get(tool_name)
        if tool is None:
            logger.warning(
                "GuardianAgent: unknown tool requested: %s", tool_name,
            )
            return {"success": False, "error": f"未知工具: {tool_name}"}

        try:
            result = await tool.execute(arguments)
            return result
        except Exception as e:
            logger.error(
                "GuardianAgent tool '%s' failed: %s",
                tool_name, e, exc_info=True,
            )
            return {"success": False, "error": str(e)}

    # ── System Prompt ──

    def _build_system_prompt(self) -> str:
        """从 守护Agent.md 构建 System Prompt（含 datetime 替换 + L1 领域知识）。"""
        template = self._load_prompt_template()
        from ..infrastructure.database.models import BEIJING_TZ
        beijing_time = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        result = template.replace("{CURRENT_DATETIME}", beijing_time)

        # M10 L1: 注入地产领域核心认知
        domain_knowledge = ""
        try:
            dk_override = getattr(self.config, "domain_knowledge_path", "") if self.config else ""
            if dk_override:
                dk_path = Path(dk_override)
            else:
                dk_path = Path(__file__).parent.parent / "prompts" / "domain_knowledge.md"
            if dk_path.exists():
                domain_knowledge = dk_path.read_text(encoding="utf-8")
            else:
                domain_knowledge = "（领域知识文件未生成）"
        except Exception as e:
            logger.warning("GuardianAgent failed to load L1 domain knowledge: %s", e)
            domain_knowledge = "（领域知识加载失败）"

        return result.replace("{DOMAIN_KNOWLEDGE}", domain_knowledge)

    def _load_prompt_template(self) -> str:
        """从 prompts/守护Agent.md 加载 prompt（实例级缓存）。"""
        if self._prompt_loaded and self._system_prompt_template:
            return self._system_prompt_template

        if self.prompt_path_override:
            prompt_path = Path(self.prompt_path_override)
        else:
            prompt_path = Path(__file__).parent.parent / "prompts" / "守护Agent.md"
        try:
            self._system_prompt_template = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("GuardianAgent prompt not found: %s", prompt_path)
            self._system_prompt_template = (
                "你是 Emy 的守护调查员。"
                "请使用 query_data 工具查询数据，用 write_notebook 记录发现，"
                "然后给出综合分析报告。"
            )

        self._prompt_loaded = True
        return self._system_prompt_template

    # ── 工具方法 ──

    @staticmethod
    def _now() -> str:
        """返回当前 UTC ISO 时间戳字符串。"""
        return datetime.now(timezone.utc).isoformat()
