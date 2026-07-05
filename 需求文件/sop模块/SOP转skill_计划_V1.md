# SOP转Skill — AI 执行计划

> **基于需求**：[SOP转skill_需求_V2.md](SOP转skill_需求_V2.md)
> **计划版本**：v1.0
> **目标**：将 SOP 执行从"LLM 自由推理"升级为"Skill 定义驱动 + session_scope 数据边界"

---

## 你的角色

你是 **Emily** 项目开发者。严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：除非计划明确标注"修改方法签名"，否则只能在已有类中新增方法
2. **分层不可跳**：新模块遵守 `API→Core→Session→WorkItem→App→Service→Repo→DB`
3. **sync repo + `asyncio.to_thread`**：Repository 全 sync，async Service 用 `asyncio.to_thread()` 包裹
4. **`emily_core` 不 import AstrBot**：任何新代码不得 import `astrbot.*`
5. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
6. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `SOPIntentRegistry` | `emily-core/emily_core/agent/intent_registry.py` | `load()`, `reload()`, `get_spec()`, `dump_as_text()` | M3 SkillRegistry 参照其 load/reload/原子替换模式 |
| `BusinessFlowToolRegistry` | `emily-core/emily_core/tools/business_flow_tools.py` | `register()`, `get()`, `has()`, `list_names()` | M5 直接调用其 `get()` 获取工具 handler |
| `StepResult` | `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` | dataclass | M5 产出此类型 |
| `ExecutionPlan` / `PlanStep` | `emily-core/emily_core/workitem/pipeline/interfaces/planning.py` | dataclass | M6 node2 路径产出此类型 |
| `SessionContext` | `emily-core/emily_core/session/session_context.py` | `project_ids`, `db_perms`, `has_db_permission()` | M4 source=context 取值来源；M6 session_scope 来源 |
| `KnowledgeInjector` | `emily-core/emily_core/workitem/injector.py` | `get_context_text()`, `analyze()` | M6 适配 Skill instructions 注入 |
| `WorkItemAgent` | `emily-core/emily_core/workitem/workitem_agent.py` | `node2_plan()`, `node3_execute()`, `_real_execute()` | M6 改造入口 |
| `QueryService` | `emily-core/emily_core/services/query_service.py` | `execute()`, `query_events()` 等 | M6 增加 session_scope 过滤 |
| `EmilyCore` | `emily-core/emily_core/__init__.py` | `_ensure_initialized()`, `_build_pipeline_bus()` | M6 挂载 SkillRegistry/SkillExecutor |

### 架构决策

Skill 不持有数据、不声明 datasets。数据边界由 session-context + 工具层自动过滤保障。Skill 定义三段结构（instructions / tools / steps），工具白名单即数据白名单，1:N 工具（query_data）通过 session_scope 参数自动限定查询范围。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| Registry | `emily-core/emily_core/agent/intent_registry.py` | load/reload 原子替换 + `_lock` + `RegistryStatus` dataclass + 索引持久化 |
| Tool | `emily-core/emily_core/tools/business_flow_tools.py` | `BusinessFlowTool` dataclass + `BusinessFlowToolRegistry` dict 注册 |
| Dataclass | `emily-core/emily_core/workitem/pipeline/interfaces/planning.py` | `PlanStep` / `ExecutionPlan` frozen dataclass + `_source` 元数据 |
| Service | `emily-core/emily_core/services/query_service.py` | sync 方法 + 按 query_type 分发 |
| Bootstrap | `emily-core/emily_core/__init__.py` | `_init_phase_b_deps()` 模式：try/except + fail-open + logger |

---

## 模块依赖图

```
M1(SkillSchema) ──→ M2(SkillParser) ──→ M3(SkillRegistry) ──→ M6(Pipeline集成)
                                              │                       ↑
                                              ↓                       │
                                         M4(ParamExtractor) ──→ M5(SkillExecutor)

M7(SOP转Skill转换器) —— 独立离线工具，依赖 M2

M8(11份Skill文件) —— 依赖 M1+M7
```

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `emily-core/emily_core/skill/__init__.py` | 新增 | 模块入口 |
| M1 | `emily-core/emily_core/skill/definition.py` | 新增 | `ParamMapping`, `SkillStep`, `SkillTool`, `SkillDefinition` |
| M1 | `emily-core/emily_core/skill/validator.py` | 新增 | `SkillValidationResult`, `validate_skill()` |
| M1 | `emily-data/schemas/skill_schema.yaml` | 新增 | Skill YAML schema |
| M2 | `emily-core/emily_core/skill/parser.py` | 新增 | `parse_skill_file()`, `parse_skill_text()` |
| M3 | `emily-core/emily_core/skill/registry.py` | 新增 | `SkillRegistry`, `SkillRegistryStatus` |
| M4 | `emily-core/emily_core/skill/param_extractor.py` | 新增 | `ParamExtractor` |
| M4 | `emily-data/prompts/param_extraction.md` | 新增 | LLM 参数提取 prompt |
| M5 | `emily-core/emily_core/skill/executor.py` | 新增 | `SkillExecutor`, `SkillExecutionContext` |
| M6 | `emily-core/emily_core/workitem/workitem_agent.py` | 修改 | `node2_plan()` 增加 Skill 路径, `node3_execute()` 增加 SkillExecutor |
| M6 | `emily-core/emily_core/workitem/injector.py` | 修改 | 新增 `get_skill_instructions()` |
| M6 | `emily-core/emily_core/tools/query_tool.py` | 修改 | `handle_query_data()` 增加 session_scope |
| M6 | `emily-core/emily_core/services/query_service.py` | 修改 | `execute()` 和查询方法增加 session_scope |
| M6 | `emily-core/emily_core/__init__.py` | 修改 | 新增 `_skill_registry`, `_skill_executor`, `_init_skill_module()` |
| M7 | `scripts/sop_to_skill.py` | 新增 | CLI 转换器 |
| M7 | `emily-data/prompts/sop_to_skill.md` | 新增 | LLM 转换 prompt |
| M8 | `emily-data/skills/*.skill.yaml` | 新增 | 11 份 Skill 文件 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `agent/sop_parser.py` | 不变 | — |
| `agent/intent_registry.py` | 不变 | — |
| `workitem/injector.py` | 修改 | 新增 `get_skill_instructions()` 方法 |
| `tools/business_flow_tools.py` | 不变 | — |
| `tools/query_tool.py` | 修改 | `handle_query_data()` 从 `_session_scope` 提取范围，做 db_perms 检查 + project_ids 注入 |
| `services/query_service.py` | 修改 | `execute()` 和各 `query_xxx()` 增加 `session_scope` 参数 |
| `workitem/workitem_agent.py` | 修改 | node2/node3 增加 Skill 路径 |
| `bootstrap.py` | 不变 | Skill 初始化在 `__init__.py` 的 `_init_skill_module()` 中 |
| `__init__.py` | 扩展 | 新增 `_skill_registry` / `_skill_executor` 属性 + `_init_skill_module()` |
| `session/session_agent.py` | 不变 | — |
| `workitem/pipeline/bus.py` | 不变 | — |
| `workitem/pipeline/hook.py` | 不变 | — |
| `session/session_context.py` | 不变 | — |

---

## M1: Skill Schema 定义与校验器

**依赖**：无

**职责**：定义 Skill YAML 三段结构的 dataclass + 校验器。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 模块入口 | `emily-core/emily_core/skill/__init__.py` |
| 2 | Skill 定义 dataclass | `emily-core/emily_core/skill/definition.py` |
| 3 | Schema 校验器 | `emily-core/emily_core/skill/validator.py` |
| 4 | Schema YAML | `emily-data/schemas/skill_schema.yaml` |

### 代码

#### `emily-core/emily_core/skill/__init__.py` — 新建

```python
# emily-core/emily_core/skill/__init__.py
"""Skill 模块 —— SOP 结构化执行定义。"""

from .definition import ParamMapping, SkillStep, SkillTool, SkillDefinition
from .validator import validate_skill, SkillValidationResult

__all__ = [
    "ParamMapping",
    "SkillStep",
    "SkillTool",
    "SkillDefinition",
    "validate_skill",
    "SkillValidationResult",
]
```

#### `emily-core/emily_core/skill/definition.py` — 新建

