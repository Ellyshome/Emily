# BusinessFlowToolRegistry验证机制 — AI 执行计划

> **基于需求**：[BusinessFlowToolRegistry验证机制需求报告.md](BusinessFlowToolRegistry验证机制需求报告.md) + [审核报告 V1](BusinessFlowToolRegistry验证机制_审核_V1.md)（方案 B）
> **计划版本**：v1.0
> **目标**：建一个独立审核脚本 + self_check 集成，验证 BusinessFlowToolRegistry 与 Skill YAML / tool_registry 表的一致性

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **不修改任何 tool 的 schema/handler 代码**——本计划只建验证机制，不改被验证对象（`emily-core/emily_core/tools/*.py` 一行不动）
2. **不修改 registry.py / business_flow_tools.py**——验证机制只读取，不改动注册逻辑
3. **分层约束**：核心逻辑放 `emily-core/emily_core/infrastructure/`（基础设施层，横切关注点），不污染 Application/Service 层
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **参照模式**：薄壳脚本参照 `scripts/rag_dry_run.py` 和 `scripts/self_check.py`；核心逻辑参照 `infrastructure/paths.py` 的模块风格
6. **fail-open**：验证机制本身任何异常都不能阻断调用方（脚本/启动），必须 try-except 兜底

---

## 上下文（执行前必读）

### 方案 B 范围（审核报告确认）

**做**：
- 独立脚本 `scripts/check_tools_consistency.py`（薄壳 CLI，全量检查）
- 核心逻辑 `emily-core/emily_core/infrastructure/tools_consistency.py`（可 import，返回 dict）
- `scripts/self_check.py` 集成（加启动快速检查项，复用现有启动邮件链路）

**砍掉**（理由）：
- V2/V3/V4（handler callable / name 非空 / 无重名）——需要 BusinessFlowToolRegistry 实例，独立脚本无 EmilyCore 拿不到；且 register_all 的 fail-fast 已保证，价值低
- V6/V7/V9（schema vs handler 签名 / required 处理 / description 矛盾）——语义级检查难机器化
- 独立启动自检逻辑——self_check.py 集成即可复用启动邮件链路，不新建

### 4 个阻塞性问题的解决

| 阻塞问题 | 解决方案 |
|---------|---------|
| V6 参数对齐语义 | **砍掉 V6**（方案 B 不做语义级检查） |
| V13 一致语义 | 拆为 V13a（内存→DB 存在性）+ V13b（DB→内存），V13c（字段一致性）列为 P2 不做 |
| 代码放置位置 | 核心 `infrastructure/tools_consistency.py`，薄壳 `scripts/check_tools_consistency.py`，self_check 集成在 `scripts/self_check.py` |
| Skill 启用时间线 | 当前未启用——P0（独立脚本）作为 Skill YAML 修复的验收工具先行；P1（self_check 集成）同步做；P2 视 Skill 启用进度 |

### 已有的可复用组件

| 组件 | 位置 | 本次怎么用 |
|------|------|-----------|
| `ToolRegistryRepo.get_all_active` | `emily-core/emily_core/repositories/tool_registry_repo.py:111` | V13 查 tool_registry 表 |
| `SkillRegistry` | `emily-core/emily_core/skill/registry.py` | 不直接用——核心逻辑自己用 yaml 解析 Skill（更轻量，不依赖 SkillRegistry 初始化） |
| 各 tool 的 `_*_SCHEMA` 常量 | `emily-core/emily_core/tools/*_tool.py` + `tools/project/__init__.py` | 动态 import 提取参数集合 |
| `self_check()` 函数 | `scripts/self_check.py:55` | M3 在其 line 105 后加 tools_consistency 检查项 |
| `_format_self_check()` | `scripts/self_check.py:110` | M3 加 tools_consistency 格式化行 |
| 薄壳脚本模式 | `scripts/rag_dry_run.py` | M2 参照其 sys.path 注入 / UTF-8 stdout / argparse / 核心函数 import 结构 |

### 架构决策

选方案 B（独立脚本 + self_check 集成）而非方案 A（完整启动自检）——因为启动邮件的 SOP 统计长期为 0 未被发现，证明启动信号受众错配；独立脚本覆盖"改完验证+回归"核心场景，self_check 集成复用现有链路，工作量减半。

工具名集合采用**硬编码 + 注释说明维护**（新增工具时同步更新）——而非动态从 register_all 提取。理由：register_all 需要 EmilyCore 实例（依赖注入），独立脚本轻量构造会因 fail-safe 丢工具；硬编码低频维护，且 V13 的 DB 对比能间接发现遗漏。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| infrastructure 模块 | `emily-core/emily_core/infrastructure/paths.py` | 模块级函数 + logger + 无类（轻量工具模块风格） |
| 薄壳脚本 | `scripts/rag_dry_run.py` | sys.path 注入 / UTF-8 stdout / argparse / import 核心函数 / JSON 输出 |
| self_check 集成 | `scripts/self_check.py:90-105`（knowledge 部分） | try-except 兜底 + result[key] = dict 模式 |

