# ScriptManager 使用说明

> ScriptManager 是 `scripts/` 目录下全部开发者/维护脚本的统一管理入口，与 ToolManager（管 LLM 运行时工具）平级。提供 CLI 命令行和 Python API 两层接口。

---

## 1. 概述

### 1.1 解决了什么问题

- **目录漂移**：原 `docs/脚本工具目录.md` 手写维护，声称 27 个脚本但实列 28、`scripts/` 实际 30+ 个 `.py`
- **无统一入口**：脚本零散执行，无注册表、无自检、无 smoke 测试
- **bootstrap 硬编码**：`maintain_node_template_index.py` 在 `bootstrap.py` 中单独硬编码 `subprocess`，不可扩展
- **ToolManager 边界模糊**：ToolManager 契约是 LLM 运行时工具（`async handler`），管 subprocess 脚本不匹配

### 1.2 架构定位

```
EmilyCore
├── tools/                 ← ToolManager（LLM 运行时工具，进程内 async handler）
├── scripts/               ← ScriptManager（开发者/维护脚本，subprocess CLI）
└── 共享 service 层（node_batch / InsightGenerator 等，两者互不调用）
```

### 1.3 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| 注册表 YAML | `emily-data/config/scripts_registry.yaml` | 声明式元信息，single source of truth |
| `ScriptEntry` | `emily_core/scripts/script_entry.py` | 单条脚本元信息 dataclass |
| `ScriptRegistry` | `emily_core/scripts/registry.py` | 从 YAML 加载，提供查询 |
| `ScriptManager` | `emily_core/scripts/manager.py` | 聚合层：list/describe/check/run/test/export |
| `catalog.py` | `emily_core/scripts/catalog.py` | doc-as-code 生成器，生成 `docs/脚本工具目录.md` |
| CLI | `scripts/scriptmgr.py` | 命令行薄壳，镜像 `toolmgr.py` |

---

## 2. CLI 命令参考

所有命令在仓库根目录 `d:\app\Emily` 下执行，前缀 `uv run python scripts/scriptmgr.py`。

### 2.1 list — 列出全部脚本

```bash
# 表格输出，按 category 分组
uv run python scripts/scriptmgr.py list

# JSON 输出（方便脚本消费）
uv run python scripts/scriptmgr.py list --json
```

输出示例：
```
[evolution_pipeline]
NAME               STATUS   CHECK   DB   DESCRIPTION
evolution          active   Y       N    进化闭环薄聚合脚本
evolution_metrics  active   Y       N    指标聚合（10 数据源）
...

total: 31 scripts
```

### 2.2 describe — 查看脚本详情

```bash
# 全部脚本摘要表格
uv run python scripts/scriptmgr.py describe

# 单个脚本完整元信息
uv run python scripts/scriptmgr.py describe maintain_node_template_index

# JSON 输出
uv run python scripts/scriptmgr.py describe maintain_node_template_index --json
```

### 2.3 check — 自检

跑每个脚本在注册表中声明的 `check_arg`（`--check` / `--dry-run` / `--preview` / `--probe`），不产生副作用：

```bash
# 全部脚本自检
uv run python scripts/scriptmgr.py check

# 单个脚本
uv run python scripts/scriptmgr.py check self_check

# JSON
uv run python scripts/scriptmgr.py check --json
```

输出示例：
```
Check: 31 scripts

NAME                CATEGORY        READY  RC   NOTE
self_check          cold_start      True   0    系统自检通过...
build_world_book    cold_start      n/a    -    no check_arg defined

15 ready, 0 not ready, 16 n/a
```

### 2.4 run — 执行脚本

默认走 `subprocess.run([sys.executable, ...])`，与 bootstrap 一致：

```bash
# 基础调用
uv run python scripts/scriptmgr.py run self_check --args "--dry-run"

# 多参数（空格分隔）
uv run python scripts/scriptmgr.py run manage_nodes --args "query --project-id ECOCITY-26"

# JSON 输出
uv run python scripts/scriptmgr.py run maintain_node_template_index --args "--check" --json
```

返回结构：
```json
{
  "success": true,
  "returncode": 0,
  "stdout": "...",
  "stderr": "",
  "script": "self_check",
  "code": 0
}
```