```python
# emily-core/emily_core/skill/definition.py
"""Skill 定义 dataclass —— 三段结构（instructions / tools / steps）。

设计原则：
  - Skill 不持有数据，不声明 datasets
  - 数据来源 = session-context（source: context）
  - 数据边界 = session-context + 工具层自动过滤
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ParamMapping:
    """参数来源映射 —— 定义 tool_params 中每个参数值如何获取。"""

    source: str          # "user_input" | "prev_step" | "fixed" | "context"
    path: str = ""       # prev_step / context 时的 dot-path
    value: Any = None    # fixed 时的固定值
    extraction: str = "" # user_input 时的 LLM 提取提示
    required: bool = False
    default: Any = None
    enum: list[str] = field(default_factory=list)
    max_length: int = 0


@dataclass(frozen=True)
class SkillStep:
    """Skill 中的单个执行步骤。"""

    id: str
    description: str
    tool_name: str
    tool_params: dict[str, ParamMapping] = field(default_factory=dict)
    output_key: str = ""


@dataclass(frozen=True)
class SkillTool:
    """Skill 中引用的工具声明（白名单）。"""

    name: str
    description: str = ""


@dataclass(frozen=True)
class SkillDefinition:
    """完整 Skill 定义 —— 三段结构。"""

    skill_id: str
    sop_id: str
    version: str
    display_name: str
    instructions: str
    tools: list[SkillTool]
    steps: list[SkillStep]
```

#### `emily-core/emily_core/skill/validator.py` — 新建

```python
# emily-core/emily_core/skill/validator.py
"""Skill 定义校验器。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .definition import SkillDefinition, SkillStep, ParamMapping

_VALID_SOURCES = {"user_input", "prev_step", "fixed", "context"}


@dataclass
class SkillValidationResult:
    """校验结果。"""
    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_skill(skill: SkillDefinition) -> SkillValidationResult:
    """校验 SkillDefinition 的完整性和合法性。"""
    result = SkillValidationResult()

    # ── 必填字段 ──
    if not skill.skill_id:
        result.errors.append("skill_id 不能为空")
    if not skill.sop_id:
        result.errors.append("sop_id 不能为空")
    if not skill.version:
        result.errors.append("version 不能为空")
    if not skill.display_name:
        result.errors.append("display_name 不能为空")

    # ── steps 校验 ──
    if not skill.steps:
        result.errors.append("steps 不能为空——Skill 必须至少有一个步骤")
    else:
        step_ids: set[str] = set()
        for i, step in enumerate(skill.steps):
            if not step.id:
                result.errors.append(f"steps[{i}].id 不能为空")
            if step.id in step_ids:
                result.errors.append(f"steps[{i}].id 重复: {step.id}")
            step_ids.add(step.id)

            if not step.tool_name:
                result.errors.append(f"steps[{i}].tool_name 不能为空")

            # tool_params source 校验
            for pname, mapping in step.tool_params.items():
                if mapping.source not in _VALID_SOURCES:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}.source 非法: {mapping.source}"
                    )
                if mapping.source == "prev_step" and not mapping.path:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=prev_step 时 path 不能为空"
                    )
                if mapping.source == "context" and not mapping.path:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=context 时 path 不能为空"
                    )
                if mapping.source == "fixed" and mapping.value is None:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=fixed 时 value 不能为 None"
                    )
                if mapping.source == "user_input" and not mapping.extraction:
                    result.errors.append(
                        f"steps[{i}].tool_params.{pname}: source=user_input 时 extraction 不能为空"
                    )

    # ── tools 校验 ──
    if not skill.tools:
        result.warnings.append("tools 为空——Skill 未声明任何可用工具")
    else:
        tool_names = {t.name for t in skill.tools}
        # 检查 steps 中的 tool_name 是否在 tools 白名单中
        for i, step in enumerate(skill.steps):
            if step.tool_name and step.tool_name not in tool_names:
                result.warnings.append(
                    f"steps[{i}].tool_name='{step.tool_name}' 不在 tools 白名单中"
                )

    result.is_valid = len(result.errors) == 0
    return result
```

#### `emily-data/schemas/skill_schema.yaml` — 新建

```yaml
# Skill 定义文件 schema —— 三段结构（instructions / tools / steps）
# 用于文档参考和未来 JSON Schema 校验

skill_id:
  type: string
  required: true
  description: "Skill 唯一标识，如 SOP-002-REC-event-record"

sop_id:
  type: string
  required: true
  description: "关联的 SOP 编号，如 SOP-002-REC"

version:
  type: string
  required: true
  description: "Skill 定义版本，如 1.0"

display_name:
  type: string
  required: true
  description: "显示名称，如 事件记录"

instructions:
  type: string
  required: true
  description: "给 AI 的说明（注入 LLM prompt）"

tools:
  type: list
  required: true
  items:
    name:
      type: string
      required: true
    description:
      type: string
      required: false

steps:
  type: list
  required: true
  items:
    id:
      type: string
      required: true
    description:
      type: string
      required: true
    tool_name:
      type: string
      required: true
    tool_params:
      type: dict
      required: false
      items:
        source:
          type: enum
          values: [user_input, prev_step, fixed, context]
          required: true
        path:
          type: string
          required: conditional  # prev_step / context 时必填
        value:
          type: any
          required: conditional  # fixed 时必填
        extraction:
          type: string
          required: conditional  # user_input 时必填
        required:
          type: bool
          default: false
        default:
          type: any
          required: false
        enum:
          type: list
          required: false
        max_length:
          type: int
          required: false
    output_key:
      type: string
      required: false
```

### 模块验收检测

```bash
# 验收 1：dataclass 可导入
uv run python -c "from emily_core.skill.definition import ParamMapping, SkillStep, SkillTool, SkillDefinition; print('IMPORT OK')"

# 验收 2：校验器拒绝空定义
uv run python -c "
from emily_core.skill.validator import validate_skill
from emily_core.skill.definition import SkillDefinition
result = validate_skill(SkillDefinition(skill_id='', sop_id='', version='', display_name='', instructions='', tools=[], steps=[]))
assert not result.is_valid
print(f'REJECTED: {len(result.errors)} errors')
"

# 验收 3：校验器通过合法定义
uv run python -c "
from emily_core.skill.validator import validate_skill
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
skill = SkillDefinition(
    skill_id='SOP-002-REC-event-record', sop_id='SOP-002-REC',
    version='1.0', display_name='事件记录', instructions='执行事件录入',
    tools=[SkillTool(name='record_event', description='录入事件')],
    steps=[SkillStep(id='step-01', description='录入', tool_name='record_event',
                     tool_params={'title': ParamMapping(source='user_input', extraction='事件标题', required=True)},
                     output_key='result')])
result = validate_skill(skill)
assert result.is_valid, result.errors
print('PASS OK')
"

# 验收 4：Schema 文件存在且可解析
uv run python -c "import yaml; d=yaml.safe_load(open('emily-data/schemas/skill_schema.yaml')); assert 'skill_id' in d; print('SCHEMA OK')"
```

---

## M2: Skill Parser（YAML 解析器）

**依赖**：M1

**职责**：读取 Skill YAML 文件，解析为 `SkillDefinition` 对象。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Skill YAML 解析器 | `emily-core/emily_core/skill/parser.py` |

### 代码

#### `emily-core/emily_core/skill/parser.py` — 新建

