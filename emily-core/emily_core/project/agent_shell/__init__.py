# emily-core/emily_core/project/agent_shell
"""EmilyShell —— ProjectAgent 对话终端。

基于 Python cmd.Cmd 的交互式对话 REPL。
用户输入自然语言，LLM 理解意图并通过 function calling 调用运维工具。

用法：
    docker exec -it emily-core python -m emily_core.project.agent_shell
"""

from emily_core.project.agent_shell.shell import ProjectAgentShell

__all__ = ["ProjectAgentShell"]
