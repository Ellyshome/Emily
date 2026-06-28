# emily-core/emily_core/project/agent_shell/__main__.py
"""EmilyShell 入口模块。

用法：
    # 交互 REPL（需要 -it）
    docker exec -it emily-core python -m emily_core.project.agent_shell

    # 单命令模式
    docker exec emily-core python -m emily_core.project.agent_shell -c "锦绣花园进度怎么样？"

    # Cron 定时调用
    docker exec emily-core python -m emily_core.project.agent_shell -c "生成周报"
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys


def main() -> None:
    """EmilyShell 入口函数。

    1. 解析命令行参数
    2. 自举 Config / DB / LLMClient / Tools
    3. 创建 ProjectAgentShell 并启动
    """
    parser = argparse.ArgumentParser(
        description="Emily ProjectAgent Shell — 对话式项目运维终端"
    )
    parser.add_argument(
        "--command", "-c",
        type=str,
        default=None,
        help="直接执行命令（非交互模式），执行完即退出",
    )
    args = parser.parse_args()

    # ── 配置日志 ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # ── 加载配置（从环境变量） ──
    from emily_core.config import Config

    raw_config: dict = {}
    env_map = {
        "EMILY_DATABASE_URL": "database_url",
        "EMILY_LLM_API_KEY": "llm_api_key",
        "EMILY_LLM_BASE_URL": "llm_base_url",
        "EMILY_LLM_MODEL": "llm_model",
    }
    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            raw_config[config_key] = val

    config = Config.from_dict(raw_config if raw_config else None)

    # ── 检查 API Key ──
    if not config.llm_api_key:
        print("❌ 未设置 LLM API Key。请设置环境变量 EMILY_LLM_API_KEY。")
        print("   示例：export EMILY_LLM_API_KEY=sk-xxxxx")
        sys.exit(1)

    # ── 初始化 DB ──
    from emily_core.infrastructure.database.session import init_db

    if config.database_url:
        init_db(db_url=config.database_url)
    else:
        init_db()  # 使用 Docker Compose 默认值

    # ── 创建 LLM 客户端 ──
    from emily_core.infrastructure.llm.client import LLMClient

    llm_client = LLMClient(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        model=config.llm_model,
        temperature=config.llm_temperature,
        max_tokens=config.llm_max_tokens,
    )

    # ── 生成实例 ID ──
    instance_id = f"emily-core-{socket.gethostname()[-8:]}"

    # ── 创建工具 ──
    from emily_core.repositories.sm_node_repo import SMNodeRepository
    from emily_core.project.ops.repositories.ops_repo import OpsRepository
    from emily_core.project.agent_shell.tools import ToolExecutor, get_tool_definitions

    node_repo = SMNodeRepository()
    ops_repo = OpsRepository()
    tool_executor = ToolExecutor(node_repo, ops_repo, config, instance_id=instance_id)
    tool_definitions = get_tool_definitions()

    # ── 创建 Shell 并启动 ──
    from emily_core.project.agent_shell.shell import ProjectAgentShell

    shell = ProjectAgentShell(
        llm_client=llm_client,
        tool_executor=tool_executor,
        tool_definitions=tool_definitions,
        instance_id=instance_id,
    )

    if args.command:
        # 单命令模式：直接执行 → 退出
        shell.default(args.command)
    else:
        # 交互模式：进入 REPL
        try:
            shell.cmdloop()
        except KeyboardInterrupt:
            print("\n\n👋 EmilyShell: 再见！")


if __name__ == "__main__":
    main()