```python
# emily-core/emily_core/skill/parser.py
"""Skill YAML 解析器 —— 将 .skill.yaml 文件解析为 SkillDefinition。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .definition import ParamMapping, SkillStep, SkillTool, SkillDefinition
from .validator import validate_skill

logger = logging.getLogger("emily.skill.parser")


class SkillParseError(Exception):
    """Skill 解析异常。"""


def _parse_param_mapping(name: str, data: dict) -> ParamMapping:
    """将 dict 解析为 ParamMapping。"""
    if not isinstance(data, dict):
        # 简写：直接是值 → fixed
        return ParamMapping(source="fixed", value=data)

    source = data.get("source", "fixed")
    return ParamMapping(
        source=source,
        path=data.get("path", ""),
        value=data.get("value"),
        extraction=data.get("extraction", ""),
        required=data.get("required", False),
        default=data.get("default"),
        enum=data.get("enum", []),
        max_length=data.get("max_length", 0),
    )


def _parse_step(data: dict) -> SkillStep:
    """将 dict 解析为 SkillStep。"""
    tool_params: dict[str, ParamMapping] = {}
    for pname, pdata in data.get("tool_params", {}).items():
        tool_params[pname] = _parse_param_mapping(pname, pdata)

    return SkillStep(
        id=data.get("id", ""),
        description=data.get("description", ""),
        tool_name=data.get("tool_name", ""),
        tool_params=tool_params,
        output_key=data.get("output_key", ""),
    )


def _parse_tool(data: dict) -> SkillTool:
    """将 dict 解析为 SkillTool。"""
    return SkillTool(
        name=data.get("name", ""),
        description=data.get("description", ""),
    )


def parse_skill_text(text: str, source_name: str = "<text>") -> SkillDefinition:
    """解析 Skill YAML 文本为 SkillDefinition。

    Args:
        text: YAML 文本内容
        source_name: 来源标识（用于错误提示）

    Returns:
        SkillDefinition

    Raises:
        SkillParseError: YAML 格式错误或校验失败
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SkillParseError(f"YAML 解析失败 ({source_name}): {e}") from e

    if not isinstance(data, dict):
        raise SkillParseError(f"Skill 文件根节点必须是 dict ({source_name})")

    # 解析 tools
    tools = [_parse_tool(t) for t in data.get("tools", [])]

    # 解析 steps
    steps = [_parse_step(s) for s in data.get("steps", [])]

    # 构建 SkillDefinition
    skill = SkillDefinition(
        skill_id=data.get("skill_id", ""),
        sop_id=data.get("sop_id", ""),
        version=str(data.get("version", "")),
        display_name=data.get("display_name", ""),
        instructions=data.get("instructions", ""),
        tools=tools,
        steps=steps,
    )

    # 校验
    result = validate_skill(skill)
    if not result.is_valid:
        raise SkillParseError(
            f"Skill 校验失败 ({source_name}): {'; '.join(result.errors)}"
        )

    if result.warnings:
        for w in result.warnings:
            logger.warning("Skill %s 校验警告: %s", source_name, w)

    return skill


def parse_skill_file(path: Path) -> SkillDefinition:
    """读取 Skill YAML 文件并解析。"""
    path = Path(path)
    if not path.exists():
        raise SkillParseError(f"文件不存在: {path}")

    text = path.read_text(encoding="utf-8")
    return parse_skill_text(text, source_name=path.name)
```

### 模块验收检测

```bash
# 验收 1：解析合法 YAML
uv run python -c "
from emily_core.skill.parser import parse_skill_text
yaml_text = '''
skill_id: test-skill
sop_id: SOP-002-REC
version: \"1.0\"
display_name: 测试
instructions: 测试指令
tools:
  - name: record_event
    description: 录入
steps:
  - id: step-01
    description: 测试步骤
    tool_name: record_event
    tool_params:
      title:
        source: user_input
        extraction: 标题
        required: true
    output_key: result
'''
skill = parse_skill_text(yaml_text, 'test')
assert skill.skill_id == 'test-skill'
assert len(skill.steps) == 1
assert skill.steps[0].tool_params['title'].source == 'user_input'
print('PARSE OK')
"

# 验收 2：非法 YAML 抛异常
uv run python -c "
from emily_core.skill.parser import parse_skill_text, SkillParseError
try:
    parse_skill_text('invalid: {broken', 'bad')
    assert False, 'should raise'
except SkillParseError:
    print('REJECT OK')
"

# 验收 3：空 steps 抛异常
uv run python -c "
from emily_core.skill.parser import parse_skill_text, SkillParseError
try:
    parse_skill_text('skill_id: x\nsop_id: x\nversion: \"1\"\ndisplay_name: x\ninstructions: x\ntools: []\nsteps: []', 'bad')
    assert False, 'should raise'
except SkillParseError:
    print('EMPTY STEPS REJECT OK')
"
```

---

## M3: Skill Registry（注册表 + 热加载）

**依赖**：M1, M2

**职责**：扫描 `emily-data/skills/` 目录，加载全部 Skill 文件，提供按 sop_id 查询。参照 `SOPIntentRegistry` 的 load/reload/原子替换模式。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SkillRegistry | `emily-core/emily_core/skill/registry.py` |

### 代码

#### `emily-core/emily_core/skill/registry.py` — 新建

```python
# emily-core/emily_core/skill/registry.py
"""SkillRegistry —— Skill 注册表（load/reload/查询）。

参照 SOPIntentRegistry 的 load/reload/原子替换模式。
扫描 emily-data/skills/ 目录，解析全部 .skill.yaml 文件。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .definition import SkillDefinition
from .parser import parse_skill_file, SkillParseError

logger = logging.getLogger("emily.skill.registry")


@dataclass
class SkillRegistryStatus:
    """SkillRegistry 运行状态快照。"""
    total_files: int = 0
    successfully_parsed: int = 0
    failed_parsed: int = 0
    failed_files: list[str] = field(default_factory=list)
    is_ready: bool = False
    last_reload_at: str = ""


class SkillRegistry:
    """Skill 注册表 —— 扫描目录 + 按 sop_id 查询 + 热重载。"""

    _SKILL_FILE_PATTERN = "*.skill.yaml"

    def __init__(self, skill_directory: str):
        self.skill_directory = Path(skill_directory)
        self._lock = threading.RLock()
        self._registry: dict[str, SkillDefinition] = {}   # sop_id → SkillDefinition
        self._by_skill_id: dict[str, SkillDefinition] = {}  # skill_id → SkillDefinition
        self._is_ready: bool = False

    # ── 加载 / 重载 ──

    def load(self) -> SkillRegistryStatus:
        """首次加载。"""
        with self._lock:
            new_registry, new_by_skill_id, status = self._scan_and_parse()
            self._registry = new_registry
            self._by_skill_id = new_by_skill_id
            self._is_ready = status.successfully_parsed > 0
            logger.info(
                "SkillRegistry loaded: %d skills, %d ok, %d failed",
                status.total_files, status.successfully_parsed, status.failed_parsed,
            )
            return status

    def reload(self) -> SkillRegistryStatus:
        """热重载（原子替换）。"""
        with self._lock:
            new_registry, new_by_skill_id, status = self._scan_and_parse()
            if status.successfully_parsed == 0 and len(self._registry) > 0:
                logger.warning("SkillRegistry reload: all failed, keeping old registry")
                return self._get_status()
            self._registry = new_registry
            self._by_skill_id = new_by_skill_id
            self._is_ready = status.successfully_parsed > 0
            logger.info(
                "SkillRegistry reloaded: %d ok, %d failed",
                status.successfully_parsed, status.failed_parsed,
            )
            return status

    # ── 查询 ──

    def get_by_sop_id(self, sop_id: str) -> SkillDefinition | None:
        """按 SOP 编号查询 Skill。"""
        with self._lock:
            return self._registry.get(sop_id)

    def get_by_skill_id(self, skill_id: str) -> SkillDefinition | None:
        """按 Skill ID 查询。"""
        with self._lock:
            return self._by_skill_id.get(skill_id)

    def has_skill(self, sop_id: str) -> bool:
        """判断某 SOP 是否有对应 Skill。"""
        with self._lock:
            return sop_id in self._registry

    def list_sop_ids(self) -> list[str]:
        """列出所有已注册 Skill 对应的 sop_id。"""
        with self._lock:
            return sorted(self._registry.keys())

    # ── 内部 ──

    def _scan_and_parse(self) -> tuple[dict[str, SkillDefinition], dict[str, SkillDefinition], SkillRegistryStatus]:
        """扫描目录 + 解析。"""
        new_registry: dict[str, SkillDefinition] = {}
        new_by_skill_id: dict[str, SkillDefinition] = {}
        now = datetime.now(timezone.utc).isoformat()

        if not self.skill_directory.exists():
            logger.warning("Skill directory not found: %s", self.skill_directory)
            status = SkillRegistryStatus(last_reload_at=now)
            return new_registry, new_by_skill_id, status

        skill_files = sorted(self.skill_directory.glob(self._SKILL_FILE_PATTERN))
        ok_count = 0
        failed_count = 0
        failed_files: list[str] = []

        for file_path in skill_files:
            try:
                skill = parse_skill_file(file_path)
                new_registry[skill.sop_id] = skill
                new_by_skill_id[skill.skill_id] = skill
                ok_count += 1
            except (SkillParseError, Exception) as e:
                failed_count += 1
                failed_files.append(file_path.name)
                logger.error("Skill parse failed: %s — %s", file_path.name, e)

        status = SkillRegistryStatus(
            total_files=ok_count + failed_count,
            successfully_parsed=ok_count,
            failed_parsed=failed_count,
            failed_files=failed_files,
            is_ready=ok_count > 0,
            last_reload_at=now,
        )
        return new_registry, new_by_skill_id, status

    def _get_status(self) -> SkillRegistryStatus:
        """返回当前状态快照。"""
        with self._lock:
            ok = sum(1 for _ in self._registry.values())
            return SkillRegistryStatus(
                total_files=ok,
                successfully_parsed=ok,
                is_ready=self._is_ready,
            )
```