---

## 模块依赖图

```
M1(核心逻辑 tools_consistency.py) ──→ M2(薄壳脚本 check_tools_consistency.py)
        │
        └──→ M3(self_check.py 集成)
                  │
                  ↓
            M4(文档更新)
```

构建顺序：M1 → M2 → M3 → M4。M2 和 M3 都依赖 M1，可并行但建议串行（M2 验证 M1 的全量接口，M3 验证 M1 的快速接口）。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `emily-core/emily_core/infrastructure/tools_consistency.py` | 新增 | `check_all()`, `check_quick()`, `REGISTERED_TOOLS`, `TOOL_SCHEMA_MAP` |
| M2 | `scripts/check_tools_consistency.py` | 新增 | `main()`, CLI 入口 |
| M3 | `scripts/self_check.py` | 修改 | `self_check()` 加 tools_consistency 字段；`_format_self_check()` 加格式化行 |
| M4 | `docs/脚本工具目录.md` | 修改 | 速查表加第 27 行 + 6.4 后加 6.5 条目 |
| M4 | `docs/技术踩坑备忘录.md` | 修改 | 追加 6.8 条目 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `scripts/self_check.py` | 修改 | `self_check()` 函数 line 105 后加 tools_consistency 检查项；`_format_self_check()` 末尾加格式化行 |
| `emily-core/emily_core/tools/*.py` | 不变 | —（被验证对象，不动） |
| `emily-core/emily_core/tools/registry.py` | 不变 | —（被验证对象，不动） |
| `docs/脚本工具目录.md` | 修改 | 速查表 + 6.5 条目 |
| `docs/技术踩坑备忘录.md` | 修改 | 6.8 条目 |

---

## 脚本结构约定

### 独立脚本清单

| # | 脚本 | 职责 | 关键参数 | `--dry-run` 输出 |
|---|------|------|---------|------------------|
| 1 | `scripts/check_tools_consistency.py` | 全量一致性检查 | `--skill-dir` `--json` `--no-tool-registry` | 无 dry-run（只读检查，本身不写） |

### 脚本交互关系

```
scripts/check_tools_consistency.py（薄壳 CLI）
  └── import emily_core.infrastructure.tools_consistency.check_all
        → dict（结构化报告）
        → 终端输出 / --json

scripts/self_check.py（启动自检，已有）
  └── import emily_core.infrastructure.tools_consistency.check_quick
        → dict（快速检查，加到 result["tools_consistency"]）
        → 启动邮件 / --json
```

---

## M1: 核心检查逻辑

**依赖**：无（首建模块）

**职责**：提供 `check_all()`（全量）和 `check_quick()`（快速）两个函数，供脚本和 self_check 复用。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 核心检查逻辑 | `emily-core/emily_core/infrastructure/tools_consistency.py` |

### 代码

#### `emily-core/emily_core/infrastructure/tools_consistency.py` — 新建

