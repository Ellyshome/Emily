# emily-core/emily_core/project/agent_shell/shell.py
"""EmilyShell —— ProjectAgent 对话终端。

基于 Python cmd.Cmd 的交互式对话 REPL：
  - 用户输入 → LLM（DeepSeek）→ 回复
  - LLM 可通过 function calling 调用运维工具
  - 单次 REPL 内保持对话上下文记忆
"""

from __future__ import annotations

import asyncio
import cmd
import json
import logging
import sys
from datetime import datetime
from typing import TYPE_CHECKING

from emily_core.project.agent_shell.formatter import ShellFormatter

if TYPE_CHECKING:
    from emily_core.infrastructure.llm.client import LLMClient

logger = logging.getLogger("emily.agent_shell")


SYSTEM_PROMPT = """你是 Emily ProjectAgent —— 企业项目管理 AI 助手。

## 你的能力
你可以通过工具（functions）查询项目数据：
- query_project_status：查询项目整体状态
- list_stale_nodes：列出卡滞/阻塞的节点
- list_milestone_alerts：列出即将到期的里程碑
- list_recent_findings：查看最近发现的问题
- generate_weekly_report：生成项目周报
- show_system_info：查看系统运行信息

## 行为准则
1. 用户问项目相关问题时，主动调用工具获取实时数据，不要编造
2. 回复简洁、有条理，用中文
3. 如果工具返回"暂无数据"，如实告知用户
4. 如果用户的问题超出你的能力范围，诚实说明
5. 不需要每次都自我介绍，直接回答问题
6. 工具调用是透明的，不要在回复中提到「我调用了XX工具」
"""

MAX_TOOL_ROUNDS = 5  # 最多连续工具调用轮数，防止死循环