### 模块验收检测

```bash
# 验收 1：空目录加载不报错
uv run python -c "
from emily_core.skill.registry import SkillRegistry
import tempfile, os
reg = SkillRegistry(skill_directory=tempfile.mkdtemp())
status = reg.load()
assert status.total_files == 0
print('EMPTY DIR OK')
"

# 验收 2：有文件时加载成功（需先手动创建一个测试 Skill 文件）
uv run python -c "
import tempfile, os
from pathlib import Path
from emily_core.skill.registry import SkillRegistry

tmpdir = tempfile.mkdtemp()
skill_text = '''
skill_id: test-skill
sop_id: SOP-002-REC
version: \"1.0\"
display_name: 测试
instructions: 测试
tools:
  - name: record_event
    description: 录入
steps:
  - id: step-01
    description: 录入
    tool_name: record_event
    tool_params:
      title: {source: fixed, value: 测试标题}
    output_key: result
'''
Path(tmpdir, 'test.skill.yaml').write_text(skill_text, encoding='utf-8')

reg = SkillRegistry(skill_directory=tmpdir)
status = reg.load()
assert status.successfully_parsed == 1
assert reg.has_skill('SOP-002-REC')
skill = reg.get_by_sop_id('SOP-002-REC')
assert skill.skill_id == 'test-skill'
print('LOAD OK')
"

# 验收 3：reload 原子替换
uv run python -c "
from emily_core.skill.registry import SkillRegistry
import tempfile
tmpdir = tempfile.mkdtemp()
reg = SkillRegistry(skill_directory=tmpdir)
reg.load()
status = reg.reload()
print('RELOAD OK')
"
```

---

## M4: Parameter Extractor（参数提取引擎）

**依赖**：M1

**职责**：根据 `ParamMapping` 从四种 source 解析参数值。`source=user_input` 时调用 LLM。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ParamExtractor | `emily-core/emily_core/skill/param_extractor.py` |
| 2 | 提取 prompt | `emily-data/prompts/param_extraction.md` |

### 代码

#### `emily-core/emily_core/skill/param_extractor.py` — 新建

```python
# emily-core/emily_core/skill/param_extractor.py
"""Parameter Extractor —— 从四种 source 解析参数值。

source 详解：
  user_input → 调 LLM chat_json 提取
  prev_step  → 从前步结果按 dot-path 取值
  fixed      → 使用固定值（today/now 特殊处理）
  context    → 从 session-context 字段取值
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .definition import ParamMapping

logger = logging.getLogger("emily.skill.param_extractor")


def _resolve_dot_path(data: dict, path: str) -> Any:
    """按 dot-path 从嵌套 dict 取值。如 'project_info.object_id' → data['project_info']['object_id']"""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
        if current is None:
            return None
    return current


class ParamExtractor:
    """参数提取引擎。"""

    def __init__(self, llm_client=None):
        self._llm = llm_client

    async def resolve_params(
        self,
        tool_params_mapping: dict[str, ParamMapping],
        user_input: str,
        session_context: dict,
        step_results: dict[str, dict],
    ) -> dict[str, Any]:
        """解析一步的全部参数。"""
        resolved: dict[str, Any] = {}

        for pname, mapping in tool_params_mapping.items():
            value = await self._resolve_one(mapping, user_input, session_context, step_results)
            if value is None:
                if mapping.required:
                    raise ValueError(
                        f"必填参数 '{pname}' 提取失败 (source={mapping.source})"
                    )
                value = mapping.default
            # enum 约束
            if mapping.enum and value is not None and value not in mapping.enum:
                logger.warning("参数 '%s' 值 '%s' 不在枚举 %s 中，使用 default=%s",
                               pname, value, mapping.enum, mapping.default)
                value = mapping.default
            # max_length 约束
            if mapping.max_length and isinstance(value, str) and len(value) > mapping.max_length:
                value = value[:mapping.max_length]

            if value is not None:
                resolved[pname] = value

        return resolved

    async def _resolve_one(
        self,
        mapping: ParamMapping,
        user_input: str,
        session_context: dict,
        step_results: dict[str, dict],
    ) -> Any:
        """解析单个参数。"""
        source = mapping.source

        if source == "fixed":
            return self._extract_from_fixed(mapping)
        elif source == "context":
            return self._extract_from_context(mapping, session_context)
        elif source == "prev_step":
            return self._extract_from_prev_step(mapping, step_results)
        elif source == "user_input":
            return await self._extract_from_user_input(mapping, user_input)
        else:
            logger.warning("未知 source: %s", source)
            return None

    def _extract_from_fixed(self, mapping: ParamMapping) -> Any:
        """解析固定值。today → 当前日期，now → 当前时间戳。"""
        value = mapping.value
        if isinstance(value, str):
            if value == "today":
                return datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if value == "now":
                return datetime.now(timezone.utc).isoformat()
        return value

    def _extract_from_context(self, mapping: ParamMapping, session_context: dict) -> Any:
        """从 session-context 取值。"""
        return _resolve_dot_path(session_context, mapping.path)

    def _extract_from_prev_step(self, mapping: ParamMapping, step_results: dict[str, dict]) -> Any:
        """从前步结果取值。"""
        return _resolve_dot_path(step_results, mapping.path)

    async def _extract_from_user_input(self, mapping: ParamMapping, user_input: str) -> Any:
        """调 LLM 从用户消息中提取值。"""
        if self._llm is None:
            logger.warning("LLM 不可用，无法提取 user_input 参数: %s", mapping.extraction)
            return mapping.default

        try:
            from ..infrastructure.llm.prompt_loader import load_prompt
            prompt_template = load_prompt("param_extraction")
        except Exception:
            prompt_template = (
                "请从用户消息中提取指定字段的值。\n\n"
                "字段：{extraction}\n"
                "约束：{constraints}\n\n"
                "用户消息：\n{user_input}\n\n"
                '仅输出 JSON：{{"value": "提取的值"}}\n'
                '若无法提取且非必填，输出：{{"value": null}}'
            )

        constraints_parts = []
        if mapping.required:
            constraints_parts.append("required=true")
        if mapping.max_length:
            constraints_parts.append(f"max_length={mapping.max_length}")
        if mapping.enum:
            constraints_parts.append(f"enum={mapping.enum}")

        prompt = prompt_template.format(
            extraction=mapping.extraction,
            constraints=", ".join(constraints_parts) if constraints_parts else "无特殊约束",
            user_input=user_input[:1000],
        )

        try:
            result = await self._llm.chat_json(prompt)
            data = result.get("data", {})
            value = data.get("value")
            return value
        except Exception as e:
            logger.warning("LLM 参数提取失败: %s — %s", mapping.extraction, e)
            return mapping.default
```

#### `emily-data/prompts/param_extraction.md` — 新建

```markdown
请从用户消息中提取指定字段的值。

字段：{extraction}
约束：{constraints}

用户消息：
{user_input}

仅输出 JSON：{{"value": "提取的值"}}
若无法提取且非必填，输出：{{"value": null}}
```

### 模块验收检测

```bash
# 验收 1：fixed source
uv run python -c "
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
m = ParamMapping(source='fixed', value='today')
val = ext._extract_from_fixed(m)
assert val  # today → 当前日期
print(f'FIXED OK: {val}')
"

# 验收 2：prev_step source
uv run python -c "
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
m = ParamMapping(source='prev_step', path='project_info.object_id')
prev = {'project_info': {'object_id': 'uuid-123'}}
val = ext._extract_from_prev_step(m, prev)
assert val == 'uuid-123'
print('PREV_STEP OK')
"

# 验收 3：context source
uv run python -c "
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
m = ParamMapping(source='context', path='user_id')
val = ext._extract_from_context(m, {'user_id': 'user-456', 'project_name': 'ECOCITY'})
assert val == 'user-456'
print('CONTEXT OK')
"

# 验收 4：resolve_params 整体（sync 包裹）
uv run python -c "
import asyncio
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
mappings = {
    'title': ParamMapping(source='fixed', value='测试标题'),
    'project_id': ParamMapping(source='context', path='project_ids.0'),
}
ctx = {'project_ids': ['uuid-001', 'uuid-002']}
result = asyncio.run(ext.resolve_params(mappings, '测试消息', ctx, {}))
assert result['title'] == '测试标题'
assert result['project_id'] == 'uuid-001'
print(f'RESOLVE OK: {result}')
"
```