退出码：0=成功，1=执行失败，2=脚本不存在（与 ToolManager 一致）。

### 2.5 test — smoke 用例

跑 `emily-core/tests/scriptmgr_cases.yaml` 中的预设用例：

```bash
# 全部用例
uv run python scripts/scriptmgr.py test

# 单个
uv run python scripts/scriptmgr.py test self_check

uv run python scripts/scriptmgr.py test --json
```

测试无关注点隔离：用例失败不阻塞 CI，仅报告。

### 2.6 export — 重生成脚本目录文档

```bash
# 生成 Markdown 到 docs/
uv run python scripts/scriptmgr.py export --out docs/脚本工具目录.md

# 输出到终端（预览）
uv run python scripts/scriptmgr.py export

# JSON 格式
uv run python scripts/scriptmgr.py export --format json
```

`export` 幂等——连跑两次输出字节一致。> 预期用法：修改 `scripts_registry.yaml` 后跑 `export` 同步文档，提交前确认 diff 符合预期。

---

## 3. Python API

### 3.1 快速开始

```python
from emily_core.scripts.registry import load_registry
from emily_core.scripts.manager import ScriptManager

reg = load_registry()
sm = ScriptManager(reg)
```

### 3.2 方法参考

**自描述**

```python
# 列出全部脚本（轻量元信息）
sm.list()
# → [{"name": "self_check", "category": "cold_start", "status": "active", ...}, ...]

# 单个完整描述
sm.describe("self_check")
# → {"name": "self_check", "source_path": "scripts/self_check.py", ...}

# 全部描述
sm.describe(None)
# → {"scripts": [...], "count": 31}

# 导出
sm.export("markdown")   # → Markdown 字符串
sm.export("json")       # → {"scripts": [...], "count": 31}
```

**调用**

```python
# 自检
sm.check("self_check")
# → {"results": [{name, category, ready, returncode, note}, ...], "count": N}

sm.check(None)  # 全部

# 执行
sm.run("self_check", args=["--dry-run"], timeout=60)
# → {success, returncode, stdout, stderr, script, code}

# smoke 测试
sm.test("self_check")
sm.test(None)  # 全部
```

**查询**

```python
reg.get("self_check")                     # → ScriptEntry | None

reg.has("self_check")                     # → bool

reg.entries_with_auto_run("bootstrap")    # → 获取 auto_run=bootstrap 的脚本列表
```

---

## 4. 注册表 YAML 结构

文件位置：[emily-data/config/scripts_registry.yaml](../emily-data/config/scripts_registry.yaml)

### 4.1 顶层结构

```yaml
prologue: |    # 生成器输出的序言 Markdown（可选）
  > 本文档收录...

scripts:       # 脚本条目（必填）
  <name>:
    ...

aggregations:  # 聚合壳关系（供 export 附录 B）（可选）
  <shell>.py:
    children:
      - <child1>
      - <child2>
```

### 4.2 ScriptEntry 字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | str | ✅ | 脚本名（不含 `.py`），与 scripts/ 下文件名一致 |
| `description` | str | ✅ | 功能描述 |
| `category` | str | ✅ | 分组：`evolution_pipeline` / `cold_start` / `cognition_cycle` / `node_management` / `system_maintenance` / `business_tool` / `file_api_manage` / `data_collection` / `one_shot` / `aggregation_shell` |
| `source_path` | str | ✅ | 相对仓库根路径 |
| `invocation` | str | ✅ | 调用模板，`{args}` 占位参数 |
| `check_arg` | str 或 null | — | 自检 flag：`"--check"` / `"--dry-run"` / `"--preview"` / `"--probe"` / null |
| `run_args` | list | — | 默认运行参数 |
| `auto_run` | str 或 null | — | `"bootstrap"` → 启动时自动执行；`"scheduler:<name>"` → 调度器触发 |
| `auto_run_args` | list | — | 自动触发时参数 |
| `writes_db` | bool | — | 是否写数据库 |
| `aggregation_parent` | str 或 null | — | 归属聚合壳文件名 |
| `status` | str | — | `active` / `deprecated` / `one_shot` |
| `entrypoint` | str 或 null | — | `"module:function"` in-process 入口（未来 Phase 2） |
| `timeout_seconds` | int | — | 超时秒数，默认 60 |
| `flow_note` | str 或 null | — | 每日流程说明（供 doc 生成） |
| `scheduling_note` | str 或 null | — | 调度归属注（供 doc 生成） |