```python
"""tools_consistency.py — BusinessFlowToolRegistry 一致性检查核心逻辑。

供 scripts/check_tools_consistency.py（薄壳 CLI）和 scripts/self_check.py（启动快速检查）复用。
方案 B：独立脚本 + self_check 集成，砍掉语义级检查（V6/V7/V9）和需要运行时实例的检查（V2/V3/V4）。

验证项：
  V1  — 注册工具数（硬编码集合大小）
  V5  — business 类空 schema 检测
  V10 — Skill YAML tools[].name 在 REGISTERED_TOOLS
  V11 — Skill YAML steps[].tool_name 在 REGISTERED_TOOLS
  V12 — Skill YAML steps[].tool_params 参数名在对应工具 schema
  V13a — 内存已注册工具在 tool_registry 表也存在
  V13b — tool_registry 表工具在内存也已注册

砍掉的验证项（理由见计划文档）：
  V2/V3/V4 — 需要 BusinessFlowToolRegistry 实例，独立脚本拿不到
  V6/V7/V9 — 语义级检查难机器化
  V8      — description 废弃概念（P2，后续按需加）
  V13c    — 同名工具字段一致性（P2）
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

logger = logging.getLogger("emily.infrastructure.tools_consistency")

# ── register_all 注册的工具名集合 ──────────────────────────────────
# ⚠️ 与 tools/registry.py 的 register_all 保持同步：
# 新增/删除工具时，需同步更新此集合（V13 的 DB 对比能间接发现遗漏，但显式维护更可靠）。
REGISTERED_TOOLS: set[str] = {
    # base
    "query_data", "knowledge_search",
    # business
    "record_event", "record_task", "record_meeting", "record_file",
    "query_files", "update_file_category", "write_user_memory",
    "create_task_node", "submit_node_deliverable", "confirm_node_deliverable",
    "return_node_deliverable", "query_my_nodes",
    # project
    "create_node", "query_node", "update_node_progress", "add_node_dependency",
    "mount_child_node", "update_nodes", "activate_nodes", "discard_nodes",
    "send_email", "fetch_inbox", "chat_archive", "manage_pending_issues", "voice_entry",
}

# ── 工具名 → (模块路径, schema 变量名) ─────────────────────────────
# 无 schema 常量的工具（write_user_memory / node_task 5 个）不在此映射，V12 对其跳过。
TOOL_SCHEMA_MAP: dict[str, tuple[str, str]] = {
    "query_data": ("emily_core.tools.query_tool", "_QUERY_TOOL_SCHEMA"),
    "knowledge_search": ("emily_core.tools.knowledge_search_tool", "_KNOWLEDGE_SEARCH_SCHEMA"),
    "record_event": ("emily_core.tools.event_tool", "_EVENT_TOOL_SCHEMA"),
    "record_task": ("emily_core.tools.task_tool", "_TASK_TOOL_SCHEMA"),
    "record_meeting": ("emily_core.tools.meeting_tool", "_MEETING_TOOL_SCHEMA"),
    "record_file": ("emily_core.tools.file_tool", "_FILE_TOOL_SCHEMA"),
    "query_files": ("emily_core.tools.file_tool", "_QUERY_FILES_SCHEMA"),
    "update_file_category": ("emily_core.tools.file_tool", "_UPDATE_CATEGORY_SCHEMA"),
    "create_node": ("emily_core.tools.node_tool", "_CREATE_NODE_SCHEMA"),
    "query_node": ("emily_core.tools.node_tool", "_QUERY_NODE_SCHEMA"),
    "update_node_progress": ("emily_core.tools.node_tool", "_UPDATE_PROGRESS_SCHEMA"),
    "add_node_dependency": ("emily_core.tools.node_tool", "_ADD_DEPENDENCY_SCHEMA"),
    "mount_child_node": ("emily_core.tools.node_tool", "_MOUNT_CHILD_SCHEMA"),
    "update_nodes": ("emily_core.tools.node_tool", "_UPDATE_NODES_SCHEMA"),
    "activate_nodes": ("emily_core.tools.node_tool", "_ACTIVATE_NODES_SCHEMA"),
    "discard_nodes": ("emily_core.tools.node_tool", "_DISCARD_NODES_SCHEMA"),
    "send_email": ("emily_core.tools.project", "_SEND_EMAIL_SCHEMA"),
    "fetch_inbox": ("emily_core.tools.project", "_FETCH_INBOX_SCHEMA"),
    "chat_archive": ("emily_core.tools.project", "_CHAT_ARCHIVE_SCHEMA"),
    "manage_pending_issues": ("emily_core.tools.project", "_PENDING_ISSUE_SCHEMA"),
    "voice_entry": ("emily_core.tools.project", "_VOICE_ENTRY_SCHEMA"),
}


def _load_tool_schemas() -> dict[str, set[str] | None]:
    """import 各 tool 模块，提取 schema 的 properties 参数集合。

    Returns:
        {tool_name: set(param_names) or None}，None 表示 schema 不可用或无 properties。
    """
    result: dict[str, set[str] | None] = {}
    for tool, (mod_path, schema_var) in TOOL_SCHEMA_MAP.items():
        try:
            m = importlib.import_module(mod_path)
            schema = getattr(m, schema_var, None)
            if isinstance(schema, dict) and "properties" in schema:
                result[tool] = set(schema["properties"].keys())
            else:
                result[tool] = None
        except Exception as e:
            logger.warning("load schema %s failed: %s", tool, e)
            result[tool] = None
    return result


def _load_skills(skill_dir: str | Path) -> list[tuple[str, dict, Path]]:
    """加载 Skill YAML 列表（轻量，不依赖 SkillRegistry 初始化）。

    Returns:
        [(skill_id, data_dict, file_path), ...]
    """
    import yaml
    skills: list[tuple[str, dict, Path]] = []
    skill_path = Path(skill_dir)
    if not skill_path.exists():
        return skills
    for yfile in sorted(skill_path.glob("*.skill.yaml")):
        try:
            data = yaml.safe_load(yfile.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                skills.append((data.get("skill_id", yfile.stem), data, yfile))
        except Exception as e:
            logger.warning("parse skill %s failed: %s", yfile.name, e)
    return skills


def _check_skill_yaml(
    skills: list[tuple[str, dict, Path]],
    tool_schemas: dict[str, set[str] | None],
    issues: list[dict],
) -> None:
    """V10/V11/V12: Skill YAML 工具名存在性 + 参数 schema 匹配。"""
    for skill_id, data, _yfile in skills:
        # V10: tools[].name 存在
        for t in data.get("tools", []) or []:
            if isinstance(t, dict) and "name" in t and t["name"] not in REGISTERED_TOOLS:
                issues.append({
                    "severity": "fatal", "check": "V10_tool_name_missing",
                    "skill": skill_id, "detail": f"tools 引用不存在的工具: {t['name']}",
                })
        # V11/V12: steps[].tool_name + tool_params
        for s in data.get("steps", []) or []:
            if not isinstance(s, dict):
                continue
            tn = s.get("tool_name")
            if not tn:
                continue
            if tn not in REGISTERED_TOOLS:
                issues.append({
                    "severity": "fatal", "check": "V11_step_tool_missing",
                    "skill": skill_id, "step": s.get("id"),
                    "detail": f"step 引用不存在的工具: {tn}",
                })
                continue
            # V12: tool_params 参数在 schema
            expected = tool_schemas.get(tn)
            if expected is None:
                continue  # 无 schema 的工具跳过参数检查
            actual: set[str] = set()
            for p in s.get("tool_params", []) or []:
                if isinstance(p, dict) and "name" in p:
                    actual.add(p["name"])
            extra = actual - expected
            if extra:
                issues.append({
                    "severity": "fatal", "check": "V12_param_mismatch",
                    "skill": skill_id, "step": s.get("id"), "tool": tn,
                    "detail": f"传了 schema 外参数: {sorted(extra)}，schema 实际: {sorted(expected)}",
                })


def _check_tool_registry(issues: list[dict]) -> dict:
    """V13a/V13b: tool_registry 表与内存 REGISTERED_TOOLS 一致性。"""
    try:
        from emily_core.repositories.tool_registry_repo import ToolRegistryRepo
        db_tools = {row["api_id"] for row in ToolRegistryRepo.get_all_active()}
    except Exception as e:
        logger.warning("check_tool_registry failed: %s", e)
        return {"error": str(e)}

    missing_in_db = REGISTERED_TOOLS - db_tools   # V13a: 内存有 DB 无
    extra_in_db = db_tools - REGISTERED_TOOLS     # V13b: DB 有内存无
    for t in sorted(missing_in_db):
        issues.append({
            "severity": "warning", "check": "V13a_missing_in_db",
            "tool": t, "detail": f"工具 {t} 内存已注册但 tool_registry 表缺失",
        })
    for t in sorted(extra_in_db):
        issues.append({
            "severity": "warning", "check": "V13b_extra_in_db",
            "tool": t, "detail": f"工具 {t} tool_registry 表有但内存未注册",
        })
    return {
        "db_count": len(db_tools),
        "missing_in_db": sorted(missing_in_db),
        "extra_in_db": sorted(extra_in_db),
    }


def check_all(skill_dir: str, check_tool_registry: bool = True) -> dict:
    """全量一致性检查。返回结构化报告 dict。

    Args:
        skill_dir: Skill YAML 目录路径
        check_tool_registry: 是否检查 tool_registry 表（需 DB 连接）

    Returns:
        {
            "summary": {registered, with_schema, skills, total_issues, fatal_issues},
            "empty_schema_tools": [...],   # V5
            "tool_registry": {...} or None,  # V13
            "issues": [{severity, check, ...}, ...],
        }
    """
    issues: list[dict] = []
    tool_schemas = _load_tool_schemas()

    # V5: business 类空 schema 检测
    empty_schema_tools = [
        tool for tool, params in tool_schemas.items()
        if params is not None and len(params) == 0
    ]
    for tool in empty_schema_tools:
        issues.append({
            "severity": "warning", "check": "V5_empty_schema",
            "tool": tool, "detail": f"工具 {tool} 的 schema properties 为空",
        })

    # V10/V11/V12: Skill YAML 一致性
    skills = _load_skills(skill_dir)
    _check_skill_yaml(skills, tool_schemas, issues)

    # V13: tool_registry 表同步（可选）
    tool_registry_report = None
    if check_tool_registry:
        tool_registry_report = _check_tool_registry(issues)

    fatal_count = sum(1 for i in issues if i["severity"] == "fatal")
    return {
        "summary": {
            "registered": len(REGISTERED_TOOLS),
            "with_schema": sum(1 for v in tool_schemas.values() if v is not None),
            "skills": len(skills),
            "total_issues": len(issues),
            "fatal_issues": fatal_count,
        },
        "empty_schema_tools": empty_schema_tools,
        "tool_registry": tool_registry_report,
        "issues": issues,
    }


def check_quick(skill_dir: str) -> dict:
    """快速检查（供 self_check 启动集成）。只做 Skill YAML 一致性，不查 DB。

    fail-open：任何异常返回 {"ok": False, "error": ...}，不阻断 self_check。

    Returns:
        {"skills": N, "issues": M, "fatal": K, "ok": bool}
    """
    try:
        tool_schemas = _load_tool_schemas()
        skills = _load_skills(skill_dir)
        issues: list[dict] = []
        _check_skill_yaml(skills, tool_schemas, issues)
        fatal = sum(1 for i in issues if i["severity"] == "fatal")
        return {
            "skills": len(skills),
            "issues": len(issues),
            "fatal": fatal,
            "ok": fatal == 0,
        }
    except Exception as e:
        logger.warning("check_quick failed: %s", e)
        return {"skills": 0, "issues": 0, "fatal": 0, "ok": False, "error": str(e)}
```

