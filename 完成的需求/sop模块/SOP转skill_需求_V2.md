# SOP转Skill — 需求文档 V2

> **版本**：v2.2
> **基于审核**：[SOP转skill_审核_V1.md](SOP转skill_审核_V1.md)
> **原始文档**：[SOP转skill.md](SOP转skill.md)
> **文档受众**：AI 编码执行工具（非人类战略文档）

---

## 1. 问题定义

当前 SOP 执行路径中，`node2 planner` 将 SOP 7 章自由文本整体喂给 LLM，由 LLM 自由规划 `ExecutionPlan`。这导致 5 类不可靠性：

| # | 问题 | 根因 |
|---|------|------|
| 1 | 步骤遗漏 | LLM 可能跳过 SOP §3.3 的中间步骤 |
| 2 | 工具误用 | LLM 可能选择 §3.2 未列出的工具 |
| 3 | 参数提取不稳定 | §4 字段约束为自然语言，LLM 提取结果不一致 |
| 4 | 流程偏离 | LLM 可能自创步骤或改变顺序 |
| 5 | 异常处理不可控 | §5 为自然语言建议，LLM 可能忽略 |

**目标**：将 SOP 从"LLM 自由推理的参考文档"重构为"引擎可直接执行的结构化定义"——步骤序列和工具绑定由 Skill 定义确定性驱动，LLM 仅负责从用户消息中提取字段值。

---

## 2. 目标架构

### 2.1 改造前后对比

```
改造前：
  node2: LLM 读 SOP 全文 → 自由规划 ExecutionPlan → _source="llm_planner"
  node3: 按 ExecutionPlan 调用 handler，tool_params 100% 来自 LLM 规划

改造后：
  node2: 若 Skill 存在 → 直接解析为 ExecutionPlan → _source="skill_definition"
         若 Skill 不存在 → 走原 LLM 规划路径 → _source="llm_planner"（兼容）
  node3: 按 Skill 定义的参数来源映射解析 tool_params
         source=user_input → 调 LLM 提取值
         source=prev_step  → 从前步结果取值
         source=fixed      → 使用固定值
         source=context    → 从 session-context 取值
```

### 2.2 数据治理模型：三层隔离

Skill 不持有数据，不声明数据范围。数据边界由 **session-context + 工具层** 共同保障：

```
┌─────────────────────────────────────────────────────┐
│  session-context（数据边界源头）                        │
│  project_ids / db_perms / info_level / company_type   │
└──────────────────────┬──────────────────────────────┘
                       ↓ 注入 session_scope
┌──────────────────────┴──────────────────────────────┐
│  Skill（纯业务流：instructions + tools + steps）        │
│  不涉及数据范围，只定义"用什么工具、参数从哪来"           │
└──────────────────────┬──────────────────────────────┘
                       ↓ 工具白名单 + session_scope 自动过滤
┌──────────────────────┴──────────────────────────────┐
│  Tools（每个工具按 session_scope 自动限定数据范围）      │
│  query_data: 自动 WHERE project_id IN (project_ids)  │
│  record_event: 检查 db_perms["events"] 是否可写       │
│  1:1 工具天然受约束，1:N 工具按 session_scope 过滤     │
└─────────────────────────────────────────────────────┘
```

**关键原则**：

- **Skill = 纯业务流**（instructions / tools / steps 三段），不声明 datasets，不持有数据
- **数据来源 = session-context**，通过 `source: context` 显式引用
- **数据边界 = session-context + 工具层自动过滤**，Skill 不参与
- **工具白名单即数据白名单**：Skill 的 `tools` 段声明可用工具，1:1 工具（如 `record_event`）天然限定访问表，1:N 工具（如 `query_data`）按 `session_scope` 参数自动限定查询范围

### 2.3 Skill 定义文件

独立 YAML 文件，存放于 `emily-data/skills/`，一个 SOP 对应一个 Skill 文件。SOP `.md` 文件保留给人看，Skill YAML 给引擎看。**三段结构**：instructions / tools / steps。