### 4.3 示例

```yaml
scripts:
  maintain_node_template_index:
    description: "扫描 node_templates/*.md 模板，生成/更新 index.yaml 索引"
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
    scheduling_note: "bootstrap 自动"

  fix_code_fences:
    description: "修复代码围栏格式问题"
    category: one_shot
    source_path: scripts/fix_code_fences.py
    invocation: uv run python scripts/fix_code_fences.py {args}
    check_arg: null
    writes_db: true
    aggregation_parent: null
    status: one_shot
    timeout_seconds: 60
    scheduling_note: "一次性"

aggregations:
  evolution.py:
    description: 进化闭环薄聚合壳（daily / weekly / morning / validate / full）
    children:
      - evolution_metrics
      - evolution_anomaly
      - evolution_insight
      - evolution_rules
      - evolution_patch
      - evolution_apply
      - evolution_validate
      - evolution_morning
      - evolution_node_close
```

---

## 5. 日常操作流程

### 5.1 新增一个脚本

1. 写脚本放到 `scripts/` 下
2. 编辑 [emily-data/config/scripts_registry.yaml](../emily-data/config/scripts_registry.yaml)，在 `scripts:` 下新增一条
3. 重生成文档：

```bash
uv run python scripts/scriptmgr.py export --out docs/脚本工具目录.md
```

4. 跑自检确认脚本能工作：
   ```bash
   uv run python scripts/scriptmgr.py check <新脚本名>
   ```

### 5.2 新增 bootstrap 自动运行的脚本

在注册表中设置 `auto_run: bootstrap` 和 `auto_run_args`，Emily 启动时会自动枚举执行：

```yaml
auto_run: bootstrap
auto_run_args: ["--auto"]
```

无需改 `bootstrap.py`。

### 5.3 给脚本补 smoke 用例

编辑 [emily-core/tests/scriptmgr_cases.yaml](../emily-core/tests/scriptmgr_cases.yaml)：

```yaml
my_script:
  args: ["--check"]
  expect_returncode: [0, 1]
  expect_stdout_contains: "OK"
```

然后：
```bash
uv run python scripts/scriptmgr.py test my_script
```

### 5.4 检查文档是否漂移

```bash
uv run python scripts/scriptmgr.py export --out docs/脚本工具目录.md
git diff docs/脚本工具目录.md
```

如果 diff 非空且不是你的预期变更，说明有人改了 `scripts/` 但未同步注册表。

---

## 6. 与 ToolManager 的边界

| 维度 | ToolManager | ScriptManager |
|------|------------|---------------|
| 管理对象 | `BusinessFlowTool`（LLM 运行时工具） | `scripts/*.py`（开发者/维护脚本） |
| 执行模型 | 进程内 `async handler(params) → dict` | subprocess `[sys.executable, script.py, args]` |
| 消费者 | LLM（结构化输出后框架直调） | 开发者（CLI / 调度器 / bootstrap） |
| 注册方式 | `register_all(core)` 命令式 | `scripts_registry.yaml` 声明式 |
| 是否共享 service 层 | ✅ 共享 | ✅ 共享 |
| 是否互相调用 | ❌ 互不调用 | ❌ 互不调用 |

---

## 7. 注意事项

- **bootstrap 并行加载**：`bootstrap.py` 和 `EmilyCore._ensure_initialized()` 各自独立调用 `load_registry()`，双重读 YAML 开销可忽略
- **subprocess 用 `sys.executable`**：不裸调 `python`，与 bootstrap 原写法一致
- **容器路径**：YAML 的 `source_path` 是仓库相对路径，运行时由 `PROJECT_ROOT` 解析
- **Windows GBK**：`scriptmgr.py` 已做 UTF-8 stdout 包装，与 `toolmgr.py` 一致
- **`backup.ps1`**：已纳入注册表但 `status: one_shot`，`run` 命令仍可执行但 `check` 跳过