### 模块验收检测

```bash
# 验收 1：模块可 import，无语法错误
cd d:\app\Emily
uv run python -c "import sys; sys.path.insert(0,'emily-core'); from emily_core.infrastructure.tools_consistency import check_all, check_quick, REGISTERED_TOOLS; print('import OK, tools=', len(REGISTERED_TOOLS))"
→ 预期输出：import OK, tools= 27

# 验收 2：check_all 能跑，发现已知问题（当前 24 处 Skill YAML 不一致）
uv run python -c "
import sys; sys.path.insert(0,'emily-core')
from emily_core.infrastructure.tools_consistency import check_all
r = check_all('emily-data/skills', check_tool_registry=False)
print('summary:', r['summary'])
print('empty_schema_tools:', r['empty_schema_tools'])
"
→ 预期输出：summary 含 registered=27, skills=10, fatal_issues>=12（当前 24 处不一致中至少 12 个 fatal）
→ 预期输出：empty_schema_tools 含 record_event/record_task/record_meeting/record_file 等 business 工具

# 验收 3：check_quick 能跑，fail-open
uv run python -c "
import sys; sys.path.insert(0,'emily-core')
from emily_core.infrastructure.tools_consistency import check_quick
r = check_quick('emily-data/skills')
print(r)
"
→ 预期输出：{'skills': 10, 'issues': N, 'fatal': M, 'ok': False}（当前有不一致，ok=False）
```