---

## M5: Skill Executor（执行引擎）

**依赖**：M1, M4

**职责**：接收 SkillDefinition + 运行时上下文，线性执行步骤序列，产出 StepResult 列表。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SkillExecutionContext + SkillExecutor | `emily-core/emily_core/skill/executor.py` |

### 代码

#### `emily-core/emily_core/skill/executor.py` — 新建

```python
# emily-core/emily_core/skill/executor.py
"""SkillExecutor —— 线性执行 Skill 步骤序列。

核心流程：
  对每步 → 校验 tool_name 在白名单 → ParamExtractor.resolve_params() →
  注入 session_scope → 调 BusinessFlowTool.handler() → 存 step_results → 下一步
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

from .definition import SkillDefinition
from .param_extractor import ParamExtractor
from ..workitem.pipeline.interfaces.execution import StepResult, ToolCallRecord, DbResult
from ..tools.business_flow_tools import BusinessFlowToolRegistry

logger = logging.getLogger("emily.skill.executor")


@dataclass
class SkillExecutionContext:
    """Skill 执行上下文。"""

    skill: SkillDefinition
    user_input: str
    user_id: str
    message_id: str
    conversation_id: str
    session_context: dict          # 从 SessionContext 扁平化
    step_results: dict[str, dict]  # output_key → 前步 business_data
    business_flow_tools: BusinessFlowToolRegistry
    llm_client: Any = None         # ParamExtractor 用


class SkillExecutor:
    """Skill 执行引擎。"""

    def __init__(self):
        self._param_extractor: ParamExtractor | None = None

    async def execute(self, ctx: SkillExecutionContext) -> list[StepResult]:
        """线性执行 Skill 步骤序列。"""
        results: list[StepResult] = []
        self._param_extractor = ParamExtractor(llm_client=ctx.llm_client)

        # 构建 tool 白名单集合
        allowed_tools = {t.name for t in ctx.skill.tools}

        for step in ctx.skill.steps:
            t_start = _time.monotonic()

            # 1. 工具白名单校验
            if step.tool_name not in allowed_tools:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 不在 Skill 工具白名单中",
                ))
                break  # 失败即停止

            # 2. 获取工具
            tool = ctx.business_flow_tools.get(step.tool_name)
            if tool is None:
                results.append(StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"工具 '{step.tool_name}' 未在 BusinessFlowToolRegistry 中注册",
                ))
                break

            try:
                # 3. 解析参数
                tool_params = await self._param_extractor.resolve_params(
                    step.tool_params, ctx.user_input, ctx.session_context, ctx.step_results,
                )

                # 4. 注入运行时上下文 + session_scope
                tool_params["_user_id"] = ctx.user_id
                tool_params["_message_id"] = ctx.message_id
                tool_params["_conversation_id"] = ctx.conversation_id
                tool_params["_session_scope"] = self._build_session_scope(ctx.session_context)

                # 5. 调用工具 handler
                import inspect
                sig = inspect.signature(tool.handler)
                handler_kwargs = {"params": tool_params}
                if "user_id" in sig.parameters:
                    handler_kwargs["user_id"] = ctx.user_id
                if "message_id" in sig.parameters:
                    handler_kwargs["message_id"] = ctx.message_id

                handler_result = await tool.handler(**handler_kwargs)
                handler_dict = handler_result if isinstance(handler_result, dict) else {}

                # 6. 构建 StepResult
                elapsed_ms = int((_time.monotonic() - t_start) * 1000)
                tool_call = ToolCallRecord(
                    tool_name=step.tool_name,
                    tool_input=tool_params,
                    tool_output=handler_dict,
                    success=handler_dict.get("success", True),
                    elapsed_ms=elapsed_ms,
                )

                db_results = []
                object_id = handler_dict.get("object_id", "")
                if object_id:
                    db_results.append(DbResult(
                        operation="insert",
                        table=step.tool_name.replace("record_", "") + "s",
                        affected_rows=1,
                        result_data=handler_dict,
                    ))

                output = handler_dict.get("reply", step.description)
                success = handler_dict.get("success", True)

                sr = StepResult(
                    step_id=step.id,
                    success=success,
                    output=str(output),
                    tool_calls=[tool_call],
                    db_results=db_results,
                    business_data=handler_dict,
                )

                # 7. 存储 output_key → business_data
                if step.output_key and handler_dict:
                    ctx.step_results[step.output_key] = handler_dict

            except Exception as e:
                logger.error("Step %s failed: %s", step.id, e, exc_info=True)
                sr = StepResult(
                    step_id=step.id,
                    success=False,
                    output=f"步骤执行异常: {e}",
                )

            results.append(sr)
            if not sr.success:
                break

        return results

    @staticmethod
    def _build_session_scope(session_context: dict) -> dict:
        """从 session_context 提取数据范围字段。"""
        return {
            "project_ids": session_context.get("project_ids", []),
            "db_perms": session_context.get("db_perms", {}),
            "info_level": session_context.get("info_level", "public"),
            "company_type": session_context.get("company_type", ""),
            "department": session_context.get("department", ""),
        }
```

### 模块验收检测

```bash
# 验收 1：线性执行 mock 工具
uv run python -c "
import asyncio
from emily_core.skill.executor import SkillExecutor, SkillExecutionContext
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool

reg = BusinessFlowToolRegistry()
async def mock_handler(params, **kw): return {'success': True, 'reply': 'ok', 'object_id': 'mock-123'}
reg.register(BusinessFlowTool(name='record_event', description='mock', parameters={}, handler=mock_handler))

skill = SkillDefinition(
    skill_id='test', sop_id='SOP-002-REC', version='1.0', display_name='测试',
    instructions='', tools=[SkillTool(name='record_event', description='mock')],
    steps=[SkillStep(id='s1', description='录入', tool_name='record_event',
                     tool_params={'title': ParamMapping(source='fixed', value='测试标题')},
                     output_key='r1')])
ctx = SkillExecutionContext(skill=skill, user_input='测试', user_id='', message_id='',
                            conversation_id='', session_context={'project_ids': [], 'db_perms': {}},
                            step_results={}, business_flow_tools=reg, llm_client=None)
results = asyncio.run(SkillExecutor().execute(ctx))
assert len(results) == 1
assert results[0].success
print(f'EXECUTE OK: {results[0].output}')
"

# 验收 2：白名单拒绝
uv run python -c "
import asyncio
from emily_core.skill.executor import SkillExecutor, SkillExecutionContext
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool

reg = BusinessFlowToolRegistry()
async def mock_handler(params, **kw): return {'success': True, 'reply': 'ok'}
reg.register(BusinessFlowTool(name='query_data', description='mock', parameters={}, handler=mock_handler))

skill = SkillDefinition(
    skill_id='test', sop_id='SOP-002-REC', version='1.0', display_name='测试',
    instructions='', tools=[SkillTool(name='record_event', description='录入')],
    steps=[SkillStep(id='s1', description='查询', tool_name='query_data',
                     tool_params={'query_type': ParamMapping(source='fixed', value='project')},
                     output_key='r1')])
ctx = SkillExecutionContext(skill=skill, user_input='测试', user_id='', message_id='',
                            conversation_id='', session_context={},
                            step_results={}, business_flow_tools=reg, llm_client=None)
results = asyncio.run(SkillExecutor().execute(ctx))
assert not results[0].success
assert '白名单' in results[0].output
print('WHITELIST REJECT OK')
"

# 验收 3：session_scope 注入
uv run python -c "
import asyncio
from emily_core.skill.executor import SkillExecutor, SkillExecutionContext
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool

reg = BusinessFlowToolRegistry()
captured = {}
async def capture_handler(params, **kw):
    captured.update(params)
    return {'success': True, 'reply': 'ok'}
reg.register(BusinessFlowTool(name='record_event', description='mock', parameters={}, handler=capture_handler))

skill = SkillDefinition(
    skill_id='test', sop_id='SOP-002-REC', version='1.0', display_name='测试',
    instructions='', tools=[SkillTool(name='record_event', description='mock')],
    steps=[SkillStep(id='s1', description='录入', tool_name='record_event',
                     tool_params={'title': ParamMapping(source='fixed', value='t')},
                     output_key='r1')])
ctx = SkillExecutionContext(skill=skill, user_input='', user_id='uid', message_id='mid',
                            conversation_id='cid',
                            session_context={'project_ids': ['p1'], 'db_perms': {'events': 'write'}},
                            step_results={}, business_flow_tools=reg, llm_client=None)
asyncio.run(SkillExecutor().execute(ctx))
assert '_session_scope' in captured
assert captured['_session_scope']['project_ids'] == ['p1']
print('SESSION_SCOPE OK')
"
```

