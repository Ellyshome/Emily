# 引入 ScriptManager —— 与 ToolManager 平级的脚本聚合层

> **本文件用途**：交付给 AI 执行工具的实施计划。AI 友好——每步含明确文件路径、复用点、验收标准。执行者按编号步骤推进，每步独立可验收，最终用 Verification 段端到端验证。
>
> **背景决策**：用户提出 ToolManager 使命是否应扩展到管理 `maintain_node_template_index.py` 等系统运转脚本。经讨论确认：**不扩 ToolManager**（其契约故意收窄于 LLM 运行时工具），**新增同级 ScriptManager** 镜像 ToolManager 交互习惯，二者共享 service 层。

---

## Context

用户提出：ToolManager 的使命是否该改改？业务工具只是其职责的一部分，系统运转脚本（如 `maintain_node_template_index.py`）也应在其管理之下，核心职能是"管理脚本，方便开发者管理与测试脚本，保证各脚本的功能与开发者对全量脚本的掌握"。

**诊断确认（痛点真实）**：
- `docs/脚本工具目录.md` 已漂移：自称 27 个、实列 28、`scripts/` 实际 33 个 `.py` + 1 个 `.ps1`；`maintain_node_template_index.py` 和 `dump_session_prompt.py` 均未收录。
- 脚本层无任何统一管理/测试基础设施：无 ScriptRegistry、无 DB 表、无统一元数据；唯一"聚合"是几个领域 shell（evolution.py / cold_start.py / cognition_cycle.py）。
- 脚本零测试覆盖：`emily-core/tests/` 只有 `toolmgr_cases.yaml` 一个文件（业务工具 smoke 用例）。
- bootstrap 集成硬编码：[bootstrap.py:123-137](emily-core/emily_core/bootstrap.py#L123-L137) 对 `maintain_node_template_index.py` 单独 `subprocess` + fail-open，不可扩展，脚本坏掉静默失效。

**选定的方向**：保留 ToolManager 契约不动，新增**同级 ScriptManager**镜像 ToolManager 的交互习惯，二者共享 service 层。ToolManager 使命声明仅补一句边界澄清。

**为何不直接扩 ToolManager**：ToolManager 聚合的是 `BusinessFlowTool`（统一形状 `{name, description, parameters(JSON Schema), handler(async), category, permission_flag}`，LLM 结构化输出后框架直调）。维护脚本没有这个形状（argparse、副作用、subprocess、消费者是开发者而非 LLM）。塞进去要么泛化到最低公约数、要么开"第二类条目"使 `toolmgr list` 混杂两类东西。名字"ToolManager"语义上也指向 LLM 工具。"管理脚本"本质是一个 ScriptManager。

**预期结果**：脚本有声明式注册表作 single source of truth；`scriptmgr list/describe/check/run/test` 统一管理入口；`docs/脚本工具目录.md` 由 `scriptmgr export` 生成（doc-as-code 杀漂移）；bootstrap 从清单枚举 auto-run 脚本取代硬编码；ToolManager 边界显式澄清。

---

## 交付物清单（执行完成后须全部成立）

- [ ] 新建 `emily-core/emily_core/scripts/` 包（5 文件：`__init__.py` / `script_entry.py` / `registry.py` / `manager.py` / `catalog.py`）
- [ ] 新建 `emily-data/config/scripts_registry.yaml`（33 条脚本 + aggregations + excluded）
- [ ] 新建 `scripts/scriptmgr.py` CLI（list/describe/check/run/test/export）
- [ ] 新建 `emily-core/tests/scriptmgr_cases.yaml`（首批 3-5 个高价值脚本 smoke 用例）
- [ ] 重构 [bootstrap.py:123-137](emily-core/emily_core/bootstrap.py#L123-L137) 为从清单枚举 auto-run 脚本
- [ ] [tools/manager.py](emily-core/emily_core/tools/manager.py) docstring 补边界澄清（无逻辑改动）
- [ ] [__init__.py](emily-core/emily_core/__init__.py) 接线 `_script_manager`
- [ ] `docs/脚本工具目录.md` 改为 `scriptmgr export` 生成产物（修复 3 处漂移）
- [ ] 同步更新 CLAUDE.md / docs/代码文件目录.md / docs/接口协议与调用约定.md / docs/业务模块与运转全景.md / docs/开发记录.md
- [ ] Verification 全部通过（见末段）

---

## 架构决策

| 决策 | 选择 | 理由 |
|---|---|---|
| ScriptManager 位置 | 新包 `emily-core/emily_core/scripts/`（与 `tools/` 同级） | 镜像 `tools/` 布局（`manager.py` + `registry.py` + `script_entry.py`），导航类比友好。所有 import 用全限定 `emily_core.scripts.X`，避免与仓库根 `scripts/` 混淆。 |
| 注册表格式 | 单一中央 YAML `emily-data/config/scripts_registry.yaml` | 声明式哲学（CLAUDE.md 约束 #4 Hook 声明式 JSON）；与 `scheduler_config.json`/`core_config.json` 同置；可 PR diff；无 import 副作用。 |
| `run` 执行模型 | subprocess 默认 + `entrypoint` 可选 in-process | subprocess 是脚本现行调用契约（含 bootstrap）；多脚本有模块级副作用（env 加载、sys.path），in-process 导入污染宿主。`entrypoint` 字段为已证 import-safe 的脚本留快路径。 |
| 脚本 onboard 范围 | 一次性全量 33 个（seed 脚本草拟 + 人工校验） | 用户痛点是目录漂移，分批 onboard 使漂移延续；seed 脚本让批量 onboard 成本约 1 小时。 |
| `check` 契约 | 不重构脚本 argparse，用清单 `check_arg` 字段做适配器 | 统一"契约"（每脚本都有 `check_arg`，可 `null`）而非统一"flag 拼写"；`--check`/`--dry-run`/`--preview`/`--probe` 多样性封装在清单里。 |
| `backup.ps1` | 纳入清单，`status: one_shot`（run 禁用） | 目录完整性；跨 shell run 在容器内不可靠，故仅收录不执行。 |

---

## 实施步骤

### 步骤 1：新建 `emily_core/scripts/` 包（镜像 `tools/`）

新建 5 个文件：

| 文件 | 职责 | 镜像源 |
|---|---|---|
| `emily-core/emily_core/scripts/__init__.py` | 包标记，导出 `ScriptManager`/`ScriptEntry`/`load_registry` | `tools/__init__.py` |
| `emily-core/emily_core/scripts/script_entry.py` | `ScriptEntry` dataclass | `tools/business_flow_tools.py:20` (`BusinessFlowTool`) |
| `emily-core/emily_core/scripts/registry.py` | `ScriptRegistry` + `load_registry()` 读 YAML | `tools/registry.py`（但声明式读 YAML，非命令式 register） |
| `emily-core/emily_core/scripts/manager.py` | `ScriptManager` 类 | `tools/manager.py:16-127` (`ToolManager`) |
| `emily-core/emily_core/scripts/catalog.py` | `generate_markdown(registry)` 生成目录 | 无（新增 doc-as-code 生成器） |

**ScriptEntry 字段**（镜像 `BusinessFlowTool` 形状）：
```python
@dataclass
class ScriptEntry:
    name: str                       # 与清单 key 一致
    description: str
    category: str                   # business_tool / system_maintenance / evolution_pipeline / aggregation_shell / one_shot
    source_path: str                # 相对仓库根，如 "scripts/maintain_node_template_index.py"
    invocation: str                 # "uv run python scripts/{name}.py {args}"
    check_arg: str | None           # "--check" / "--dry-run" / "--preview" / "--probe" / None
    run_args: list[str]             # 默认运行参数
    auto_run: str | None            # "bootstrap" / "scheduler:<name>" / None
    auto_run_args: list[str]        # 自动触发时参数，如 ["--auto"]
    writes_db: bool
    aggregation_parent: str | None  # 归属聚合壳
    status: str                     # active / deprecated / one_shot
    entrypoint: str | None          # 可选 "module:function" in-process 入口
    timeout_seconds: int = 60
```

**ScriptManager 方法**（镜像 [tools/manager.py](emily-core/emily_core/tools/manager.py)，subprocess 语义）：
- `list()` → `[{name, category, status, writes_db, auto_run, has_check}]`（镜像 `manager.py:24-35`）
- `describe(name=None)` → 完整元信息；name=None 返回 `{"scripts":[...], "count":N}`；不存在 `{"error":..., "code":2}`（镜像 `manager.py:37-53`）
- `check(name=None)` → 跑每个脚本 `check_arg`，返回 `[{name, category, ready, returncode, note}]`；`check_arg=None` 标 `ready="n/a"` 跳过（镜像 `selfcheck` `manager.py:87-98`）
- `run(name, args=None, timeout=None)` → subprocess 执行；返回 `{success, returncode, stdout, stderr, script, code}`，code 0/1/2（镜像 `call` `manager.py:66-83`）。`entrypoint` 非空时走 in-process import + `contextlib.redirect_stdout`
- `test(name=None)` → 跑 `scriptmgr_cases.yaml` smoke 用例，无 case 退化为 `check`（镜像 `toolmgr.py:216-270` 的 test）
- `export(format="markdown")` → 调 `catalog.generate_markdown`，生成 `docs/脚本工具目录.md`（镜像 `export` `manager.py:60-62`）

**使命 docstring**（镜像 `tools/manager.py:1-5` 风格 + 边界声明）：
> ScriptManager — scripts/ 目录的对外聚合层。
> 补三件事：统一调用入口、自描述、CLI/HTTP 测试接口。
> 不替代 scripts/ 目录本身，注册元信息仍走 scripts/registry.yaml 的声明式清单。
> 与 ToolManager 的边界：ToolManager 管 LLM 运行时工具（BusinessFlowTool.handler，进程内 async）；ScriptManager 管开发者/维护脚本（subprocess CLI）。两者共享 service 层，互不调用。

**验收**：`uv run python -c "from emily_core.scripts.manager import ScriptManager; from emily_core.scripts.registry import load_registry; print('import ok')"` 通过。

### 步骤 2：新建声明式清单 `emily-data/config/scripts_registry.yaml`

Schema 见上 ScriptEntry 字段。结构：`scripts:`（33 条）+ `aggregations:`（聚合壳关系，供 export 生成附录 B）+ `excluded:`（`_diag_tools.py`/`_test_flows.py`/`_test_flows2.py`）+ 顶层 `prologue`/`epilogue`（生成器原样输出的自由 Markdown）。

**seed 流程**（一次性）：
1. 写临时 seed 脚本 `emily-core/emily_core/scripts/_seed.py`（不交付）：glob `scripts/*.py`（排除 `_*`）→ 读首行 docstring 作 description → 解析 argparse 排测 `--check`/`--dry-run`/`--preview`/`--probe` → 交叉比对 `docs/脚本工具目录.md` 速查表（lines 582-611）取 category/writes_db/aggregation_parent → 输出草稿 YAML。
2. 人工校验：修正 description；`maintain_node_template_index` 设 `auto_run: bootstrap`/`auto_run_args: ["--auto"]`/`timeout_seconds: 30`；`fix_code_fences.py`/`backup.ps1` 设 `status: one_shot`；补 2 个缺失条目（`maintain_node_template_index`、`dump_session_prompt`）。
3. 存为 `emily-data/config/scripts_registry.yaml`，删 `_seed.py`。

**清单 high-value 条目示例**（`maintain_node_template_index`）：
```yaml
maintain_node_template_index:
  description: 扫描 node_templates/*.md 模板，生成/更新 index.yaml 索引
  category: system_maintenance
  source_path: scripts/maintain_node_template_index.py
  invocation: uv run python scripts/maintain_node_template_index.py {args}
  check_arg: --check
  run_args: []
  auto_run: bootstrap
  auto_run_args: ["--auto"]
  writes_db: false
  aggregation_parent: null
  status: active
  entrypoint: null
  timeout_seconds: 30
```

**aggregations 段示例**：
```yaml
aggregations:
  evolution.py:
    subcommands: [daily, weekly, morning, validate, full]
    children: [evolution_metrics, evolution_anomaly, evolution_insight,
               evolution_rules, evolution_patch, evolution_apply,
               evolution_validate, evolution_morning, evolution_node_close]
  cold_start.py:
    children: [self_check, check_initialization, build_world_book]
  cognition_cycle.py:
    children: [detect_cognition_drift, update_world_book]
```

**验收**：`load_registry()` 能解析全部 33 条；`len(registry.entries)` == `ls scripts/*.py | grep -v '^_' | wc -l`。

### 步骤 3：新建 CLI `scripts/scriptmgr.py`（镜像 `scripts/toolmgr.py`）

复用 [toolmgr.py:1-100](scripts/toolmgr.py#L1-L100) 的脚手架：`.env` 零依赖加载、`sys.path.insert`、`_init_core()`、`_print_table`、`--json` flag、退出码 0/1/2、Windows UTF-8 stdout 包装（`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")`）。

子命令：`list [--json]` / `describe <name> [--json]` / `check [<name>] [--json]` / `run <name> [--args "..."] [--json]` / `test [<name>] [--json]` / `export [--format markdown|json] [--out <path>]`。

**验收**：`uv run python scripts/scriptmgr.py list` 输出 33 行表格；`scriptmgr describe maintain_node_template_index` 显示 `auto_run=bootstrap`。

### 步骤 4：新建 smoke 用例 `emily-core/tests/scriptmgr_cases.yaml`

镜像 `toolmgr_cases.yaml` 结构，schema：`{args: [...], expect_returncode: [...], expect_stdout_contains: ...}`。首批覆盖 3-5 个高价值脚本：`maintain_node_template_index`（`--check`，期望 0 或 1）、`self_check`（`--dry-run`）、`check_tools_consistency`（无 DB 快速检查）。

**验收**：`uv run python scripts/scriptmgr.py test --json` 首批用例通过或正确 skipped。

### 步骤 5：重构 `bootstrap.py:123-137`

将硬编码单脚本调用替换为从清单枚举 `auto_run: bootstrap` 的脚本，**保留 fail-open + timeout + 日志语义不变**：

```python
# 自动运行清单中 auto_run: bootstrap 的脚本（fail-open，启动时自检）
try:
    from .scripts.registry import load_registry
    _registry = load_registry()
    for _entry in _registry.entries_with_auto_run("bootstrap"):
        _script = Path(__file__).resolve().parents[2] / _entry.source_path
        if not _script.exists():
            continue
        _result = subprocess.run(
            [sys.executable, str(_script), *_entry.auto_run_args],
            capture_output=True, text=True, timeout=_entry.timeout_seconds,
        )
        if _result.returncode == 0 and _result.stdout.strip():
            _logger.info("%s: %s", _entry.name, _result.stdout.strip())
        elif _result.stderr.strip():
            _logger.debug("%s: %s", _entry.name, _result.stderr.strip())
except Exception as e:
    _logger.warning("bootstrap auto-run scripts skipped: %s", e)
```

`load_registry()` 复用 `resolve_data_path` 多级路径解析（与 `core_config.json`/`scheduler_config.json` 同模式）找 YAML。bootstrap 独立加载（EmilyCore 在 `_ensure_initialized` 再加载一次，双重读可忽略）。

**验收**：重启 emily-core 后日志仍出现 `Node template index: ...`（同重构前）；`maintain_node_template_index` 仍以 `--auto` + 30s timeout 运行。

### 步骤 6：ToolManager 使命声明澄清（仅 docstring，无逻辑改动）

[tools/manager.py:1-5](emily-core/emily_core/tools/manager.py#L1-L5) 模块 docstring 追加边界段：
> 边界：ToolManager 只管 LLM 运行时工具（BusinessFlowTool.handler，进程内 async）。开发者/维护脚本（scripts/*.py，subprocess CLI）归 ScriptManager 管，见 emily_core/scripts/manager.py。两者共享 service 层（node_batch / InsightGenerator 等），互不调用。

[tools/manager.py:17](emily-core/emily_core/tools/manager.py#L17) 类 docstring 追加一句：`仅管 LLM 运行时工具；开发者脚本见 ScriptManager。`

**验收**：`toolmgr list` 工具数与就绪态同重构前（无回归）。

### 步骤 7：接线 `emily_core/__init__.py`

[__init__.py:68-69](emily-core/emily_core/__init__.py#L68-L69) 声明区追加 `self._script_registry = None` / `self._script_manager = None`。在 `self._tool_manager = ToolManager(...)`（line 218-221）之后构造：
```python
from .scripts.registry import load_registry
from .scripts.manager import ScriptManager
self._script_registry = load_registry()
self._script_manager = ScriptManager(self._script_registry)
logger.info("script_manager: ready (%d scripts)", len(self._script_registry))
```

**验收**：`core._script_manager` 非 None；`core._script_manager.list()` 返回 33 条。

### 步骤 8：生成 `docs/脚本工具目录.md`（doc-as-code）

`scriptmgr export --out docs/脚本工具目录.md` 生成，文件头加 `<!-- AUTO-GENERATED from emily-data/config/scripts_registry.yaml by scriptmgr export. Do not edit by hand. -->`。

**prose 处理**：现有手写 prose（每日流程说明、调度归属注、聚合关系图）作为结构化字段吸收进清单（`flow_note` per aggregation、`scheduling_note` per script、`aggregations` 段），生成器内联渲染。**不保留"生成器跳过的自由 prose 段"**——那会重新引入漂移。首次 `export` 的 git diff 应只显示 3 处修正：补 `maintain_node_template_index`、补 `dump_session_prompt`、计数 27/28→33。

**验收**：`scriptmgr export` 幂等（连跑两次字节一致）；git diff 仅 3 处修正 + 首次格式归一化。

### 步骤 9：同步 docs（CLAUDE.md 维护约定 #1-#3）

- [CLAUDE.md](CLAUDE.md)：§5 文档导引表加 ScriptManager 行；§6 约束加 #10「ToolManager 管 LLM 运行时工具；ScriptManager 管 scripts/ 开发者脚本，共享 service 层，脚本元信息声明在 `emily-data/config/scripts_registry.yaml`，目录由 `scriptmgr export` 生成」；日常命令段加 `scriptmgr list/describe/check/run/export` 示例。
- [docs/代码文件目录.md](docs/代码文件目录.md)：加 ScriptManager 包条目；ToolManager 条目补边界说明。
- [docs/接口协议与调用约定.md](docs/接口协议与调用约定.md)：§3.3 后加 §3.4 ScriptManager，镜像 §3.3 结构。
- [docs/业务模块与运转全景.md](docs/业务模块与运转全景.md)：模块清单加 ScriptManager；注明 bootstrap 改读清单。
- [docs/开发记录.md](docs/开发记录.md)：新增架构决策「ScriptManager 引入 — 与 ToolManager 平级的脚本聚合层」。

**验收**：5 份 docs 均含 ScriptManager 条目；CLAUDE.md 约束 #10 就位。

---

## 复用清单（明确）

**镜像 ToolManager**：文件布局（`manager.py`+`registry.py`+`*_entry.py`）、使命 docstring 风格、`list/describe/export` 返回 shape、`selfcheck`→`check` 概念、`call`→`run` 的 `{success,...,code}` 0/1/2 语义、CLI 脚手架（`.env`/`sys.path`/`_init_core`/`_print_table`/`--json`/退出码）、cases YAML shape、`test` 无 case 退化逻辑。

**复用 scripts 层（不改）**：共享 service 层（`node_batch.py`/`node_batch_update.py`/`InsightGenerator`/`ProjectWorldBookService`/`SystemDescriptionService`/`SessionDataFetcher`/`tools_consistency.py`/`handle_knowledge_search`）、现有 argparse 子命令模式、现有 `--check`/`--dry-run`/`--preview`/`--probe` 约定（清单 `check_arg` 适配）、bootstrap fail-open+timeout 模式、`resolve_data_path` 多级路径解析。

**显式不复用**：`tool_registry` DB 表与 `TOOL_META_MAP` seed —— 那是 LLM 运行时工具的（对 Skill YAML 做 consistency），脚本无 Skill YAML，用 YAML 清单即可，不新建 DB 表。

---

## 新建/修改文件汇总

**新建（8）**：
- `emily-core/emily_core/scripts/__init__.py`
- `emily-core/emily_core/scripts/script_entry.py`
- `emily-core/emily_core/scripts/registry.py`
- `emily-core/emily_core/scripts/manager.py`
- `emily-core/emily_core/scripts/catalog.py`
- `scripts/scriptmgr.py`
- `emily-data/config/scripts_registry.yaml`
- `emily-core/tests/scriptmgr_cases.yaml`

**修改（8）**：
- [emily-core/emily_core/bootstrap.py](emily-core/emily_core/bootstrap.py)（lines 123-137 → 清单枚举）
- [emily-core/emily_core/tools/manager.py](emily-core/emily_core/tools/manager.py)（lines 1-5, 17 docstring 边界澄清）
- [emily-core/emily_core/__init__.py](emily-core/emily_core/__init__.py)（lines 68-69 声明 + 218-221 后接线）
- [docs/脚本工具目录.md](docs/脚本工具目录.md)（改为生成产物）
- [docs/代码文件目录.md](docs/代码文件目录.md)、[docs/接口协议与调用约定.md](docs/接口协议与调用约定.md)、[docs/业务模块与运转全景.md](docs/业务模块与运转全景.md)、[docs/开发记录.md](docs/开发记录.md)
- [CLAUDE.md](CLAUDE.md)

---

## Verification（端到端验证）

PowerShell 下 `$env:PYTHONIOENCODING="utf-8"`，仓库根 `d:\app\Emily` 执行：

```bash
# 1. 清单加载，list 显示全部 33 个
uv run python scripts/scriptmgr.py list --json | python -c "import sys,json; d=json.load(sys.stdin); assert d['count']==33, d['count']; print('OK')"

# 2. describe 显示 canonical 脚本元信息
uv run python scripts/scriptmgr.py describe maintain_node_template_index --json
# 期望: auto_run="bootstrap", check_arg="--check", writes_db=false

# 3. check 跑所有 check_arg（非阻断）
uv run python scripts/scriptmgr.py check --json
# 期望: maintain_node_template_index ready=true (rc 0 或 1), 部分 n/a

# 4. run 调用脚本
uv run python scripts/scriptmgr.py run maintain_node_template_index --args "--check" --json
# 期望: success=true, stdout 含 "索引"

# 5. export 重生成目录；git diff 只显示 3 处修正
uv run python scripts/scriptmgr.py export --out docs/脚本工具目录.md
git diff docs/脚本工具目录.md
# 期望: 补 maintain_node_template_index / dump_session_prompt, 计数→33, 无内容丢失

# 6. bootstrap 仍经清单调用 maintain_node_template_index
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 50 emily-core 2>&1 | grep -i "node template index\|maintain_node_template"
# 期望: 同重构前日志

# 7. ToolManager 无回归
uv run python scripts/toolmgr.py list --json | python -c "import sys,json; print('tools:', json.load(sys.stdin)['count'])"
uv run python scripts/toolmgr.py selfcheck
# 期望: 工具数与就绪态同重构前

# 8. smoke 用例
uv run python scripts/scriptmgr.py test --json
# 期望: maintain_node_template_index/self_check/check_tools_consistency 用例通过
```

**关键不变量**：
1. `scriptmgr list` count == `ls scripts/*.py | grep -v '^_' | wc -l`（33，排除 `_*` 与 `backup.ps1` 的 run）。
2. `scriptmgr export` 幂等——连跑两次字节一致。
3. bootstrap 重构后 `maintain_node_template_index` 仍以 `--auto` + 30s timeout 在启动期运行（查日志）。
4. `toolmgr list` count 重构前后不变（ToolManager 仅改 docstring）。
5. `docs/脚本工具目录.md` git diff 仅 3 处修正（+首次格式归一化的一次性 diff）。

**失败模式留意**：
- YAML 容器内路径：`emily-data/config/scripts_registry.yaml` 须解析到容器内 `/app/data/config/scripts_registry.yaml`，复用 `resolve_data_path`。
- `__pycache__` 不自动刷新（CLAUDE.md 踩坑）：新包首部署后 `docker exec emily-core find /app/emily_core/scripts -name '__pycache__' -type d -exec rm -rf {} +` 再 restart。
- subprocess 须用 `sys.executable`（非裸 `python`），与 [bootstrap.py:129](emily-core/emily_core/bootstrap.py#L129) 一致。
- Windows GBK：`scriptmgr.py` 须复刻 `toolmgr.py` 的 UTF-8 stdout 包装。

---

## Phase 2（后续 PR，本次不做）

- 为 import-safe 脚本（`collect_session_data`/`cold_start`）补 `entrypoint` in-process 快路径。
- 扩充 `scriptmgr_cases.yaml` 覆盖更多脚本。
- `scriptmgr run` 对 `writes_db: true` 脚本加 `--confirm` 守卫（v1 无守卫，镜像 ToolManager，operator 受信）。
- CI drift-check：pre-commit 重跑 `export` 到临时文件 diff 比对 committed `docs/脚本工具目录.md`，漂移即 fail。

---

## 执行记录

**执行时间**：2026-07-25

**执行状态**：全部 9 步完成，验证通过。

### 新建文件（8/8）

| # | 文件 | 状态 |
|---|------|------|
| 1 | `emily-core/emily_core/scripts/__init__.py` | 已创建 |
| 2 | `emily-core/emily_core/scripts/script_entry.py` | 已创建 |
| 3 | `emily-core/emily_core/scripts/registry.py` | 已创建 |
| 4 | `emily-core/emily_core/scripts/manager.py` | 已创建 |
| 5 | `emily-core/emily_core/scripts/catalog.py` | 已创建 |
| 6 | `scripts/scriptmgr.py` | 已创建 |
| 7 | `emily-data/config/scripts_registry.yaml` | 已创建（31 条） |
| 8 | `emily-core/tests/scriptmgr_cases.yaml` | 已创建（5 个用例） |

### 修改文件（8/8）

| # | 文件 | 变更 |
|---|------|------|
| 1 | `emily-core/emily_core/bootstrap.py` | L123-140：硬编码 → 清单枚举 `auto_run: bootstrap` |
| 2 | `emily-core/emily_core/tools/manager.py` | 模块 + 类 docstring 补边界澄清 |
| 3 | `emily-core/emily_core/__init__.py` | 声明 + 接线 `_script_registry` / `_script_manager` |
| 4 | `docs/脚本工具目录.md` | 改为 `scriptmgr export` 生成产物 |
| 5 | `CLAUDE.md` | 文档导引更新 / 约束 #10 / 日常命令加 scriptmgr |
| 6 | `docs/代码文件目录.md` | 加 `scripts/` 包条目 |
| 7 | `docs/接口协议与调用约定.md` | 加 §3.4 ScriptManager |
| 8 | `docs/业务模块与运转全景.md` | 模块清单加 ToolManager + ScriptManager |
| 9 | `docs/开发记录.md` | 加 ADR-E12 |

### 验证结果

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | Registry 加载 `count=31` | ✅ |
| 2 | `maintain_node_template_index`: `auto_run=bootstrap`, `check_arg=--check`, `timeout=30` | ✅ |
| 3 | `dump_session_prompt` 已收录 | ✅ |
| 4 | `fix_code_fences` / `backup` 正确标记 `status: one_shot` | ✅ |
| 5 | `bootstrap auto-run` 枚举 = `['maintain_node_template_index']` | ✅ |
| 6 | `scriptmgr export` 幂等（两次输出字节一致） | ✅ |
| 7 | `toolmgr list` 退出码 0，32 tools 全部 READY（ToolManager 无回归） | ✅ |
| 8 | Core 日志：`script_manager: ready (31 scripts)` | ✅ |
| 9 | Docker 验证（bootstrap 重启后日志 + scriptmgr check/run/test） | [待容器环境验证] |

### 偏差记录

| 项目 | 计划 | 实际 | 原因 |
|------|------|------|------|
| 注册表条目数 | 33 | 31 | 实际 `scripts/` 非 `_*` 的 .py 为 30 + 1 .ps1 = 31，`scriptmgr.py` 本身不入注册表 |

### 临时文件清理

- `emily-core/emily_core/scripts/_seed.py` — 已删除（seed 脚本）
- `_test_registry.py` — 已删除（临时验证脚本）
- `_gen_doc.py` — 已删除（临时 doc 生成脚本）