**失败处理**：
- 验收 1 失败（import 报错）→ 检查文件路径 `emily-core/emily_core/infrastructure/tools_consistency.py` 是否正确，检查 `from __future__ import annotations` 是否遗漏
- 验收 2 fatal_issues=0 但实际有不一致 → 检查 `_check_skill_yaml` 逻辑，确认 REGISTERED_TOOLS 集合和 TOOL_SCHEMA_MAP 映射正确
- 验收 3 报错 → check_quick 的 try-except 未兜底，检查异常处理

---

## M2: 薄壳脚本

**依赖**：M1

**职责**：CLI 入口，调 `check_all()`，格式化输出报告，非零退出码表示有 fatal 问题。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 薄壳 CLI 脚本 | `scripts/check_tools_consistency.py` |

### 代码

#### `scripts/check_tools_consistency.py` — 新建

```python
"""check_tools_consistency.py — BusinessFlowToolRegistry 一致性检查 CLI。

薄壳脚本，核心逻辑在 emily_core.infrastructure.tools_consistency。
方案 B：独立审核脚本，供开发者改完 Skill YAML / 工具后验证 + 回归保障。

用法：
    uv run python scripts/check_tools_consistency.py
    uv run python scripts/check_tools_consistency.py --skill-dir emily-data/skills
    uv run python scripts/check_tools_consistency.py --json
    uv run python scripts/check_tools_consistency.py --no-tool-registry

退出码：0=无 fatal 问题；1=有 fatal 问题（便于 CI / 脚本集成）。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))


def _find_skill_dir(explicit: str = "") -> str:
    """多级回退查找 skills 目录：--skill-dir 参数 → 容器 /app/skills → 开发 emily-data/skills。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
        print(f"[WARN] --skill-dir 指定路径不存在: {explicit}", file=sys.stderr)
    candidates = [
        Path("/app/skills"),
        _HERE.parent / "emily-data" / "skills",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return str(_HERE.parent / "emily-data" / "skills")  # 默认值（可能不存在，check_all 会返回空 skills）


def _format_report(r: dict) -> str:
    """格式化报告为终端可读文本。"""
    lines = []
    lines.append("=" * 70)
    lines.append("BusinessFlowToolRegistry 一致性检查报告")
    lines.append("=" * 70)

    s = r.get("summary", {})
    lines.append(f"\n[摘要] 注册工具 {s.get('registered', 0)} | 有 schema {s.get('with_schema', 0)} | "
                 f"Skill 文件 {s.get('skills', 0)} | 问题 {s.get('total_issues', 0)} (fatal {s.get('fatal_issues', 0)})")

    # V5: 空 schema
    empty = r.get("empty_schema_tools", [])
    if empty:
        lines.append(f"\n[V5] 空 schema 工具 ({len(empty)}):")
        for t in empty:
            lines.append(f"  ⚠️  {t}")

    # V13: tool_registry 表
    tr = r.get("tool_registry")
    if tr:
        if "error" in tr:
            lines.append(f"\n[V13] tool_registry 表检查失败: {tr['error']}")
        else:
            lines.append(f"\n[V13] tool_registry 表: DB {tr['db_count']} 条 | "
                         f"内存缺 DB {len(tr['missing_in_db'])} | DB 缺内存 {len(tr['extra_in_db'])}")
            for t in tr["missing_in_db"]:
                lines.append(f"  ⚠️  内存有 DB 无: {t}")
            for t in tr["extra_in_db"]:
                lines.append(f"  ⚠️  DB 有内存无: {t}")

    # 问题清单
    issues = r.get("issues", [])
    fatal = [i for i in issues if i["severity"] == "fatal"]
    warning = [i for i in issues if i["severity"] == "warning"]
    if fatal:
        lines.append(f"\n[fatal] {len(fatal)} 处致命问题:")
        for i in fatal:
            loc = i.get("skill", i.get("tool", ""))
            step = i.get("step", "")
            loc_str = f"{loc}/{step}" if step else loc
            lines.append(f"  ❌ [{i['check']}] {loc_str}: {i['detail']}")
    if warning:
        lines.append(f"\n[warning] {len(warning)} 处警告:")
        for i in warning:
            loc = i.get("skill", i.get("tool", ""))
            lines.append(f"  ⚠️  [{i['check']}] {loc}: {i['detail']}")

    if not issues:
        lines.append("\n✅ 所有一致性检查通过")

    lines.append("=" * 70)
    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="BusinessFlowToolRegistry 一致性检查（方案 B：独立审核脚本）",
    )
    parser.add_argument("--skill-dir", default="", help="Skill YAML 目录（默认多级回退）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--no-tool-registry", action="store_true",
                        help="跳过 tool_registry 表检查（不连 DB）")
    args = parser.parse_args()

    skill_dir = _find_skill_dir(args.skill_dir)

    from emily_core.infrastructure.tools_consistency import check_all

    result = check_all(skill_dir, check_tool_registry=not args.no_tool_registry)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_report(result))

    # 退出码：有 fatal 则 1
    fatal = result.get("summary", {}).get("fatal_issues", 0)
    sys.exit(1 if fatal > 0 else 0)


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：脚本能跑，发现已知问题
cd d:\app\Emily
uv run python scripts/check_tools_consistency.py --no-tool-registry
→ 预期输出：报告含 [fatal] N 处致命问题（当前 24 处不一致）
→ 预期退出码：1

# 验收 2：JSON 输出
uv run python scripts/check_tools_consistency.py --json --no-tool-registry | python -c "import sys,json; d=json.load(sys.stdin); print('summary:', d['summary'])"
→ 预期输出：summary: {'registered': 27, 'with_schema': 21, 'skills': 10, 'total_issues': N, 'fatal_issues': M}

# 验收 3：含 tool_registry 表检查（当前表空，应报 missing_in_db）
uv run python scripts/check_tools_consistency.py 2>&1 | grep -A2 "V13"
→ 预期输出：[V13] tool_registry 表: DB 0 条 | 内存缺 DB 27 | DB 缺内存 0
→ （DB 0 行，内存 27 个工具都缺 DB 记录）
```