```yaml
skill_id: SOP-002-REC-event-record
sop_id: SOP-002-REC
version: "1.0"
display_name: 事件记录

# ── 给 AI 的说明 ──
instructions: |
  你正在执行"事件记录"业务流。请从用户消息中提取事件信息，
  确保标题简洁（≤50字），事件类型优先从枚举中选择。
  如果用户未指定日期，默认使用今天。
  如果无法推断项目归属，主动询问用户。

# ── 可用工具（白名单） ──
tools:
  - name: query_data
    description: 查询项目信息，推断事件所属项目
  - name: record_event
    description: 录入事件到系统

# ── 步骤序列 ──
steps:
  - id: step-01
    description: 推断项目归属
    tool_name: query_data
    tool_params:
      query_type:
        source: fixed
        value: project
      keyword:
        source: context
        path: project_name
    output_key: project_info

  - id: step-02
    description: 录入事件
    tool_name: record_event
    tool_params:
      title:
        source: user_input
        extraction: 事件标题
        required: true
        max_length: 50
      event_type:
        source: user_input
        extraction: 事件类型
        required: false
        enum: [construction_progress, quality_issue, safety_incident,
               material_arrival, inspection, decision, other]
        default: other
      event_date:
        source: fixed
        value: today
      description:
        source: user_input
        extraction: 事件描述
        required: true
      project_id:
        source: prev_step
        path: project_info.object_id
        required: false
    output_key: event_result
```

### 2.4 参数来源映射语法

每个 `tool_params` 条目用以下结构定义参数值如何获取：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source` | enum | ✅ | `user_input` / `prev_step` / `fixed` / `context` |
| `path` | string | 条件 | `prev_step` 时必填，前步结果的 dot-path；`context` 时必填，session-context 字段路径 |
| `value` | any | 条件 | `fixed` 时必填，固定值（`"today"` = 当前日期，`"now"` = 当前时间戳） |
| `extraction` | string | 条件 | `user_input` 时必填，LLM 参数提取提示 |
| `required` | bool | ❌ | 默认 `false`。缺失时若为 true 则中止步骤并报错 |
| `default` | any | ❌ | 缺失时的默认值 |
| `enum` | list | ❌ | 可选值枚举，提取值不在枚举中时使用 default |
| `max_length` | int | ❌ | 字符串最大长度约束 |

**`source: context` 详解**：从 `SessionContext` 的字段取值，可用路径包括：

| path | 含义 | 示例值 |
|------|------|--------|
| `user_id` | 当前用户 UUID | `"uuid-xxx"` |
| `user_name` | 用户名 | `"张工"` |
| `project_name` | 当前项目名称 | `"生态城26号地块"` |
| `permission_level` | 权限等级 | `3` |
| `company_type` | 企业类型 | `"owner"` |
| `department` | 部门 | `"工程部"` |
| `info_level` | 信息可见级别 | `"public"` |

### 2.5 步骤序列约束

- 当前仅支持**线性步骤序列**（无条件分支）
- 条件分支由 Guardian 机制 + 异常处理覆盖，不在步骤定义中表达
- 每步最多绑定一个工具，`output_key` 将结果存入执行上下文供后续步骤引用

---

## 3. 模块拆解与交付物清单

### 模块依赖图

```
M1(SkillSchema) ──→ M2(SkillParser) ──→ M3(SkillRegistry) ──→ M6(Pipeline集成)
                                              │                       ↑
                                              ↓                       │
                                         M4(ParamExtractor) ──→ M5(SkillExecutor)

M7(SOP转Skill转换器) —— 独立离线工具，依赖 M2

M8(12份Skill文件) —— 依赖 M1+M7
```

### M1: Skill Schema 定义与校验器

**职责**：定义 Skill YAML 的完整 schema，提供校验能力。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Schema YAML（含注释） | `emily-data/schemas/skill_schema.yaml` |
| 2 | Skill 定义 dataclass | `emily-core/emily_core/skill/definition.py` |
| 3 | Schema 校验器 | `emily-core/emily_core/skill/validator.py` |
| 4 | 模块 `__init__.py` | `emily-core/emily_core/skill/__init__.py` |

**dataclass 定义**（`definition.py` 核心类）：

- `ParamMapping`：参数来源映射（source / path / value / extraction / required / default / enum / max_length）
- `SkillStep`：单步骤（id / description / tool_name / tool_params: dict[str, ParamMapping] / output_key）
- `SkillTool`：工具引用（name / description）
- `SkillDefinition`：完整 Skill 定义（skill_id / sop_id / version / display_name / instructions / tools / steps）

**验收检测**：

```bash
# 1. Schema 文件可解析
uv run python -c "import yaml; d=yaml.safe_load(open('emily-data/schemas/skill_schema.yaml')); assert 'properties' in d"