---

## M6: PipelineBUS 集成 + session_scope 支持

**依赖**：M1, M2, M3, M4, M5

**职责**：改造 node2/node3、注入 Skill 模块、改造 query_data 支持 session_scope。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | EmilyCore 挂载 | `emily-core/emily_core/__init__.py` 修改 |
| 2 | KnowledgeInjector 适配 | `emily-core/emily_core/workitem/injector.py` 修改 |
| 3 | WorkItemAgent 改造 | `emily-core/emily_core/workitem/workitem_agent.py` 修改 |
| 4 | query_tool 改造 | `emily-core/emily_core/tools/query_tool.py` 修改 |
| 5 | query_service 改造 | `emily-core/emily_core/services/query_service.py` 修改 |

### 操作

#### 操作 1：`emily-core/emily_core/__init__.py` — 在 `EmilyCore.__init__` 新增属性

在 `self._query_service = None` 行后追加：

```python
        # Skill 模块
        self._skill_registry = None
        self._skill_executor = None
```

在 `_ensure_initialized()` 方法中，`_build_pipeline_bus()` 调用之前，追加：

```python
        # ── Skill 模块 ──
        self._init_skill_module()
```

在 `EmilyCore` 类中新增方法 `_init_skill_module`（放在 `_init_email_module` 之后）：

```python
    def _init_skill_module(self) -> None:
        """初始化 Skill 模块：Registry + Executor。fail-open。"""
        try:
            from .skill.registry import SkillRegistry
            from .skill.executor import SkillExecutor

            # 多级 fallback: 容器内 > 环境变量 > 宿主机开发路径
            skill_dir = "/app/skills"
            if not Path(skill_dir).exists():
                skill_dir = getattr(self.config, "skill_directory", "") or ""
            if not skill_dir or not Path(skill_dir).exists():
                dev_dir = str(Path(__file__).resolve().parents[2] / "emily-data" / "skills")
                if Path(dev_dir).exists():
                    skill_dir = dev_dir

            self._skill_registry = SkillRegistry(skill_directory=skill_dir)
            status = self._skill_registry.load()
            self._skill_executor = SkillExecutor()
            logger.info("Skill module initialized: %s — dir=%s", status, skill_dir)
        except Exception as e:
            logger.warning("Skill module init failed: %s", e)
            self._skill_registry = None
            self._skill_executor = None
```

在 `_build_pipeline_bus()` 方法中，`WorkItemAgent(...)` 构造调用中新增参数：

```python
        self._workitem_agent = WorkItemAgent(
            injector=injector,
            llm_client=self._llm_client,
            config=self.config,
            business_flow_tools=self._business_flow_tools,
            sop_intent_registry=self._sop_intent_registry,
            rag_provider=self._rag_provider,
            permission_engine=self._permission_auth_engine,
            # Skill 模块
            skill_registry=self._skill_registry,
            skill_executor=self._skill_executor,
        )
```

在 `_collect_injected_services()` 方法中追加：

```python
        if self._skill_registry is not None:
            injected["skill_registry"] = self._skill_registry
```

#### 操作 2：`emily-core/emily_core/workitem/injector.py` — 新增 `get_skill_instructions`

在 `KnowledgeInjector` 类中新增方法：

```python
    def get_skill_instructions(self, sop_id: str) -> str | None:
        """获取 Skill 的 instructions 段（若存在）。"""
        if self._sop_intent_registry is None:
            return None
        # 通过 SkillRegistry 查询（由外部注入）
        return None  # SkillRegistry 由 WorkItemAgent 直接访问，此处仅保留接口
```

在 `get_context_text()` 方法开头追加 Skill instructions 优先逻辑：

```python
    def get_context_text(self) -> str:
        """构建注入的上下文字符串，供 WorkItemAgent 在构造 LLM prompt 时使用。"""
        parts = []
        if self._sop_texts:
            for sop_id, text in self._sop_texts.items():
                parts.append(f"--- SOP: {sop_id} ---\n{text}")
        if self._table_schemas:
            parts.append("--- 可用数据表 ---")
            for name, schema in self._table_schemas.items():
                parts.append(f"- {name}: {schema}")
        return "\n\n".join(parts)
```

> 注：`get_context_text()` 当前逻辑无需改动。Skill instructions 在 node2 中的注入方式由 WorkItemAgent 直接从 `self._skill_registry.get_by_sop_id(sop_id).instructions` 获取，不经过 KnowledgeInjector。

#### 操作 3：`emily-core/emily_core/workitem/workitem_agent.py` — 改造 node2/node3

在 `WorkItemAgent.__init__()` 新增参数和属性：

```python
    def __init__(
        self,
        injector=None,
        llm_client=None,
        config=None,
        business_flow_tools=None,
        sop_intent_registry=None,
        rag_provider=None,
        permission_engine=None,
        # Skill 模块
        skill_registry=None,
        skill_executor=None,
    ):
        ...
        # Skill 模块
        self._skill_registry = skill_registry
        self._skill_executor = skill_executor
```

改造 `node2_plan` 方法，在 `planner_mode = self._resolve_mode("planner")` 之前插入 Skill 路径：

```python
    async def node2_plan(self, context: BusContext) -> None:
        """Node 2 [计划+标准] —— Skill 定义优先，否则 LLM 规划 或 MockPlanner fallback。"""
        wi = context.work_item

        # ── Skill 路径优先 ──
        if self._skill_registry and self._skill_registry.has_skill(wi.sop_id or ""):
            skill = self._skill_registry.get_by_sop_id(wi.sop_id)
            plan = self._skill_to_execution_plan(skill)
            plan._source = "skill_definition"
            wi.execution_plan = plan
            wi.risk_level = plan.risk_level
            wi.acceptance_criteria = list(getattr(plan, "acceptance_criteria", []))
            wi.llm_call_count += 1
            logger.info("WI %s node2: using Skill definition (sop=%s)", wi.id, wi.sop_id)
            return

        # ── 原有 LLM/Mock 规划路径 ──
        planner_mode = self._resolve_mode("planner")
        ...
```

在 `WorkItemAgent` 类中新增方法 `_skill_to_execution_plan`：

```python
    @staticmethod
    def _skill_to_execution_plan(skill) -> ExecutionPlan:
        """将 SkillDefinition 转换为 ExecutionPlan。"""
        from ..workitem.pipeline.interfaces.planning import PlanStep, ExecutionPlan

        steps = [
            PlanStep(
                step_id=s.id,
                description=s.description,
                tool_name=s.tool_name,
                tool_params={},  # 由 SkillExecutor 在执行时从 ParamMapping 解析
                expected_output="",
                depends_on=[],
            )
            for s in skill.steps
        ]

        return ExecutionPlan(
            risk_level="L2",
            steps=steps,
            acceptance_criteria=[],
            estimated_steps=len(steps),
            _source="skill_definition",
        )
```

改造 `node3_execute` 方法，在 `if wi.execution_plan is None:` 之后插入 Skill 路径：

```python
    async def node3_execute(self, context: BusContext) -> None:
        """Node 3 [执行+验收] —— Skill 路径优先，否则走原有 _real_execute。"""
        wi = context.work_item
        if wi.execution_plan is None:
            return

        # ── Skill 路径 ──
        if getattr(wi.execution_plan, "_source", "") == "skill_definition" and self._skill_executor:
            step_results = await self._execute_skill(wi, context)
        else:
            # ── 原有路径 ──
            executor_mode = self._resolve_mode("executor")
            if executor_mode == "real":
                step_results = await self._real_execute(wi.execution_plan, context)
            else:
                step_results = await self._work_agent.execute(wi.execution_plan, context)

        ...  # Guardian 逻辑不变
```

在 `WorkItemAgent` 类中新增方法 `_execute_skill`：