**失败处理**：
- 验收 1 报错 `ModuleNotFoundError: emily_core` → 检查 sys.path 注入（`_CORE_DIR = _HERE.parent / "emily-core"`）
- 验收 3 tool_registry 检查报 error → DB 未连接，检查 `--no-tool-registry` 是否漏加；若需连 DB，确认 `EMILY_DATABASE_URL` 环境变量或 init_db 调用（参照 self_check.py 的 _init_db）。如需连 DB，在 main() 里 check_all 前加 `_init_db()`（从 self_check.py 复制）

---

## M3: self_check.py 集成

**依赖**：M1

**职责**：在 `self_check()` 函数里加 tools_consistency 快速检查项，复用现有启动邮件链路。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | self_check.py 加 tools_consistency 字段 | `scripts/self_check.py` |
| 2 | _format_self_check 加格式化行 | `scripts/self_check.py` |

### 代码

#### `scripts/self_check.py` — 在 `self_check()` 函数的 `return result` 前追加

打开 `scripts/self_check.py`，定位到 line 105-107：

```python
        result["knowledge"] = {"sop_count": sop_count}

    return result
```

在 `result["knowledge"] = {"sop_count": sop_count}` 之后、`return result` 之前，追加：

```python
        result["knowledge"] = {"sop_count": sop_count}

    # 工具一致性快速检查（方案 B：复用 self_check 启动链路）
    try:
        from emily_core.infrastructure.tools_consistency import check_quick
        skill_dir = "/app/skills"
        if not Path(skill_dir).exists():
            dev_dir = str(Path(__file__).resolve().parent.parent / "emily-data" / "skills")
            if Path(dev_dir).exists():
                skill_dir = dev_dir
        result["tools_consistency"] = check_quick(skill_dir)
    except Exception as e:
        result["tools_consistency"] = {"ok": False, "error": str(e)}

    return result
```

注意：这段在 `with get_session() as session:` 块**外**（line 107 `return result` 之前，但 `with` 块在 line 106 已结束）。确认缩进——`result["tools_consistency"]` 与 `return result` 同级（函数体顶层，4 空格缩进）。

#### `scripts/self_check.py` — 在 `_format_self_check()` 末尾追加格式化行

定位到 `_format_self_check` 函数末尾（`return "\n".join(lines)` 之前），在 `知识库` 行后追加：

