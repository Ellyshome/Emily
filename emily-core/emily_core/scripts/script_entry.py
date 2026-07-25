"""ScriptEntry — 脚本元信息 dataclass，镜像 BusinessFlowTool 形状。

ScriptManager 聚合的是开发者/维护脚本（subprocess CLI），而非 LLM 运行时工具（BusinessFlowTool.handler）。
"""

from dataclasses import dataclass, field


@dataclass
class ScriptEntry:
    """开发者/维护脚本的元信息条目。

    与 BusinessFlowTool 的区别：
      - BusinessFlowTool: LLM 运行时工具，进程内 async handler
      - ScriptEntry: 开发者脚本，subprocess CLI，消费者是开发者而非 LLM
    """
    name: str                           # 与清单 key 一致
    description: str                    # 脚本功能描述
    category: str                       # business_tool / system_maintenance / evolution_pipeline / aggregation_shell / one_shot
    source_path: str                    # 相对仓库根，如 "scripts/maintain_node_template_index.py"
    invocation: str                     # "uv run python scripts/{name}.py {args}"
    check_arg: str | None = None        # "--check" / "--dry-run" / "--preview" / "--probe" / None
    run_args: list = field(default_factory=list)   # 默认运行参数
    auto_run: str | None = None         # "bootstrap" / "scheduler:<name>" / None
    auto_run_args: list = field(default_factory=list)  # 自动触发时参数
    writes_db: bool = False
    aggregation_parent: str | None = None  # 归属聚合壳
    status: str = "active"              # active / deprecated / one_shot
    entrypoint: str | None = None       # 可选 "module:function" in-process 入口
    timeout_seconds: int = 60
    flow_note: str | None = None        # 每日流程说明（供 doc 生成）
    scheduling_note: str | None = None  # 调度归属注（供 doc 生成）

    @property
    def has_check(self) -> bool:
        """是否有自检能力。"""
        return self.check_arg is not None