```python
    async def _execute_skill(self, wi, context: BusContext) -> list[StepResult]:
        """通过 SkillExecutor 执行 Skill 定义。"""
        from ..skill.executor import SkillExecutionContext

        skill = self._skill_registry.get_by_sop_id(wi.sop_id)
        if skill is None:
            logger.error("Skill not found for sop_id=%s, falling back to _real_execute", wi.sop_id)
            return await self._real_execute(wi.execution_plan, context)

        # 从 BusContext 获取 session_context
        session_ctx = context.get_session_context() if context else None
        session_context_dict = {}
        if session_ctx:
            session_context_dict = {
                "user_id": getattr(session_ctx, "user_id", ""),
                "user_name": getattr(session_ctx, "user_name", ""),
                "project_ids": list(getattr(session_ctx, "project_ids", [])),
                "project_name": getattr(session_ctx, "project_name", ""),
                "db_perms": dict(getattr(session_ctx, "db_perms", {})),
                "info_level": getattr(session_ctx, "info_level", "public"),
                "company_type": getattr(session_ctx, "company_type", ""),
                "department": getattr(session_ctx, "department", ""),
            }

        skill_ctx = SkillExecutionContext(
            skill=skill,
            user_input=wi.user_input,
            user_id=context.user_id if hasattr(context, "user_id") else "",
            message_id=context.db_message_id if hasattr(context, "db_message_id") else "",
            conversation_id=context.message.conversation_id if context.message else "",
            session_context=session_context_dict,
            step_results={},
            business_flow_tools=self._business_flow_tools,
            llm_client=self._llm,
        )

        return await self._skill_executor.execute(skill_ctx)
```

#### 操作 4：`emily-core/emily_core/tools/query_tool.py` — 增加 session_scope

在 `handle_query_data` 函数开头追加 session_scope 逻辑：

```python
_QUERY_TYPE_TO_TABLE = {
    "event": "events", "task": "tasks", "meeting": "meetings",
    "file": "files", "message": "messages", "conversation": "conversations",
    "user": "users", "project": "projects", "journal": "events",
    "summary": "summary",
}


async def handle_query_data(
    params: dict,
    query_service: QueryService,
) -> dict:
    """处理数据查询（M14 业务流工具 handler + session_scope 过滤）。"""
    try:
        # ── session_scope 数据边界 ──
        session_scope = params.pop("_session_scope", None) or {}

        # 1. db_perms 检查
        query_type = params.get("query_type", "event")
        target_table = _QUERY_TYPE_TO_TABLE.get(query_type, "")
        db_perms = session_scope.get("db_perms", {})
        if db_perms and target_table and target_table not in db_perms:
            logger.info("query_data: db_perms denied table=%s for query_type=%s", target_table, query_type)
            return {"success": False, "reply": f"无权限查询{target_table}", "total": 0}

        # 2. project_ids 自动注入
        project_ids = session_scope.get("project_ids", [])
        if project_ids and not params.get("project_id"):
            params["project_ids"] = project_ids

        # ── 原有逻辑 ──
        cmd = QueryCommand(
            ...
        )
        ...
```

> 注意：`_session_scope` 用 `params.pop()` 而非 `params.get()` —— 从参数中移除，不传给 QueryCommand。

#### 操作 5：`emily-core/emily_core/services/query_service.py` — 增加 project_ids 范围过滤

在 `QueryCommand` dataclass（位于 `adapters/standard/command.py`）中新增可选字段：

```python
    project_ids: list[str] | None = None   # session_scope 注入的项目范围
```

在 `QueryService.execute()` 方法中，`if qt == "event":` 等分支调用处，传递 `project_ids`：

```python
        if qt == "event":
            items = self.query_events(
                project_id=cmd.project_id,
                project_ids=cmd.project_ids,  # 新增
                time_range=cmd.time_range,
                status=cmd.status_filter,
                limit=cmd.limit,
            )
```

在各 `query_xxx` 方法中增加 `project_ids` 参数，生成 SQL 时追加 `WHERE project_id IN (...)` 过滤。以 `query_events` 为例：

```python
    def query_events(
        self,
        project_id: str | None = None,
        project_ids: list[str] | None = None,  # 新增
        time_range: str = "all",
        status: str | None = None,
        limit: int = 50,
    ):
        """查询事件。支持 session_scope 的 project_ids 范围过滤。"""
        return self.event_repo.query_events(
            project_id=project_id,
            project_ids=project_ids,
            time_range=time_range,
            status=status,
            limit=limit,
        )
```

对应 `EventRepository.query_events()` 方法追加 `project_ids` 参数和 SQL 过滤：

```python
    @staticmethod
    def query_events(
        project_id: str | None = None,
        project_ids: list[str] | None = None,
        time_range: str = "all",
        status: str | None = None,
        limit: int = 50,
    ) -> list:
        """查询事件。project_ids 用于 session_scope 范围过滤。"""
        session = get_session()
        try:
            query = session.query(Event)
            if project_id:
                query = query.filter(Event.project_id == project_id)
            elif project_ids:
                query = query.filter(Event.project_id.in_(project_ids))
            ...
```

> 对 `query_tasks`, `query_meetings`, `query_files`, `query_messages` 等方法做同样的 `project_ids` 参数追加和 SQL 过滤。

### 模块验收检测

```bash
# 验收 1：smoke_test 通过（无 Skill 文件时，走原路径）
uv run python scripts/smoke_test.py
# → 应无报错

# 验收 2：有 Skill 文件时，Skill 路径激活
# 先用 M7 生成一份测试 Skill，或手动创建 emily-data/skills/SOP-002-REC-event-record.skill.yaml
uv run python -c "
from pathlib import Path
skills_dir = Path('emily-data/skills')
skills_dir.mkdir(exist_ok=True)
skill_text = '''
skill_id: SOP-002-REC-event-record
sop_id: SOP-002-REC
version: \"1.0\"
display_name: 事件记录
instructions: 执行事件录入
tools:
  - name: query_data
    description: 查询项目
  - name: record_event
    description: 录入事件
steps:
  - id: step-01
    description: 推断项目
    tool_name: query_data
    tool_params:
      query_type: {source: fixed, value: project}
      keyword: {source: context, path: project_name}
    output_key: project_info
  - id: step-02
    description: 录入
    tool_name: record_event
    tool_params:
      title: {source: user_input, extraction: 事件标题, required: true}
      event_type: {source: user_input, extraction: 事件类型, required: false, default: other}
      event_date: {source: fixed, value: today}
      description: {source: user_input, extraction: 事件描述, required: true}
    output_key: event_result
'''
Path(skills_dir, 'SOP-002-REC-event-record.skill.yaml').write_text(skill_text, encoding='utf-8')
print('TEST SKILL CREATED')
"

# 验收 3：端到端实战测试
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "科技城5号楼铺装完成，录一下" --sender "真实用户名"
# → 检查日志：docker logs --tail 50 emily-core 2>&1 | grep "node2.*skill_definition"

# 验收 4：session_scope 过滤验证
docker logs --tail 100 emily-core 2>&1 | grep "session_scope"
# → 应看到 session_scope 日志
```

---

## M7: SOP-to-Skill 转换器（离线工具）

**依赖**：M2

**职责**：CLI 工具，用 LLM 将 SOP .md 转换为 Skill YAML。输出需人工审校。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 转换脚本 | `scripts/sop_to_skill.py` |
| 2 | LLM 转换 prompt | `emily-data/prompts/sop_to_skill.md` |

### 代码

#### `emily-data/prompts/sop_to_skill.md` — 新建

```markdown
你是一个 SOP 到 Skill 的转换器。请将以下 SOP 文档转换为 Skill 定义 YAML。

输出格式要求：
1. 三段结构：instructions / tools / steps（不要输出 datasets 段）
2. instructions：从 SOP §3.3 和 §5 提取执行要点，写为给 AI 的指引
3. tools：从 §3.2 工具表提取，每项含 name 和 description
4. steps：从 §3.3 处理流程提取为线性步骤序列，每步含：
   - id: step-NN
   - description: 步骤描述
   - tool_name: 使用的工具名
   - tool_params: 参数来源映射
     - source: user_input / prev_step / fixed / context
     - extraction: (user_input 时) 提取提示
     - path: (prev_step / context 时) 数据路径
     - value: (fixed 时) 固定值
     - required: 是否必填
     - default: 默认值
     - enum: 枚举值列表
   - output_key: 输出键名

关键规则：
- source=context 用于从 session-context 获取运行时数据（如 project_name, user_id）
- source=fixed 用于固定值，today 表示当前日期
- source=user_input 需要 LLM 从用户消息提取，extraction 是提取提示
- source=prev_step 用于从前一步结果取值，path 是 dot-path
- 不输出 datasets 段
- 步骤间有依赖时，用 output_key + prev_step 链接

SOP 文档内容：
```
{sop_text}
```
```

#### `scripts/sop_to_skill.py` — 新建