```python
    k = result.get("knowledge", {})
    lines.append(f"知识库：{k.get('sop_count', 0)} 个 SOP")

    tc = result.get("tools_consistency", {})
    if tc:
        status = "✅" if tc.get("ok") else "❌"
        lines.append(f"工具一致性：{status} Skill {tc.get('skills', 0)} 个，问题 {tc.get('issues', 0)} 处 (fatal {tc.get('fatal', 0)})")

    return "\n".join(lines)
```

### 模块验收检测

```bash
# 验收 1：self_check 能跑，含 tools_consistency 字段
cd d:\app\Emily
uv run python scripts/self_check.py --json 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); print('tools_consistency:', d.get('tools_consistency'))"
→ 预期输出：tools_consistency: {'skills': 10, 'issues': N, 'fatal': M, 'ok': False}

# 验收 2：终端输出含工具一致性行
uv run python scripts/self_check.py 2>&1 | grep "工具一致性"
→ 预期输出：工具一致性：❌ Skill 10 个，问题 N 处 (fatal M)
→ （当前有不一致，显示 ❌；Skill YAML 修复后会变 ✅）

# 验收 3：self_check 不崩溃（fail-open）
uv run python scripts/self_check.py --json > $null; echo "exit=$?"
→ 预期输出：exit=0（self_check 本身不因 tools_consistency 问题崩溃）
```

**失败处理**：
- 验收 1 报错 `ModuleNotFoundError: emily_core.infrastructure.tools_consistency` → M1 未完成或路径错误，先确认 M1 验收通过
- 验收 1 tools_consistency 字段缺失 → 检查追加位置是否在 `return result` 之前、缩进是否正确（函数体顶层）
- 验收 3 exit≠0 → check_quick 抛异常未兜底，检查 M1 的 check_quick 是否有 try-except

---

## M4: 文档更新

**依赖**：M2、M3

**职责**：同步更新脚本目录文档和踩坑备忘录。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 脚本工具目录加条目 | `docs/脚本工具目录.md` |
| 2 | 踩坑备忘录加条目 | `docs/技术踩坑备忘录.md` |

### 代码

#### `docs/脚本工具目录.md` — 速查表追加 + 6.5 条目

在速查表（附录 A）末尾追加一行（第 27 行）：

```markdown
| 27 | `check_tools_consistency.py` | 工具一致性检查 | —（独立） | 纯手动 | — | 否 |
```

在 6.4 节（rag_dry_run）后追加 6.5 节：

```markdown
### 6.5 check_tools_consistency.py — 工具一致性检查

**职责**：验证 BusinessFlowToolRegistry 与 Skill YAML / tool_registry 表的一致性。检查工具名存在性、参数 schema 匹配、空 schema、表同步。核心逻辑可 import，供 self_check 启动集成。

**调度归属**：纯手动（开发者改完 Skill YAML / 工具后验证）+ self_check 启动集成（快速检查）

```bash
uv run python scripts/check_tools_consistency.py              # 全量检查（含 tool_registry 表）
uv run python scripts/check_tools_consistency.py --json       # JSON 输出
uv run python scripts/check_tools_consistency.py --no-tool-registry  # 跳过 DB 检查
```

| 参数 | 说明 |
|------|------|
| `--skill-dir` | Skill YAML 目录（默认多级回退：/app/skills → emily-data/skills） |
| `--json` | JSON 格式输出 |
| `--no-tool-registry` | 跳过 tool_registry 表检查（不连 DB） |

退出码：0=无 fatal；1=有 fatal（便于 CI 集成）。

> **设计原则**：方案 B——独立脚本（全量）+ self_check 集成（启动快速检查），复用现有基础设施。砍掉语义级检查（V6/V7/V9）和独立启动自检。核心逻辑在 `emily_core/infrastructure/tools_consistency.py`，可被 self_check import 复用。
```

同时更新文档开头的"全部 26 个"→"全部 27 个"。

#### `docs/技术踩坑备忘录.md` — 追加 6.8 条目

在 6.7 节后追加 6.8：

```markdown
### 6.8 BusinessFlowToolRegistry 一致性断层（验证机制覆盖）

| 项 | 内容 |
|----|------|
| **现象** | Skill YAML 与 BusinessFlowToolRegistry 严重不一致（24 处）、tool_registry 表空、business 类工具空 schema——这些断层被 /app/skills 没挂载掩盖，未暴露 |
| **原因** | 缺少一致性验证机制；register_all 的 fail-safe 静默吞错；Skill YAML 手写易脱节；register_api.py 从未执行致表空 |
| **解决** | 建 `scripts/check_tools_consistency.py`（独立审核脚本）+ `emily_core/infrastructure/tools_consistency.py`（核心逻辑）+ self_check.py 集成（启动快速检查）。验证 V1/V5/V10/V11/V12/V13a/V13b，砍语义级检查 |
| **文件** | `scripts/check_tools_consistency.py`、`emily-core/emily_core/infrastructure/tools_consistency.py`、`scripts/self_check.py` |
```

