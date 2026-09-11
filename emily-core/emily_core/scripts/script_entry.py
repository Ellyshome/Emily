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
    params: list = field(default_factory=list)  # 参数 schema（见 ScriptParam），空=仅裸 args
    subcommands: list = field(default_factory=list)  # 子命令分支（见 ScriptSubcommand）

    @property
    def has_check(self) -> bool:
        """是否有自检能力。"""
        return self.check_arg is not None

    @property
    def has_params(self) -> bool:
        """是否可在 Web 端渲染表单（有参数或有子命令任一即可）。"""
        return bool(self.params) or bool(self.subcommands)


@dataclass
class ScriptSubcommand:
    """带子命令脚本的动作分支（如 manage_nodes 的 create / update / query）。

    子命令名作为位置参数拼在 argv 首位，各分支的参数集互相独立 ——
    扁平的 params 表达不了"不同子命令不同参数"，故单列一层。
    """
    name: str                           # 子命令名，如 "create"
    label: str = ""                     # 表单显示名，空则回退 name
    help: str = ""                      # 表单提示
    params: list = field(default_factory=list)   # 该子命令的参数 schema


@dataclass
class ScriptParam:
    """单个脚本参数的 schema —— 供 Web 表单渲染 + CLI 参数拼装。

    对应 CLAUDE.md §6 约束 11（工具必须带参数 schema）在脚本侧的等价物：
    没有 schema，调用方（Web 表单 / LLM）就不知道参数类型与取值约束。

    type 与前端控件的映射：
      str    → 文本框            flag → 复选框
      int    → 数字框            enum → 单选下拉（choices）
      multi  → 多选框组（choices，重复传参或逗号拼接）
    """
    name: str                           # 参数名，如 "top-k"（不含 --）
    type: str = "str"                   # str / int / flag / enum / multi
    label: str = ""                     # 表单显示名，空则回退 name
    help: str = ""                      # 表单提示文案
    required: bool = False
    default: object = None
    choices: list = field(default_factory=list)   # enum / multi 的候选值
    positional: bool = False            # True=位置参数（不带 --）
    group: str | None = None            # 互斥组名：同组内只能选一个
    min: int | None = None              # int 下界
    max: int | None = None              # int 上界
    options_source: str | None = None   # 动态候选源：users / projects / nodes
                                        # 非空时 Web 端从真实环境取候选值渲染下拉，
                                        # 避免手抄 UUID（choices 仅用于静态枚举）

    @property
    def flag(self) -> str:
        """CLI 长选项形式。"""
        return f"--{self.name}"