class ProjectAgentShell(cmd.Cmd):
    """ProjectAgent 对话终端。

    基于 Python cmd.Cmd，支持：
      - 命令历史（上下箭头）
      - 双模式：交互 REPL + 单命令（-c）
      - 对话上下文记忆（本次 REPL 内）
    """

    intro = r"""
███████╗███╗   ███╗██╗██╗  ██╗   ███████╗██╗  ██╗███████╗██╗     ██╗
██╔════╝████╗ ████║██║╚██╗██╔╝   ██╔════╝██║  ██║██╔════╝██║     ██║
█████╗  ██╔████╔██║██║ ╚███╔╝    ███████╗███████║█████╗  ██║     ██║
██╔══╝  ██║╚██╔╝██║██║ ██╔██╗    ╚════██║██╔══██║██╔══╝  ██║     ██║
███████╗██║ ╚═╝ ██║██║██╔╝ ██╗   ███████║██║  ██║███████╗███████╗███████╗
╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝

>>> Emily ProjectAgent Shell <<<
直接与 Emily 后台大脑对话。

实例 ID: {instance_id}
启动时间: {startup_time}

输入 !help 查看命令，输入 exit 退出。
"""

    prompt = "\n[agent] > "

    def __init__(
        self,
        llm_client: "LLMClient",
        tool_executor,
        tool_definitions: list[dict],
        instance_id: str = "",
    ):
        super().__init__()
        self._llm = llm_client
        self._tool_executor = tool_executor
        self._tool_definitions = tool_definitions
        self._instance_id = instance_id
        self._fmt = ShellFormatter()

        # 对话历史（本次 REPL 内记忆）
        self._messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        self.intro = self.intro.format(
            instance_id=instance_id,
            startup_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # ── 内置命令 ──

    def default(self, line: str) -> bool | None:
        """主入口：用户输入 → LLM 对话。"""
        line = line.strip()

        # 退出
        if line.lower() in ("exit", "quit", "q"):
            print("👋 再见！")
            return True

        # 帮助
        if line.lower() in ("!help", "!h"):
            self._print_help()
            return None
        if line.lower().startswith("!help "):
            self.do_help(line[6:].strip())
            return None

        # 清空记忆
        if line.lower() in ("!clear", "!reset"):
            self._messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("🧹 对话记忆已清空。")
            return None

        # 查看历史
        if line.lower() in ("!history", "!hist"):
            self._print_history()
            return None

        if not line:
            return None

        # ── 正常对话流程 ──
        try:
            asyncio.run(self._chat(line))
        except Exception as e:
            print(f"\n❌ 对话出错：{e}")
            logger.exception("Chat failed")

        return None

    async def _chat(self, user_input: str) -> None:
        """核心对话循环：用户输入 → LLM →（可选 tool call）→ 回复。"""
        # 添加用户消息到历史
        self._messages.append({"role": "user", "content": user_input})

        for round_idx in range(MAX_TOOL_ROUNDS):
            print("⏳", end="", flush=True)  # 思考中指示

            response = await self._llm.chat_with_tools(
                messages=self._messages,
                tools=self._tool_definitions,
            )

            print("\r", end="")  # 清除思考指示

            if response["type"] == "text":
                # LLM 直接回复文本
                content = response["content"]
                self._messages.append({"role": "assistant", "content": content})
                print(f"\n{content}")
                return

            elif response["type"] == "tool_call":
                tool_name = response["tool_name"]
                tool_args = response["tool_arguments"]
                tool_call_id = response.get("tool_call_id", "")

                print(f"\n🔧 调用工具：{tool_name}...", end="", flush=True)

                # 执行工具
                tool_result = await self._tool_executor.execute(tool_name, tool_args)

                print("\r", end="")

                # 将 LLM 的 tool_call 消息和工具结果加入历史
                # assistant 消息需要包含 tool_calls 和 reasoning_content
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False),
                            },
                        }
                    ],
                }
                reasoning = response.get("reasoning_content", "")
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning

                self._messages.append(assistant_msg)
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": tool_result,
                })

            else:
                print(f"\n⚠️ 未知响应类型：{response.get('type')}")
                return

        # 超过最大轮数
        print("\n⚠️ 已达到最大工具调用轮数，请简化你的问题。")

    # ── 帮助系统 ──

    def _print_help(self) -> None:
        """打印帮助信息。"""
        help_text = """
📖 EmilyShell 使用说明

这是一个与 Emily ProjectAgent 直接对话的终端。
你输入自然语言，AI 理解你的意图并回复。
AI 可以自动调用工具查询项目数据。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💬 对话示例：

  锦绣花园项目进度怎么样？
  列出所有卡滞节点
  最近有什么告警？
  生成一份项目周报
  现在系统运行状态如何？

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 内置命令（以 ! 开头）：

  !help / !h      显示此帮助
  !clear / !reset 清空对话记忆
  !history / !hist 查看对话历史
  exit / quit / q 退出

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 提示：
  • 对话记忆在本次会话内保持，退出后清空
  • AI 会自动调用工具获取数据，无需手动指定
  • 输入 Ctrl+C 中断当前回复
"""
        print(help_text)

    def _print_history(self) -> None:
        """打印对话历史摘要。"""
        if len(self._messages) <= 1:  # 只有 system prompt
            print("📝 暂无对话历史。")
            return

        print("\n📝 对话历史：")
        for i, msg in enumerate(self._messages):
            role = msg["role"]
            if role == "system":
                continue
            content = msg.get("content", "")
            if content is None:
                # tool_calls 消息
                tc = msg.get("tool_calls", [])
                if tc:
                    content = f"[调用工具: {tc[0]['function']['name']}]"

            # 截断长内容
            display = content[:120] + "..." if len(str(content)) > 120 else content
            icon = "👤" if role == "user" else ("🔧" if role == "tool" else "🤖")
            print(f"  {icon} [{role}] {display}")

    def do_help(self, arg: str) -> None:
        """help 命令（兼容 cmd.Cmd 内置）。"""
        arg = arg.strip().lower()
        if arg in ("", "all"):
            self._print_help()
        else:
            print(f"\n  输入 !help 查看使用说明。")