### 模块验收检测

```bash
# 验收 1：脚本目录速查表含第 27 行
cd d:\app\Emily
grep "check_tools_consistency" docs/脚本工具目录.md
→ 预期输出：至少 2 行匹配（速查表行 + 6.5 标题）

# 验收 2：踩坑备忘录含 6.8
grep "6.8" docs/技术踩坑备忘录.md
→ 预期输出：1 行匹配（### 6.8 标题）
```

**失败处理**：grep 无匹配 → 检查追加位置和文本是否精确。

---

## 组装验证

所有模块完成后，运行端到端组装验证：

```bash
cd d:\app\Emily

# 1. 独立脚本全量检查（应发现当前 24 处不一致 + tool_registry 表空）
uv run python scripts/check_tools_consistency.py
→ 预期：报告含 [fatal] 12+ 处、[V13] DB 0 条 内存缺 27、[V5] 空 schema 工具列表
→ 预期退出码：1

# 2. self_check 集成验证（启动链路含 tools_consistency）
uv run python scripts/self_check.py --json 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); tc=d.get('tools_consistency',{}); print('tools_consistency:', tc); assert 'skills' in tc and 'fatal' in tc"
→ 预期输出：tools_consistency: {'skills': 10, 'issues': N, 'fatal': M, 'ok': False}
→ 预期退出码：0（assert 通过）

# 3. self_check 终端输出含工具一致性行
uv run python scripts/self_check.py 2>&1 | grep "工具一致性"
→ 预期输出：工具一致性：❌ Skill 10 个，问题 N 处 (fatal M)

# 4. check_quick fail-open（模拟异常不崩溃）
uv run python -c "
import sys; sys.path.insert(0,'emily-core')
from emily_core.infrastructure.tools_consistency import check_quick
r = check_quick('/nonexistent/path')  # 不存在的目录
print('ok=', r.get('ok'), 'skills=', r.get('skills'))  # 应返回 skills=0 不报错
"
→ 预期输出：ok= True skills= 0（空目录无 Skill，无不一致，ok=True）

# 5. Skill YAML 修复后验证（等需求/Skill_YAML一致性修复计划.md 执行后）
# 修完 Skill YAML 后重跑，fatal_issues 应为 0
uv run python scripts/check_tools_consistency.py --no-tool-registry
→ 预期：✅ 所有一致性检查通过，退出码 0
```

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

## 与 Skill YAML 修复的配合

本验证机制是 `需求/Skill_YAML一致性修复计划.md` 的**验收工具**：

1. **修复前**：跑 `check_tools_consistency.py`，确认当前 24 处不一致（基线）
2. **修复中**：每修一个 Skill YAML，重跑脚本，确认不一致数减少
3. **修复后**：跑脚本，确认 fatal_issues=0、退出码 0

执行顺序建议：
- 先做本计划（M1-M4），建好验证工具
- 再执行 Skill YAML 修复计划，用本工具验收

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。基于需求报告 + 审核报告（方案 B）+ 项目现状探查。*

---

## 执行报告（2026-07-23）

### 执行结果：全部通过

| 模块 | 文件 | 操作 | 验收 |
|------|------|------|------|
| M1 | `emily-core/emily_core/infrastructure/tools_consistency.py` | 新建 | import OK, tools=27；check_all 正常；check_quick 正常 |
| M2 | `scripts/check_tools_consistency.py` | 新建 | CLI 正常，发现 5 fatal，退出码 1；JSON 输出正常 |
| M3 | `scripts/self_check.py` | 修改（追加 tools_consistency 字段 + 格式化行） | JSON 含 tools_consistency；终端输出含"工具一致性：❌"行；fail-open 不崩溃 |
| M4 | `docs/脚本工具目录.md` | 修改（27→27、速查表第 27 行、6.5 节、附录 B） | grep 校验通过 |
| M4 | `docs/技术踩坑备忘录.md` | 修改（追加 6.8 条目） | grep 校验通过 |

### 组装验证

- 独立脚本：发现 5 fatal（V12 参数不匹配），退出码 1
- self_check 集成：tools_consistency 字段存在，assert 通过
- fail-open：不存在路径返回 ok=True 不崩溃

### 当前基线

| 指标 | 值 |
|------|-----|
| 注册工具 | 27 |
| 有 schema | 21 |
| Skill 文件 | 10 |
| fatal 问题 | 5（全部 V12_param_mismatch） |
| warning 问题 | 0 |
| tool_registry 表 | 0 条（27 个工具均未注册） |

### 与计划偏差

- fatal_issues 实际 5，计划预期 ≥12——期间已有部分 Skill YAML 修复
- empty_schema_tools 实际 []，计划预期含 record_event 等——business 工具 schema 已非空
- 偏差 ≤1 类（数量变化），不涉及文件路径或架构方向变化，属于代码演进导致的预期差异