```python
#!/usr/bin/env python3
"""SOP-to-Skill 转换器 —— 用 LLM 将 SOP .md 转换为 Skill YAML。

用法：
    uv run python scripts/sop_to_skill.py --sop SOP-002-REC --dry-run
    uv run python scripts/sop_to_skill.py --sop SOP-002-REC
    uv run python scripts/sop_to_skill.py --all
"""

import argparse
import os
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "emily-core"))


def _find_sop_dir() -> Path:
    """多级 fallback 查找 SOP 目录。"""
    candidates = [
        PROJECT_ROOT / "emily-data" / "sops",
        Path("/app/sops"),
    ]
    for d in candidates:
        if d.exists():
            return d
    raise FileNotFoundError("SOP 目录未找到")


def _find_skill_dir() -> Path:
    """查找或创建 Skill 目录。"""
    candidates = [
        PROJECT_ROOT / "emily-data" / "skills",
        Path("/app/skills"),
    ]
    for d in candidates:
        if d.exists():
            return d
    # 创建默认目录
    default = candidates[0]
    default.mkdir(parents=True, exist_ok=True)
    return default


def _load_sop(sop_id: str, sop_dir: Path) -> str:
    """加载 SOP .md 文件内容。"""
    # 按编号模糊匹配
    for f in sop_dir.glob(f"*{sop_id}*.md"):
        return f.read_text(encoding="utf-8")
    raise FileNotFoundError(f"SOP 文件未找到: {sop_id} (在 {sop_dir})")


def _call_llm(sop_text: str, api_key: str, base_url: str, model: str) -> str:
    """调用 LLM 将 SOP 转换为 Skill YAML。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt_path = PROJECT_ROOT / "emily-data" / "prompts" / "sop_to_skill.md"
    if prompt_path.exists():
        system_prompt = prompt_path.read_text(encoding="utf-8")
    else:
        system_prompt = "将以下 SOP 文档转换为 Skill YAML（三段结构：instructions / tools / steps），不要输出 datasets 段。"

    system_prompt = system_prompt.replace("{sop_text}", sop_text)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请转换此 SOP 文档为 Skill YAML。"},
        ],
        temperature=0,
    )

    content = response.choices[0].message.content or ""

    # 提取 YAML 代码块
    if "```yaml" in content:
        content = content.split("```yaml")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    return content.strip()


def _validate_yaml(text: str) -> bool:
    """验证 YAML 格式。"""
    try:
        import yaml
        data = yaml.safe_load(text)
        return isinstance(data, dict) and "skill_id" in data and "steps" in data
    except Exception:
        return False


def convert_sop(sop_id: str, dry_run: bool = False) -> str | None:
    """转换单个 SOP。"""
    sop_dir = _find_sop_dir()
    skill_dir = _find_skill_dir()

    sop_text = _load_sop(sop_id, sop_dir)
    print(f"加载 SOP: {sop_id} ({len(sop_text)} chars)")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY 环境变量")
        return None

    yaml_text = _call_llm(sop_text, api_key, base_url, model)

    if not _validate_yaml(yaml_text):
        print("警告: LLM 输出不是合法 Skill YAML，请人工审校")

    if dry_run:
        print("\n" + "=" * 60)
        print(yaml_text)
        print("=" * 60)
        return yaml_text

    # 写入文件
    # 从 YAML 解析 skill_id 确定文件名
    import yaml
    data = yaml.safe_load(yaml_text)
    skill_id = data.get("skill_id", sop_id)
    output_path = skill_dir / f"{skill_id}.skill.yaml"
    output_path.write_text(yaml_text, encoding="utf-8")
    print(f"写入: {output_path}")
    return yaml_text


def main():
    parser = argparse.ArgumentParser(description="SOP-to-Skill 转换器")
    parser.add_argument("--sop", help="SOP 编号（如 SOP-002-REC）")
    parser.add_argument("--all", action="store_true", help="批量转换全部 SOP")
    parser.add_argument("--dry-run", action="store_true", help="输出到 stdout 不写文件")
    args = parser.parse_args()

    if not args.sop and not args.all:
        parser.print_help()
        return

    if args.all:
        sop_dir = _find_sop_dir()
        for f in sorted(sop_dir.glob("SOP-*.md")):
            # 提取 SOP ID
            sop_id = f.stem.split("-")[0] + "-" + f.stem.split("-")[1] + "-" + f.stem.split("-")[2]
            print(f"\n转换: {sop_id}")
            convert_sop(sop_id, dry_run=args.dry_run)
    else:
        convert_sop(args.sop, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：dry-run 输出合法 YAML（需 DEEPSEEK_API_KEY）
DEEPSEEK_API_KEY=sk-xxx uv run python scripts/sop_to_skill.py --sop SOP-002-REC --dry-run > /tmp/test_skill.yaml
uv run python -c "import yaml; d=yaml.safe_load(open('/tmp/test_skill.yaml')); assert 'instructions' in d; assert 'tools' in d; assert 'steps' in d; assert 'datasets' not in d; print(f'OK: {d.get(\"skill_id\")}')"

# 验收 2：写入文件
DEEPSEEK_API_KEY=sk-xxx uv run python scripts/sop_to_skill.py --sop SOP-002-REC
ls emily-data/skills/SOP-002-REC-event-record.skill.yaml

# 验收 3：生成的 Skill 文件可被 M2 解析
uv run python -c "
from emily_core.skill.parser import parse_skill_file
from pathlib import Path
skill = parse_skill_file(Path('emily-data/skills/SOP-002-REC-event-record.skill.yaml'))
print(f'Parsed: {skill.skill_id}, steps={len(skill.steps)}, tools={[t.name for t in skill.tools]}')
"
```

---

## M8: 11 份 SOP 的 Skill 文件

**依赖**：M1, M7

**职责**：用 M7 转换器批量生成 11 份 Skill YAML 文件，人工审校后入库。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 11 份 Skill YAML | `emily-data/skills/*.skill.yaml` |

### 操作

```bash
# 创建 skills 目录
mkdir -p emily-data/skills

# 批量转换全部 SOP（dry-run 先看）
DEEPSEEK_API_KEY=sk-xxx uv run python scripts/sop_to_skill.py --all --dry-run

# 确认无误后正式写入
DEEPSEEK_API_KEY=sk-xxx uv run python scripts/sop_to_skill.py --all

# 人工审校每份 Skill 文件
```

### 模块验收检测

```bash
# 验收 1：11 份文件存在
ls emily-data/skills/*.skill.yaml | wc -l
# → 应为 11

# 验收 2：全部可被 M2 解析
uv run python -c "
from emily_core.skill.parser import parse_skill_file
from pathlib import Path
errors = []
for f in sorted(Path('emily-data/skills').glob('*.skill.yaml')):
    try:
        skill = parse_skill_file(f)
        assert skill.steps, f'{f.name} has no steps'
        assert skill.tools, f'{f.name} has no tools'
        print(f'  ✅ {f.name}: {len(skill.steps)} steps, tools={[t.name for t in skill.tools]}')
    except Exception as e:
        errors.append(f'{f.name}: {e}')
        print(f'  ❌ {f.name}: {e}')
assert not errors, f'{len(errors)} file(s) failed'
print('ALL PASS')
"

# 验收 3：端到端实战
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "科技城5号楼铺装完成，录一下" --sender "真实用户名"
# → 验证 node2._source=skill_definition

# 验收 4：回归——无 Skill 匹配的消息走原路径
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
# → 正常闲聊回复，不走 Skill 路径
```

---

## 组装验证

全部模块完成后，运行端到端组装验证：

```bash
# 1. 离线烟雾测试（无 LLM）
uv run python scripts/smoke_test.py
# → 应无报错，Skill 路径和原路径均正常

# 2. Skill 加载验证
uv run python -c "
from emily_core.skill.registry import SkillRegistry
from pathlib import Path
reg = SkillRegistry(skill_directory='emily-data/skills')
status = reg.load()
assert status.is_ready, f'Skill registry not ready: {status}'
print(f'SkillRegistry: {status.successfully_parsed} skills loaded')
"

# 3. 端到端实战测试
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我记录一个事件：3号楼基础浇筑完成" --sender "真实用户名"
# → 验证：
#   a) 日志中 node2._source=skill_definition
#   b) 回复包含事件编号
#   c) session_scope 中的 project_ids 被注入

# 4. 数据边界验证：不同权限用户查询结果不同
# 用低权限用户测试，验证 query_data 按 session_scope 过滤
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

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