# 2. dataclass 可导入（不含 SkillDataset，三段结构）
uv run python -c "from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping; print('OK')"

# 3. 校验器拒绝非法定义
uv run python -c "
from emily_core.skill.validator import validate_skill;
from emily_core.skill.definition import SkillDefinition;
result = validate_skill(SkillDefinition(skill_id='', sop_id='', version='', display_name='', instructions='', tools=[], steps=[]))
assert not result.is_valid; print('REJECTED OK')
"

# 4. 校验器通过合法定义
uv run python -c "
from emily_core.skill.validator import validate_skill;
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping;
skill = SkillDefinition(
    skill_id='SOP-002-REC-event-record', sop_id='SOP-002-REC',
    version='1.0', display_name='事件记录', instructions='...',
    tools=[SkillTool(name='record_event', description='录入事件')],
    steps=[
        SkillStep(id='step-01', description='录入', tool_name='record_event',
                  tool_params={'title': ParamMapping(source='user_input', extraction='事件标题', required=True)},
                  output_key='result')
    ])
result = validate_skill(skill)
assert result.is_valid; print('PASS OK')
"
```

---

### M2: Skill Parser（YAML 解析器）

**职责**：读取 Skill YAML 文件，解析为 `SkillDefinition` 对象。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Skill YAML 解析器 | `emily-core/emily_core/skill/parser.py` |
| 2 | 单元测试 | `emily-core/tests/test_skill_parser.py` |

**核心方法**：

- `parse_skill_file(path: Path) -> SkillDefinition`：读取 YAML 文件 → 校验 → 返回 SkillDefinition
- `parse_skill_text(text: str, source_name: str) -> SkillDefinition`：解析 YAML 文本
- 内部调用 M1 的 `validate_skill()`，校验失败抛 `SkillParseError`

**验收检测**：

```bash
# 1. 解析示例 Skill 文件成功
uv run python -c "
from emily_core.skill.parser import parse_skill_text;
yaml_text = '''
skill_id: test-skill
sop_id: SOP-002-REC
version: \"1.0\"
display_name: 测试
instructions: 测试指令
tools: []
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

# 2. 解析非法 YAML 抛异常
uv run python -c "
from emily_core.skill.parser import parse_skill_text;
try:
    parse_skill_text('invalid: {broken', 'bad')
    assert False, 'should raise'
except Exception:
    print('REJECT OK')
"
```

---

### M3: Skill Registry（注册表 + 热加载）

**职责**：扫描 `emily-data/skills/` 目录，加载全部 Skill 文件，提供按 `sop_id` 查询能力。启动时加载，支持热重载。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SkillRegistry 类 | `emily-core/emily_core/skill/registry.py` |
| 2 | 单元测试 | `emily-core/tests/test_skill_registry.py` |

**核心方法**：

- `load() -> RegistryStatus`：扫描目录，解析全部 `.skill.yaml` 文件
- `reload() -> RegistryStatus`：热重载（原子替换策略，参照 SOPIntentRegistry）
- `get_by_sop_id(sop_id: str) -> SkillDefinition | None`：按 SOP 编号查询
- `get_by_skill_id(skill_id: str) -> SkillDefinition | None`：按 Skill ID 查询
- `has_skill(sop_id: str) -> bool`：判断某 SOP 是否有对应 Skill

**与 EmilyCore 集成**：`bootstrap.py` 中初始化 `SkillRegistry` 并挂到 `EmilyCore` 实例上。

**验收检测**：

```bash
# 1. 扫描目录并加载
uv run python -c "
from emily_core.skill.registry import SkillRegistry
reg = SkillRegistry(skill_directory='emily-data/skills')
status = reg.load()
print(f'loaded={status.successfully_parsed}, failed={status.failed_parsed}')
"

# 2. 按 sop_id 查询（先用 M7 生成至少 1 个 Skill 文件后再测）
uv run python -c "
from emily_core.skill.registry import SkillRegistry
reg = SkillRegistry(skill_directory='emily-data/skills')
reg.load()
skill = reg.get_by_sop_id('SOP-002-REC')
assert skill is not None
assert skill.skill_id.startswith('SOP-002-REC')
print('QUERY OK')
"

# 3. 无对应 Skill 时返回 None
uv run python -c "
from emily_core.skill.registry import SkillRegistry
reg = SkillRegistry(skill_directory='emily-data/skills')
reg.load()
assert reg.get_by_sop_id('SOP-999-ZZZ') is None
print('MISS OK')
"
```

---

### M4: Parameter Extractor（参数提取引擎）

**职责**：根据 `ParamMapping` 定义，从不同来源解析参数值。`source=user_input` 时调用 LLM 提取。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ParamExtractor 类 | `emily-core/emily_core/skill/param_extractor.py` |
| 2 | 提取 prompt 模板 | `emily-data/prompts/param_extraction.md` |
| 3 | 单元测试 | `emily-core/tests/test_param_extractor.py` |

**核心方法**：

- `async resolve_params(step: SkillStep, context: SkillExecutionContext) -> dict`：解析一步的全部参数
- `async _extract_from_user_input(mapping: ParamMapping, user_input: str) -> any`：调用 LLM chat_json 提取
- `_extract_from_prev_step(mapping: ParamMapping, step_results: dict) -> any`：按 dot-path 取值
- `_extract_from_fixed(mapping: ParamMapping) -> any`：解析固定值（`"today"` → 当前日期）
- `_extract_from_context(mapping: ParamMapping, session_context: dict) -> any`：从 session-context 取值

**LLM 参数提取 prompt**（`param_extraction.md`）：

```
请从用户消息中提取指定字段的值。

字段：{extraction}
约束：{constraints}（如 required={required}, max_length={max_length}, enum={enum}）

用户消息：
{user_input}

仅输出 JSON：{{"value": "提取的值"}}
若无法提取且非必填，输出：{{"value": null}}
```

**验收检测**：

```bash
# 1. fixed source 解析
uv run python -c "
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
m = ParamMapping(source='fixed', value='today')
val = ext._extract_from_fixed(m)
assert val  # today 被解析为当前日期字符串
print(f'FIXED OK: {val}')
"

# 2. prev_step 解析
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

# 3. context 解析
uv run python -c "
from emily_core.skill.param_extractor import ParamExtractor
from emily_core.skill.definition import ParamMapping
ext = ParamExtractor(llm_client=None)
m = ParamMapping(source='context', path='user_id')
val = ext._extract_from_context(m, {'user_id': 'user-456', 'project_name': 'ECOCITY'})
assert val == 'user-456'
print('CONTEXT OK')
"

# 4. user_input 需 LLM（集成测试，需 --llm 环境可用时测）
# 不在单元测试中验证，留到 M6 集成测试
```

---

### M5: Skill Executor（执行引擎）

**职责**：接收 `SkillDefinition` + 运行时上下文，线性执行步骤序列，产出 `StepResult` 列表。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SkillExecutor 类 | `emily-core/emily_core/skill/executor.py` |
| 2 | SkillExecutionContext dataclass | `emily-core/emily_core/skill/context.py` |
| 3 | 单元测试 | `emily-core/tests/test_skill_executor.py` |

**SkillExecutionContext**：

```python
@dataclass
class SkillExecutionContext:
    skill: SkillDefinition
    user_input: str
    user_id: str
    message_id: str
    conversation_id: str
    session_context: dict          # 从 SessionContext 扁平化而来（含 project_ids / db_perms / info_level 等）
    step_results: dict[str, dict]  # output_key → 前步 business_data
    business_flow_tools: BusinessFlowToolRegistry
    llm_client: any                # ParamExtractor 用
```

**SkillExecutor 核心方法**：

- `async execute(ctx: SkillExecutionContext) -> list[StepResult]`：线性执行全部步骤
- 内部流程：对每步 → 校验 `tool_name` 在 `skill.tools` 白名单中 → 调 `ParamExtractor.resolve_params()` → 注入 `session_scope` 到 `tool_params` → 调 `BusinessFlowTool.handler()` → 存 `step_results[output_key]` → 下一步
- 步骤失败即中止（与现有 `_real_execute` 行为一致）

**工具白名单校验**：SkillExecutor 在调用工具前检查 `tool_name` 是否在 `skill.tools` 中声明。未声明的工具名拒绝执行，返回错误 StepResult。

**session_scope 注入**：每次工具调用时，将 `session_context` 中的数据范围字段注入 `tool_params["_session_scope"]`，工具 handler 据此自动限定查询范围。注入内容：

```python
tool_params["_session_scope"] = {
    "project_ids": ctx.session_context.get("project_ids", []),
    "db_perms": ctx.session_context.get("db_perms", {}),
    "info_level": ctx.session_context.get("info_level", "public"),
    "company_type": ctx.session_context.get("company_type", ""),
    "department": ctx.session_context.get("department", ""),
}
```

**验收检测**：

```bash
# 1. 线性执行（mock 工具，无 LLM）
uv run python -c "
from emily_core.skill.executor import SkillExecutor, SkillExecutionContext
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
import asyncio

# 注册 mock 工具
reg = BusinessFlowToolRegistry()
async def mock_handler(params, **kw): return {'success': True, 'reply': 'ok', 'object_id': 'mock-123'}
reg.register(BusinessFlowTool(name='record_event', description='mock', parameters={}, handler=mock_handler))

skill = SkillDefinition(
    skill_id='test', sop_id='SOP-002-REC', version='1.0', display_name='测试',
    instructions='', tools=[SkillTool(name='record_event', description='mock')],
    steps=[SkillStep(id='s1', description='录入', tool_name='record_event',
                     tool_params={'title': ParamMapping(source='fixed', value='测试标题')},
                     output_key='r1')]
)
ctx = SkillExecutionContext(skill=skill, user_input='测试', user_id='', message_id='',
                            conversation_id='', session_context={'project_ids': [], 'db_perms': {}},
                            step_results={}, business_flow_tools=reg, llm_client=None)
results = asyncio.run(SkillExecutor().execute(ctx))
assert len(results) == 1
assert results[0].success
print(f'EXECUTE OK: {results[0].output}')
"

# 2. 工具白名单校验：未声明工具被拒绝
uv run python -c "
from emily_core.skill.executor import SkillExecutor, SkillExecutionContext
from emily_core.skill.definition import SkillDefinition, SkillStep, SkillTool, ParamMapping
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
import asyncio

reg = BusinessFlowToolRegistry()
async def mock_handler(params, **kw): return {'success': True, 'reply': 'ok'}
reg.register(BusinessFlowTool(name='query_data', description='mock', parameters={}, handler=mock_handler))

# Skill 声明 tools=[record_event]，但 step 用 query_data → 应被拒绝
skill = SkillDefinition(
    skill_id='test', sop_id='SOP-002-REC', version='1.0', display_name='测试',
    instructions='', tools=[SkillTool(name='record_event', description='录入')],
    steps=[SkillStep(id='s1', description='查询', tool_name='query_data',
                     tool_params={'query_type': ParamMapping(source='fixed', value='project')},
                     output_key='r1')]
)
ctx = SkillExecutionContext(skill=skill, user_input='测试', user_id='', message_id='',
                            conversation_id='', session_context={}, step_results={},
                            business_flow_tools=reg, llm_client=None)
results = asyncio.run(SkillExecutor().execute(ctx))
assert not results[0].success  # 工具不在白名单 → 失败
print('WHITELIST REJECT OK')
"
```

---

### M6: PipelineBUS 集成 + 工具层 session_scope 支持

**职责**：将 Skill 引入 4 节点 BUS，改造 node2 和 node3，兼容无 Skill 的 SOP。同时改造工具层（`query_data` 等）支持 `session_scope` 自动过滤。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | node2_plan 改造 | `emily-core/emily_core/workitem/workitem_agent.py` 修改 |
| 2 | node3_execute 改造 | `emily-core/emily_core/workitem/workitem_agent.py` 修改 |
| 3 | KnowledgeInjector 适配 | `emily-core/emily_core/workitem/injector.py` 修改 |
| 4 | bootstrap.py 初始化 | `emily-core/emily_core/bootstrap.py` 修改 |
| 5 | EmilyCore 挂载 | `emily-core/emily_core/__init__.py` 修改 |
| 6 | query_data session_scope 过滤 | `emily-core/emily_core/tools/query_tool.py` 修改 |
| 7 | QueryService 范围过滤 | `emily-core/emily_core/services/query_service.py` 修改 |
| 8 | 离线烟雾测试 | `scripts/smoke_test.py` 更新 |

**node2_plan 改造逻辑**：

```python
async def node2_plan(self, context: BusContext) -> None:
    wi = context.work_item
    # 优先尝试 Skill 路径
    skill = self._skill_registry.get_by_sop_id(wi.sop_id) if self._skill_registry else None
    if skill:
        plan = self._skill_to_execution_plan(skill, wi.user_input)
        plan._source = "skill_definition"
    else:
        # 原有 LLM 规划路径（兼容无 Skill 的 SOP）
        planner_mode = self._resolve_mode("planner")
        if planner_mode == "real" and self._llm:
            plan = await self._llm_plan(wi, context)
        else:
            plan = await self._planner.plan(wi.route_decision, context)
    wi.execution_plan = plan
```

**node3_execute 改造逻辑**：

```python
# 在 _real_execute 开头判断：
if wi.execution_plan._source == "skill_definition" and self._skill_executor:
    # Skill 路径：使用 SkillExecutor（内含 ParamExtractor + 白名单校验 + session_scope 注入）
    skill = self._skill_registry.get_by_sop_id(wi.sop_id)
    ctx = self._build_skill_execution_context(skill, wi, context)
    return await self._skill_executor.execute(ctx)
else:
    # 原有路径：直接从 PlanStep.tool_params 取值（保留 _user_id / _message_id 注入）
    ...  # 现有 _real_execute 逻辑
```

**query_data session_scope 过滤改造**：

`handle_query_data` 从 `params["_session_scope"]` 提取数据范围，传给 `QueryService.execute()`：

```python
async def handle_query_data(params, query_service):
    # 提取 session_scope
    session_scope = params.get("_session_scope") or {}

    # db_perms 检查：用户有这张表的读权限吗？
    target_table = _query_type_to_table(params.get("query_type", "event"))
    db_perms = session_scope.get("db_perms", {})
    if db_perms and target_table not in db_perms:
        return {"success": False, "reply": "无权限查询此数据", "total": 0}

    # project_ids 自动注入：未指定 project_id 时限定在可访问项目范围内
    project_ids = session_scope.get("project_ids", [])
    if project_ids and not params.get("project_id"):
        params["project_ids"] = project_ids  # 传入项目范围列表

    # 正常执行查询
    cmd = QueryCommand(...)
    results = query_service.execute(cmd, session_scope=session_scope)
```

`QueryService.execute()` 改造：

```python
def execute(self, cmd, session_scope=None):
    # 若 cmd 包含 project_ids（来自 session_scope），在查询时自动添加 IN 过滤
    # 具体实现：各 query_xxx 方法增加 project_ids 参数，
    # 生成 SQL 时添加 WHERE project_id IN (:project_ids)
```

**KnowledgeInjector 适配**：

- 新增 `get_skill_instructions(sop_id: str) -> str | None`：若 Skill 存在，返回 `instructions` 段
- `get_context_text()` 优先注入 Skill instructions，fallback 到 SOP 全文
- 不删除原有 SOP 全文注入逻辑（node4 回复合成可能仍需）

**验收检测**：

```bash
# 1. 有 Skill 的 SOP → node2 生成 _source="skill_definition" 的 plan
# 需要 M8 至少 1 份 Skill 文件就位
uv run python scripts/smoke_test.py
# → 检查日志中 "WI.*node2.*_source=skill_definition"

# 2. 无 Skill 的 SOP → node2 走 _source="llm_planner" 或 mock
# 删掉 emily-data/skills/ 下所有文件后
uv run python scripts/smoke_test.py
# → 检查日志中 "WI.*node2.*_source=mock" 或 "llm_planner"

# 3. 端到端实战测试（需 --llm + Docker）
# 先查 users 表获取真实 UUID
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "科技城5号楼铺装完成，录一下" --sender "真实用户名"
# → 验证回复中包含事件编号，且日志中 node2._source=skill_definition

# 4. 回归：无 Skill 的消息走原路径
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
# → 验证正常闲聊回复，不触发 Skill 路径

# 5. session_scope 过滤验证：query_data 自动限定项目范围
# 查看日志中 query_data 调用时是否带 project_ids
docker logs --tail 50 emily-core 2>&1 | grep "query_data.*project_ids"
# → 应看到 project_ids 非空的日志
```

---

### M7: SOP-to-Skill 转换器（离线工具）

**职责**：CLI 工具，用 LLM 将 SOP `.md` 文件转换为 Skill YAML 文件。输出需人工审校。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 转换脚本 | `scripts/sop_to_skill.py` |
| 2 | LLM 转换 prompt 模板 | `emily-data/prompts/sop_to_skill.md` |

**CLI 接口**：

```bash
# 单个转换（dry-run 输出到 stdout）
uv run python scripts/sop_to_skill.py --sop SOP-002-REC --dry-run

# 单个转换（写入文件）
uv run python scripts/sop_to_skill.py --sop SOP-002-REC

# 批量转换全部 SOP
uv run python scripts/sop_to_skill.py --all --dry-run
uv run python scripts/sop_to_skill.py --all

# 指定 LLM API（默认用 env 中的 DEEPSEEK_API_KEY）
uv run python scripts/sop_to_skill.py --sop SOP-002-REC --api-key sk-xxx
```

**验收检测**：

```bash
# 1. dry-run 输出合法 YAML（三段结构，无 datasets）
uv run python scripts/sop_to_skill.py --sop SOP-002-REC --dry-run > /tmp/test_skill.yaml
uv run python -c "import yaml; d=yaml.safe_load(open('/tmp/test_skill.yaml')); assert 'instructions' in d; assert 'tools' in d; assert 'steps' in d; assert 'datasets' not in d; print(d.get('skill_id'))"
# → 应输出 skill_id，且无 datasets 段

# 2. 写入文件
uv run python scripts/sop_to_skill.py --sop SOP-002-REC
ls emily-data/skills/SOP-002-REC-event-record.skill.yaml
# → 文件存在

# 3. 生成的 Skill 文件可被 M2 解析
uv run python -c "
from emily_core.skill.parser import parse_skill_file
from pathlib import Path
skill = parse_skill_file(Path('emily-data/skills/SOP-002-REC-event-record.skill.yaml'))
print(f'Parsed: {skill.skill_id}, steps={len(skill.steps)}, tools={[t.name for t in skill.tools]}')
"
# → 应输出 skill_id、步骤数、工具列表
```

---

### M8: 12 份 SOP 的 Skill 文件

**职责**：用 M7 转换器为全部 12 份现有 SOP 生成 Skill YAML 文件，人工审校后入库。

**交付物**：

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 11 份 Skill YAML 文件 | `emily-data/skills/*.skill.yaml` |

**SOP 清单**：

| SOP ID | 文件名 | Skill 文件名 |
|--------|--------|-------------|
| SOP-001-REC | `SOP-001-REC-meeting_summary.md` | `SOP-001-REC-meeting_summary.skill.yaml` |
| SOP-002-REC | `SOP-002-REC-event_record.md` | `SOP-002-REC-event_record.skill.yaml` |
| SOP-003-REC | `SOP-003-REC-task_manage.md` | `SOP-003-REC-task_manage.skill.yaml` |
| SOP-004-FILE | `SOP-004-FILE-file_archive.md` | `SOP-004-FILE-file_archive.skill.yaml` |
| SOP-005-QRY | `SOP-005-QRY-data_query.md` | `SOP-005-QRY-data_query.skill.yaml` |
| SOP-007-REC | `SOP-007-REC-user_memory.md` | `SOP-007-REC-user_memory.skill.yaml` |
| SOP-008-SYS | `SOP-008-SYS-pending_issue.md` | `SOP-008-SYS-pending_issue.skill.yaml` |
| SOP-009-REC | `SOP-009-REC-plan_task.md` | `SOP-009-REC-plan_task.skill.yaml` |
| SOP-010-REC | `SOP-010-REC-plan_task_review.md` | `SOP-010-REC-plan_task_review.skill.yaml` |
| SOP-011-SYS | `SOP-011-SYS-node_manage.md` | `SOP-011-SYS-node_manage.skill.yaml` |
| SOP-999-SYS | `SOP-999-SYS-fallback.md` | `SOP-999-SYS-fallback.skill.yaml` |

> SOP-000-SYS-standard.md 是规范文件（SOPIntentRegistry 跳过），不需要 Skill 文件。

**验收检测**：

```bash
# 1. 全部 Skill 文件存在
ls emily-data/skills/*.skill.yaml | wc -l
# → 应为 11

# 2. 全部可被 M2 解析
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

# 3. tool_name 全部在 BusinessFlowToolRegistry 中注册
uv run python -c "
from emily_core.skill.parser import parse_skill_file
from pathlib import Path
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
reg = BusinessFlowToolRegistry()
# ... 注册所有工具（此处简化，实际由 register_all 完成）
registered = set(reg.list_names()) if reg else set()
for f in Path('emily-data/skills').glob('*.skill.yaml'):
    skill = parse_skill_file(f)
    for step in skill.steps:
        if step.tool_name and step.tool_name not in registered:
            print(f'  ⚠️ {f.name}: tool \"{step.tool_name}\" not registered')
print('CHECK DONE')
"
```

---

## 4. 与现有模块的关系（改动清单）

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `agent/sop_parser.py` | **不变** | Skill 有独立的 parser（M2），不改动 SOP 解析器 |
| `agent/intent_registry.py` | **不变** | 仍负责 §1+§2 路由，Skill 不影响意图识别 |
| `workitem/injector.py` | **微调** | `get_context_text()` 优先注入 Skill instructions，fallback SOP 全文 |
| `tools/business_flow_tools.py` | **不变** | Skill 的 tools 段引用此注册表中的工具名 |
| `tools/query_tool.py` | **改造** | `handle_query_data` 从 `_session_scope` 提取范围，做 db_perms 检查 + project_ids 自动注入 |
| `services/query_service.py` | **改造** | `execute()` 和各 `query_xxx()` 方法增加 `session_scope` 参数，支持 project_ids 范围过滤 |
| `workitem/workitem_agent.py` | **改造** | node2 增加 Skill 路径优先判断；node3 增加 SkillExecutor 调用路径 |
| `bootstrap.py` | **扩展** | 初始化 `SkillRegistry`，挂到 `EmilyCore` |
| `__init__.py` | **扩展** | `EmilyCore` 新增 `_skill_registry` 和 `_skill_executor` 属性 |
| `session/session_agent.py` | **不变** | SessionAgent 不感知 Skill，仍通过 sop_id 路由 |
| `workitem/pipeline/bus.py` | **不变** | BUS 不感知 Skill，仅调用 node handler |
| `workitem/pipeline/hook.py` | **不变** | Hook 机制不受影响 |
| `session/session_context.py` | **不变** | 已有 `has_db_permission()` / `project_ids` / `db_perms` 等字段，无需改动 |

---

## 5. 非功能需求

| # | 需求 | 说明 |
|---|------|------|
| 1 | **性能** | Skill 解析路径的 node2 延迟 < 10ms（纯文件解析，无 LLM），远低于 LLM 规划的 2-5s |
| 2 | **可观测** | `ExecutionPlan._source` 标记 `"skill_definition"` / `"llm_planner"` / `"mock"` |
| 3 | **热加载** | `SkillRegistry.reload()` 与 `SOPIntentRegistry.reload()` 联动 |
| 4 | **启动校验** | 启动时校验所有 Skill 文件：tool_name 是否注册、参数 source 是否合法，校验失败记 warning 不阻断 |
| 5 | **兼容性** | 无 Skill 文件的 SOP 行为与当前完全一致，零影响 |
| 6 | **新 SOP 流程** | 新增 SOP = 放 `.md` 到 `emily-data/sops/` + 放 `.skill.yaml` 到 `emily-data/skills/`，重启生效 |
| 7 | **数据安全** | 工具层自动按 session_scope 过滤，Skill 无法绕过数据边界。无 project_ids 授权的用户查询返回空结果，不报错（静默降级） |
