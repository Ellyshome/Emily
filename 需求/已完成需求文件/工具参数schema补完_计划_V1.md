# 工具参数 Schema 补完 — AI 执行计划

> **基于需求**：[AUDIT_工具参数schema缺失系统性排查](../AUDIT_工具参数schema缺失系统性排查.md)
> **计划版本**：V1
> **目标**：修复 21 个工具 schema 不可达 LLM 的系统性问题，让 LLM 规划时能看见所有工具的完整参数约束

---

## 你的角色

你作为 **Emily开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`_reg_biz()` 改为接受新参数（`params=None`），但所有调用方中 `desc` 和 `handler` 的语义不变
2. **禁止修改 handler 逻辑**：只改注册层和 schema，不动任何 handler 函数的行为
3. **node_task 工具注册方式不动**：`_register_project` 中用 `try/except` 包装的注册结构保持不变，只改传入的 schema
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **参照模式**：所有新代码必须参照 `knowledge_search` 的注册模式（schema 常量从源文件导入）

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `_EVENT_TOOL_SCHEMA` | `tools/event_tool.py#L19-L79` | 完整 schema (7 属性 + 嵌套 data) | M2 中导入并传入 `_reg_biz()` |
| `_TASK_TOOL_SCHEMA` | `tools/task_tool.py#L17-L65` | 完整 schema (5 属性 + 嵌套 data) | M2 中导入并传入 `_reg_biz()` |
| `_MEETING_TOOL_SCHEMA` | `tools/meeting_tool.py#L17-L58` | 完整 schema (5 属性 + 嵌套 data) | M2 中导入并传入 `_reg_biz()` |
| `_QUERY_TOOL_SCHEMA` | `tools/query_tool.py#L18-L77` | 完整 schema (12 属性 + 2 枚举) | M1 中导入并传入 `_register_base()` |
| `_FILE_TOOL_SCHEMA` 等 12 个 | `tools/file_tool.py` | 完整 schema（见审计报告2.3） | M2 中导入并传入 `_reg_biz()` |
| `_build_params_summary()` | `workitem/workitem_agent.py#L62-L85` | 从 JSON Schema 提取参数摘要 | M3 中增强 |
| `_reg_biz()` | `tools/registry.py#L327-L347` | 业务工具注册器（硬编码空 schema） | M0 中改签名 |

### 架构决策

不创建新的注册管道（如 ToolSchemaRegistry），而是**直接修复现有 `_reg_biz()`**——这是最小改动路径。源文件的 schema 常量已完备（16 个），只需打通最后一公里让它们到达注册表。不要引入新的中间层或映射文件。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| Schema 常量定义 | `tools/event_tool.py#L19-L79` / `tools/task_tool.py#L17-L65` | `{type, properties, required}` 结构，`"data"` 嵌套对象模式 |
| Schema 常量导入+注册 | `tools/registry.py#L165-L173` (knowledge_search) | `from .xxx_tool import _XXX_SCHEMA` → `_tool(name, desc, _XXX_SCHEMA, handler)` |
| `_reg_biz` 函数模式 | `tools/registry.py#L327-L347` | fail-safe (try/except + logger.warning + return 0/1) |

---

## 模块依赖图

```
M0 (_reg_biz 改签名)
  │
  ├──→ M1 (query_data)
  │
  ├──→ M2 (13 file 工具) ──┐
  │                         ├──→ M5 (组装验证)
  ├──→ M2' (event/task/meeting) ─┘
  │
  └──→ M3 (_build_params_summary 增强) ──→ M5
       
M4 (node_task schemas) ──────────→ M5
```

M0 是基础——修改 `_reg_biz` 签名后 M1/M2 才能传 schema。M3 与 M1/M2 独立（只改 workitem_agent.py），可并行。M4 独立（node_task_tool.py 不受 `_reg_biz` 影响，直接传 `_tool()`）。M5 是所有模块的组装验证。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M0 | `emily_core/tools/registry.py` | 修改 | `_reg_biz()` 增加 `params` 参数 |
| M1 | `emily_core/tools/registry.py` | 修改 | `_register_base()` 中 query_data 注册传 `_QUERY_TOOL_SCHEMA` |
| M2 | `emily_core/tools/registry.py` | 修改 | 13 个 file 工具 + 3 个核心 CRUD 工具的 `_reg_biz()` 调用补 `params=` |
| M3 | `emily_core/workitem/workitem_agent.py` | 修改 | `_build_params_summary()` 增强为非枚举字段输出描述/hint |
| M4 | `emily_core/tools/node_task_tool.py` | 修改 | 新增 5 个 schema 常量 |
| M4 | `emily_core/tools/registry.py` | 修改 | `_register_project()` 中 5 个 node_task 工具注册传 schema |
| M5 | 无新文件 | 验证 | compiled + 导入测试 + `_build_params_summary` 端到端 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily_core/tools/registry.py` | 修改 | M0: `_reg_biz` 函数签名；M1: `_register_base` query_data；M2: 所有 `_reg_biz` 调用；M4: node_task 注册 |
| `emily_core/workitem/workitem_agent.py` | 修改 | M3: `_build_params_summary` 函数体 |
| `emily_core/tools/node_task_tool.py` | 修改 | M4: 新增 5 个 schema 常量 |

---

## M0: `_reg_biz()` 签名改造

**依赖**：无（本模块为首建模块）

**职责**：为 `_reg_biz()` 增加可选的 `params` 参数，不再硬编码空 schema。不传 `params` 时行为不变（向后兼容）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 修改 `_reg_biz()` 函数签名和内部调用 | `emily-core/emily_core/tools/registry.py` |

### 代码

#### `emily-core/emily_core/tools/registry.py` — 替换 `_reg_biz` 函数（第 327-347 行）

```python
# emily-core/emily_core/tools/registry.py

def _reg_biz(reg, name, desc, handler, params=None,
             category="business", permission_flag="write"):
    """注册一个业务工具（fail-safe），异常时仅打日志不抛错。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        name: 工具名称。
        desc: 工具描述。
        handler: 异步处理函数。
        params: 工具参数 JSON Schema（dict），可选。传入 None 时使用空 schema（向后兼容）。
        category: 工具分类，默认 business。
        permission_flag: 权限标识，默认 write。

    Returns:
        int — 成功返回 1，失败返回 0，方便累加计数。
    """
    try:
        schema = params if params else {"type": "object", "properties": {}}
        reg.register(_tool(name, desc, schema, handler,
                          category=category, permission_flag=permission_flag))
        return 1
    except Exception as e:
        logger.warning("tool '%s' registration failed: %s", name, e)
        return 0
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/tools', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：_reg_biz 可接受 params 参数（运行时路径：在 Docker 内执行）
# 用 python 直接测试函数签名
uv run python -c "
from emily_core.tools.registry import _reg_biz
import inspect
sig = inspect.signature(_reg_biz)
params = sig.parameters
assert 'params' in params, f'_reg_biz must have params parameter, got: {list(params.keys())}'
assert params['params'].default is None, 'params default must be None'
print('OK: _reg_biz accepts params=None')
"
→ 预期输出：OK: _reg_biz accepts params=None
```

**失败处理**：如果验收不通过，检查 `_reg_biz` 函数定义中 `params=None` 是否在 `category` 参数之前。

---

## M1: `query_data` schema 修复

**依赖**：M0（`_reg_biz` 已改签名）

**职责**：`_register_base()` 中 `query_data` 的注册从硬编码空 schema 改为使用 `_QUERY_TOOL_SCHEMA`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 修改 `_register_base()` 中 query_data 注册行 | `emily-core/emily_core/tools/registry.py` |

### 代码

#### `emily-core/emily_core/tools/registry.py` — 在 `_register_base` 函数体中，替换第 152-160 行

替换前：
```python
    # query_data
    from .query_tool import handle_query_data
    qs = getattr(core, "_query_service", None)
    if qs is None:
        from ..services.query_service import QueryService
        qs = QueryService()
    reg.register(_tool("query_data", "查询项目数据（事件/任务/会议/文件/消息/用户/项目/日志）",
                       {"type": "object", "properties": {}},
                       partial(handle_query_data, query_service=qs)))
    _bc += 1
```

替换后：
```python
    # query_data
    from .query_tool import handle_query_data, _QUERY_TOOL_SCHEMA, _QUERY_TOOL_DESCRIPTION
    qs = getattr(core, "_query_service", None)
    if qs is None:
        from ..services.query_service import QueryService
        qs = QueryService()
    reg.register(_tool("query_data", _QUERY_TOOL_DESCRIPTION,
                       _QUERY_TOOL_SCHEMA,
                       partial(handle_query_data, query_service=qs)))
    _bc += 1
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/tools/registry.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：验证 schema 已被引用（确认导入路径正确）
uv run python -c "
from emily_core.tools.query_tool import _QUERY_TOOL_SCHEMA, _QUERY_TOOL_DESCRIPTION
assert 'properties' in _QUERY_TOOL_SCHEMA
assert 'query_type' in _QUERY_TOOL_SCHEMA['properties']
assert 'required' in _QUERY_TOOL_SCHEMA
print(f'OK: query schema has {len(_QUERY_TOOL_SCHEMA[\"properties\"])} properties')
"
→ 预期输出：OK: query schema has 12 properties
```

**失败处理**：如果 `_QUERY_TOOL_SCHEMA` 导入失败，检查 `query_tool.py#L18` 是否仍然存在该常量。

---

## M2: 核心 CRUD + file 工具 schema 注入

**依赖**：M0（`_reg_biz` 已改签名）

**职责**：`_register_business()` 中所有 `_reg_biz()` 调用补上 `params=` 参数，传入源文件中已定义的 schema 常量。共修改 16 处调用。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `_register_business()` 顶部增加批量导入 | `emily-core/emily_core/tools/registry.py` |
| 2 | 16 处 `_reg_biz()` 调用补 `params=` | `emily-core/emily_core/tools/registry.py` |

### 代码

#### 步骤 1 — `emily-core/emily_core/tools/registry.py`：在 `_register_business()` 函数体开头（第 212 行 `_buc = 0` 之后），追加 schema 导入

找到 `cfg = core.config` 行之后，将已有的 `from .event_tool import ...` 的隐式调用（在 `_h` helper 中）替换为显式导入。实际上，最简单的改动是在每个 `_reg_biz` 调用前插导入语句。但为了代码整洁，我们统一在函数开头导入。

```python
# emily-core/emily_core/tools/registry.py
# 在 _register_business() 函数体内，_buc = 0 之后，cfg = core.config 之后，追加：

    # ── 导入各工具的 schema 常量 ──
    from .event_tool import _EVENT_TOOL_SCHEMA as _EVT_S
    from .task_tool import _TASK_TOOL_SCHEMA as _TSK_S
    from .meeting_tool import _MEETING_TOOL_SCHEMA as _MTG_S
    from .file_tool import (
        _FILE_TOOL_SCHEMA, _QUERY_FILES_SCHEMA, _UPDATE_CATEGORY_SCHEMA,
        _SEND_FILE_SCHEMA, _LINK_FILE_SCHEMA, _NEW_FILE_VERSION_SCHEMA,
        _DELETE_FILE_SCHEMA, _LIST_FILE_VERSIONS_SCHEMA,
        _LINK_TO_MASTER_SCHEMA, _UNLINK_ATTACHMENT_SCHEMA,
        _LIST_ATTACHMENTS_SCHEMA, _UPDATE_PURPOSE_SCHEMA,
    )
```

#### 步骤 2 — `emily-core/emily_core/tools/registry.py`：替换所有 `_reg_biz()` 调用

替换 16 处调用，每处从 `_reg_biz(reg, "name", "desc", ...)` 改为 `_reg_biz(reg, "name", "desc", ..., params=_XXX_S)`。

**核心 CRUD 3 处**（第 216-228 行）：

```python
    # record_event (L216-218)
    _buc += _reg_biz(reg, "record_event", "记录项目事件",
                     partial(_h("event_tool", "handle_record_event"),
                             event_app=core._event_app), params=_EVT_S,
                     category="business", permission_flag="write")
    # record_task (L219-221)
    _buc += _reg_biz(reg, "record_task", "创建任务",
                     partial(_h("task_tool", "handle_record_task"),
                             task_app=core._task_app), params=_TSK_S,
                     category="business", permission_flag="write")
    # record_meeting (L222-224)
    _buc += _reg_biz(reg, "record_meeting", "归档会议纪要",
                     partial(_h("meeting_tool", "handle_record_meeting"),
                             meeting_app=core._meeting_app), params=_MTG_S,
                     category="business", permission_flag="write")
```

**record_file**（第 225-230 行）：

```python
    _buc += _reg_biz(reg, "record_file", "记录文件元数据",
                     partial(_h("file_tool", "handle_record_file"),
                             file_app=core._file_app,
                             file_manager=core._file_manager,
                             tei_client=core._tei_client,
                             kc_repo=core._knowledge_chunk_repo),
                     params=_FILE_TOOL_SCHEMA,
                     category="business", permission_flag="write")
```

**文件查询 + 分类修改 2 处**（第 233-238 行）：

```python
    _buc += _reg_biz(reg, "query_files", "按分类或关键词查询项目文件",
                     partial(_h("file_tool", "handle_query_files"),
                             file_app=core._file_app), params=_QUERY_FILES_SCHEMA,
                     category="business", permission_flag="all")
    _buc += _reg_biz(reg, "update_file_category", "修改文件分类归属",
                     partial(_h("file_tool", "handle_update_file_category"),
                             file_app=core._file_app), params=_UPDATE_CATEGORY_SCHEMA,
                     category="business", permission_flag="write")
```

**send_file**（第 241-245 行）：

```python
    _buc += _reg_biz(reg, "send_file", "向用户发送已有文件",
                     partial(_h("file_tool", "handle_send_file"),
                             file_manager=core._file_manager,
                             outbound_bus=core.outbound_bus),
                     params=_SEND_FILE_SCHEMA,
                     category="business", permission_flag="all")
```

**文件关联与版本 4 处**（第 248-264 行）：

```python
    _buc += _reg_biz(reg, "link_file", "关联文件到业务对象",
                     partial(_h("file_tool", "handle_link_file"),
                             file_manager=core._file_manager),
                     params=_LINK_FILE_SCHEMA,
                     category="business", permission_flag="write")
    _buc += _reg_biz(reg, "new_file_version", "创建文件新版本",
                     partial(_h("file_tool", "handle_new_file_version"),
                             file_app=core._file_app,
                             file_manager=core._file_manager),
                     params=_NEW_FILE_VERSION_SCHEMA,
                     category="business", permission_flag="write")
    _buc += _reg_biz(reg, "delete_file", "软删除文件",
                     partial(_h("file_tool", "handle_delete_file"),
                             file_manager=core._file_manager),
                     params=_DELETE_FILE_SCHEMA,
                     category="business", permission_flag="write")
    _buc += _reg_biz(reg, "list_file_versions", "列出文件版本",
                     partial(_h("file_tool", "handle_list_file_versions"),
                             file_manager=core._file_manager),
                     params=_LIST_FILE_VERSIONS_SCHEMA,
                     category="business", permission_flag="all")
```

**附件链 + purpose 4 处**（第 266-284 行）：

```python
    _buc += _reg_biz(reg, "link_to_master", "挂载附件到主文件",
                     partial(_h("file_tool", "handle_link_to_master"),
                             file_manager=core._file_manager),
                     params=_LINK_TO_MASTER_SCHEMA,
                     category="business", permission_flag="write")
    _buc += _reg_biz(reg, "unlink_attachment", "卸载附件为独立文件",
                     partial(_h("file_tool", "handle_unlink_attachment"),
                             file_manager=core._file_manager),
                     params=_UNLINK_ATTACHMENT_SCHEMA,
                     category="business", permission_flag="write")
    _buc += _reg_biz(reg, "list_attachments", "列出主文件下的附件",
                     partial(_h("file_tool", "handle_list_attachments"),
                             file_manager=core._file_manager),
                     params=_LIST_ATTACHMENTS_SCHEMA,
                     category="business", permission_flag="all")
    _buc += _reg_biz(reg, "update_file_purpose", "校正文件的业务意图",
                     partial(_h("file_tool", "handle_update_file_purpose"),
                             file_manager=core._file_manager),
                     params=_UPDATE_PURPOSE_SCHEMA,
                     category="business", permission_flag="write")
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/tools/registry.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：验证每个 schema 常量可导入且非空
uv run python -c "
from emily_core.tools.event_tool import _EVENT_TOOL_SCHEMA
from emily_core.tools.task_tool import _TASK_TOOL_SCHEMA
from emily_core.tools.meeting_tool import _MEETING_TOOL_SCHEMA
from emily_core.tools.file_tool import (
    _FILE_TOOL_SCHEMA, _QUERY_FILES_SCHEMA, _UPDATE_CATEGORY_SCHEMA,
    _SEND_FILE_SCHEMA, _LINK_FILE_SCHEMA, _NEW_FILE_VERSION_SCHEMA,
    _DELETE_FILE_SCHEMA, _LIST_FILE_VERSIONS_SCHEMA,
    _LINK_TO_MASTER_SCHEMA, _UNLINK_ATTACHMENT_SCHEMA,
    _LIST_ATTACHMENTS_SCHEMA, _UPDATE_PURPOSE_SCHEMA,
)
schemas = [
    ('_EVENT_TOOL_SCHEMA', _EVENT_TOOL_SCHEMA),
    ('_TASK_TOOL_SCHEMA', _TASK_TOOL_SCHEMA),
    ('_MEETING_TOOL_SCHEMA', _MEETING_TOOL_SCHEMA),
    ('_FILE_TOOL_SCHEMA', _FILE_TOOL_SCHEMA),
    ('_QUERY_FILES_SCHEMA', _QUERY_FILES_SCHEMA),
    ('_UPDATE_CATEGORY_SCHEMA', _UPDATE_CATEGORY_SCHEMA),
    ('_SEND_FILE_SCHEMA', _SEND_FILE_SCHEMA),
    ('_LINK_FILE_SCHEMA', _LINK_FILE_SCHEMA),
    ('_NEW_FILE_VERSION_SCHEMA', _NEW_FILE_VERSION_SCHEMA),
    ('_DELETE_FILE_SCHEMA', _DELETE_FILE_SCHEMA),
    ('_LIST_FILE_VERSIONS_SCHEMA', _LIST_FILE_VERSIONS_SCHEMA),
    ('_LINK_TO_MASTER_SCHEMA', _LINK_TO_MASTER_SCHEMA),
    ('_UNLINK_ATTACHMENT_SCHEMA', _UNLINK_ATTACHMENT_SCHEMA),
    ('_LIST_ATTACHMENTS_SCHEMA', _LIST_ATTACHMENTS_SCHEMA),
    ('_UPDATE_PURPOSE_SCHEMA', _UPDATE_PURPOSE_SCHEMA),
]
failed = []
for name, s in schemas:
    if not isinstance(s, dict) or 'properties' not in s:
        failed.append(name)
if failed:
    print(f'FAILED: {failed}')
else:
    print(f'OK: all {len(schemas)} schemas valid')
"
→ 预期输出：OK: all 15 schemas valid
```

**失败处理**：如果某个 schema 导入失败，检查 `file_tool.py` 中对应常量名是否匹配 12 个schema 的名称（特别是 `_UPDATE_CATEGORY_SCHEMA` 和 `_UPDATE_PURPOSE_SCHEMA` 容易拼错）。

---

## M3: `_build_params_summary` 增强

**依赖**：M1/M2（schema 到达 `tool.parameters` 后，增强版摘要才能从非空 schema 中提取信息）

**职责**：`_build_params_summary()` 当前只提取 `enum` + `required` 标记。需要增强为对非枚举字段也输出 `description` 或 `format` 约束，让 LLM 能看懂 `project_id` 是 UUID、`title` 是 10 字以内等。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 重写 `_build_params_summary()` 函数体 | `emily-core/emily_core/workitem/workitem_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem_agent.py` — 替换第 62-85 行的 `_build_params_summary` 函数

```python
# emily-core/emily_core/workitem/workitem_agent.py

def _build_params_summary(parameters: dict) -> str:
    """从 JSON Schema 中提取参数摘要，帮助 LLM 规划时了解合法参数值。

    提取策略：
      1. 枚举字段 → name(enum|enum|...)  带 * 表示 required
      2. 非枚举字段 → 取 description 前 20 字或 format 值作为 hint
      3. 嵌套对象 → name*(object) 标记，不展开子属性
    避免过多细节干扰 LLM 规划。
    """
    if not parameters or not isinstance(parameters, dict):
        return ""
    props = parameters.get("properties", {})
    required_fields = parameters.get("required", [])
    if not props:
        return ""
    parts = []
    for name, schema in props.items():
        if not isinstance(schema, dict):
            continue
        is_required = name in required_fields
        marker = "*" if is_required else ""

        # 1) 枚举字段：展示所有取值
        enum_vals = schema.get("enum")
        if enum_vals:
            vals = "|".join(str(v) for v in enum_vals)
            parts.append(f"{name}{marker}({vals})")
            continue

        # 2) 嵌套对象：标记类型，不展开子属性
        nested_props = schema.get("properties")
        if nested_props:
            parts.append(f"{name}{marker}(object)")
            continue

        # 3) 普通字段：取 description 或 format 作为 hint
        fmt = schema.get("format", "")
        desc = schema.get("description", "")
        if fmt:
            parts.append(f"{name}{marker}({fmt})")
        elif desc:
            hint = desc[:24] + ("..." if len(desc) > 24 else "")
            parts.append(f"{name}{marker}({hint})")

    if not parts:
        return ""
    return "\n    参数: " + ", ".join(parts)
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/workitem/workitem_agent.py', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：用 record_event schema 测试摘要输出
uv run python -c "
from emily_core.workitem.workitem_agent import _build_params_summary
from emily_core.tools.event_tool import _EVENT_TOOL_SCHEMA

summary = _build_params_summary(_EVENT_TOOL_SCHEMA)
print(summary)
assert 'project_id' in summary, f'project_id should appear in summary: {summary}'
assert 'project_name' in summary, f'project_name should appear in summary: {summary}'
assert 'data*' in summary or 'data(object)' in summary, f'data*(object) should appear: {summary}'
print('OK: record_event summary includes parameter hints')
"
→ 预期输出包含 project_id(...) 和 project_name(...) 和 data*(object)

# 验收 3：空 schema 不变
uv run python -c "
from emily_core.workitem.workitem_agent import _build_params_summary
assert _build_params_summary({}) == ''
assert _build_params_summary(None) == ''
assert _build_params_summary({'type': 'object', 'properties': {}}) == ''
print('OK: empty schema returns empty string')
"
→ 预期输出：OK: empty schema returns empty string

# 验收 4：枚举字段行为不变
uv run python -c "
from emily_core.workitem.workitem_agent import _build_params_summary
s = {'type': 'object', 'properties': {'category': {'type': 'string', 'enum': ['A', 'B']}}, 'required': ['category']}
summary = _build_params_summary(s)
assert 'category*(A|B)' in summary, f'Expected category*(A|B), got: {summary}'
print('OK: enum fields unchanged')
"
→ 预期输出：OK: enum fields unchanged
```

**失败处理**：如果验收 2 中 `project_id` 或 `project_name` 没出现在摘要中，检查 schema 中是否有 `description` 字段。如果 `data*` 没出现，检查嵌套对象检测逻辑（`nested_props = schema.get("properties")`）。

---

## M4: node_task 工具 schema 创建

**依赖**：无（本模块独立，直接改 `_tool()` 调用，不走 `_reg_biz`）

**职责**：为 `node_task_tool.py` 的 5 个 handler 定义 JSON Schema 常量，并在 `_register_project()` 的注册调用中传入。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 新增 5 个 schema 常量 | `emily-core/emily_core/tools/node_task_tool.py` |
| 2 | `_register_project()` 中注册调用传 schema | `emily-core/emily_core/tools/registry.py` |

### 代码

#### `emily-core/emily_core/tools/node_task_tool.py` — 在 logger 定义行之后、第一个 handler 函数之前，插入 5 个 schema 常量

```python
# emily-core/emily_core/tools/node_task_tool.py
# 插入在 logger = logging.getLogger("emily.tool.node_task") 之后
# 第一个 async def handle_create_task_node 之前

# ══════════════════════════════════════════════════════════════════════════════
# 工具参数 JSON Schema
# ══════════════════════════════════════════════════════════════════════════════

_CREATE_TASK_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目 UUID",
        },
        "title": {
            "type": "string",
            "description": "节点名称",
        },
        "executor_id": {
            "type": "string",
            "description": "执行人 ID（UUID 格式）",
        },
        "deadline_at": {
            "type": "string",
            "description": "截止时间（ISO 8601 格式）",
        },
        "parent_node_id": {
            "type": "string",
            "description": "父节点 ID（UUID 格式）",
        },
        "owner_dept_id": {
            "type": "string",
            "description": "归口部门 ID，默认'项目总'",
        },
        "description": {
            "type": "string",
            "description": "节点描述",
        },
    },
    "required": ["project_id", "title"],
}

_SUBMIT_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {
            "type": "string",
            "description": "成果 ID（为空则新建成果）",
        },
        "content": {
            "type": "string",
            "description": "成果内容或说明",
        },
        "file_url": {
            "type": "string",
            "description": "文件链接地址",
        },
        "file_name": {
            "type": "string",
            "description": "文件名称",
        },
        "attachment_file_id": {
            "type": "string",
            "description": "附件文件 ID（UUID 格式）",
        },
        "is_acceptance_check": {
            "type": "boolean",
            "description": "是否为验收审查提交",
        },
    },
    "required": ["content"],
}

_CONFIRM_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {
            "type": "string",
            "description": "要确认的成果 ID（UUID 格式）",
        },
        "reason": {
            "type": "string",
            "description": "确认理由",
        },
    },
    "required": ["deliverable_id"],
}

_RETURN_DELIVERABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "deliverable_id": {
            "type": "string",
            "description": "要退回的成果 ID（UUID 格式）",
        },
        "reason": {
            "type": "string",
            "description": "退回原因",
        },
    },
    "required": ["deliverable_id"],
}

_QUERY_MY_NODES_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目 UUID，可选",
        },
        "node_type": {
            "type": "string",
            "description": "节点类型筛选，可选",
        },
        "limit": {
            "type": "integer",
            "description": "返回结果上限，默认 20",
        },
    },
    "required": [],
}
```

#### `emily-core/emily_core/tools/registry.py` — 在 `_register_project()` 中修改 node_task 工具的注册段（第 418-443 行）

将注册循环中的空 schema 替换为对应的 schema 常量。需要先导入：

在 `from .node_task_tool import (` 行（第 421 行）追加导入 schema 常量：

```python
# emily-core/emily_core/tools/registry.py
# 修改第 421-425 行的 import 语句：

                from .node_task_tool import (
                    handle_create_task_node, handle_submit_node_deliverable,
                    handle_confirm_node_deliverable, handle_return_node_deliverable,
                    handle_query_my_nodes,
                    _CREATE_TASK_NODE_SCHEMA, _SUBMIT_DELIVERABLE_SCHEMA,
                    _CONFIRM_DELIVERABLE_SCHEMA, _RETURN_DELIVERABLE_SCHEMA,
                    _QUERY_MY_NODES_SCHEMA,
                )
```

修改注册循环（第 426-441 行），在 `for name, desc, handler in [` 元组中追加 schema：

```python
# emily-core/emily_core/tools/registry.py
# 替换第 426-441 行：

                for name, desc, handler, schema in [
                    ("create_task_node", "创建TASK类型叶子节点（替代record_plan_task）",
                     partial(handle_create_task_node, node_service=ns),
                     _CREATE_TASK_NODE_SCHEMA),
                    ("submit_node_deliverable", "提交节点成果（替代submit_plan_task）",
                     partial(handle_submit_node_deliverable, node_service=ns),
                     _SUBMIT_DELIVERABLE_SCHEMA),
                    ("confirm_node_deliverable", "确认节点成果",
                     partial(handle_confirm_node_deliverable, node_service=ns),
                     _CONFIRM_DELIVERABLE_SCHEMA),
                    ("return_node_deliverable", "退回节点成果",
                     partial(handle_return_node_deliverable, node_service=ns),
                     _RETURN_DELIVERABLE_SCHEMA),
                    ("query_my_nodes", "查询我负责的节点（替代query_plan_tasks）",
                     partial(handle_query_my_nodes, node_service=ns),
                     _QUERY_MY_NODES_SCHEMA),
                ]:
                    if not reg.has(name):
                        reg.register(_tool(name, desc, schema, handler,
                                          category="business", permission_flag="write"))
                        _pjc += 1
```

### 模块验收检测

```bash
# 验收 1：编译通过
cd d:\app\Emily\emily-core
uv run python -c "import compileall; compileall.compile_dir('emily_core/tools', quiet=1); print('OK')"
→ 预期输出：OK

# 验收 2：5 个 schema 可导入且结构合法
uv run python -c "
from emily_core.tools.node_task_tool import (
    _CREATE_TASK_NODE_SCHEMA, _SUBMIT_DELIVERABLE_SCHEMA,
    _CONFIRM_DELIVERABLE_SCHEMA, _RETURN_DELIVERABLE_SCHEMA,
    _QUERY_MY_NODES_SCHEMA,
)
schemas = [
    ('_CREATE_TASK_NODE_SCHEMA', _CREATE_TASK_NODE_SCHEMA),
    ('_SUBMIT_DELIVERABLE_SCHEMA', _SUBMIT_DELIVERABLE_SCHEMA),
    ('_CONFIRM_DELIVERABLE_SCHEMA', _CONFIRM_DELIVERABLE_SCHEMA),
    ('_RETURN_DELIVERABLE_SCHEMA', _RETURN_DELIVERABLE_SCHEMA),
    ('_QUERY_MY_NODES_SCHEMA', _QUERY_MY_NODES_SCHEMA),
]
for name, s in schemas:
    assert isinstance(s, dict), f'{name} is not dict'
    assert 'type' in s, f'{name} missing type'
    assert 'properties' in s, f'{name} missing properties'
    print(f'{name}: {len(s[\"properties\"])} properties')
print('OK')
"
→ 预期输出：5 行，每行显示 schema 名称和属性数

# 验收 3：schema 与 handler 参数对齐（关键字段检查）
uv run python -c "
from emily_core.tools.node_task_tool import _CREATE_TASK_NODE_SCHEMA
props = _CREATE_TASK_NODE_SCHEMA['properties']
assert 'project_id' in props
assert 'title' in props
assert 'executor_id' in props
assert 'deadline_at' in props
assert _CREATE_TASK_NODE_SCHEMA['required'] == ['project_id', 'title']
print('OK: create_task_node schema alignment verified')
"
→ 预期输出：OK: create_task_node schema alignment verified
```

**失败处理**：如果某 schema 缺少字段，回到 `node_task_tool.py` 中的 handler 函数核对 `params.get()` 调用的 key 名称。如果循环解包 `for name, desc, handler, schema in [` 出错，检查 registry.py 中 import 行是否包含所有 5 个新 schema 常量。

---

## M5: 组装验证

**依赖**：M0 + M1 + M2 + M3 + M4 全部完成

**职责**：端到端验证所有模块组装后的完整功能链。

### 模块验收检测

```bash
# 组装验证 1：全量编译
cd d:\app\Emily\emily-core
uv run python -c "import compileall; result = compileall.compile_dir('emily_core', quiet=1); print('OK') if result else print('FAIL')"
→ 预期输出：OK

# 组装验证 2：导入关键链（registry → tools → schema）
uv run python -c "
from emily_core.tools.registry import _register_base, _register_business, _register_project, _reg_biz
print('OK: registry imports')
"
→ 预期输出：OK: registry imports

# 组装验证 3：_build_params_summary 完整链路 — 端到端验证
# 模拟注册后的实际场景：schema → _build_params_summary → LLM 可读文本
uv run python -c "
from emily_core.workitem.workitem_agent import _build_params_summary

# 场景 1：record_event（最重要 — FK 违反根因）
from emily_core.tools.event_tool import _EVENT_TOOL_SCHEMA
s = _build_params_summary(_EVENT_TOOL_SCHEMA)
print('record_event:', s)
assert 'project_id' in s, 'project_id MUST appear'
assert 'project_name' in s, 'project_name MUST appear'
assert 'data' in s, 'data (nested) MUST appear'

# 场景 2：query_data（最常用）
from emily_core.tools.query_tool import _QUERY_TOOL_SCHEMA
s = _build_params_summary(_QUERY_TOOL_SCHEMA)
print('query_data:', s)
assert 'query_type' in s, 'query_type MUST appear with enum'

# 场景 3：create_task_node（新创建的 schema）
from emily_core.tools.node_task_tool import _CREATE_TASK_NODE_SCHEMA
s = _build_params_summary(_CREATE_TASK_NODE_SCHEMA)
print('create_task_node:', s)
assert 'project_id' in s, 'project_id MUST appear'
assert 'title' in s, 'title MUST appear'

# 场景 4：空 schema 工具不受影响
s = _build_params_summary({'type': 'object', 'properties': {}})
assert s == '', 'Empty schema must return empty string'

print('')
print('ALL SCENARIOS PASSED')
"
→ 预期输出：
record_event:     参数: project_id(项目 UUID), project_name(项目名称（如 '未来城...), data*(object), ...
query_data:       参数: query_type*(event|task|...), project_id(项目 UUID), ...
create_task_node: 参数: project_id*(项目 UUID), title*(节点名称), ...
ALL SCENARIOS PASSED

# 组装验证 4：统计修复前后对比
uv run python -c "
from emily_core.workitem.workitem_agent import _build_params_summary

# 修复后：每个关键工具都应该有非空摘要
tools_schemas = {}
from emily_core.tools.event_tool import _EVENT_TOOL_SCHEMA
tools_schemas['record_event'] = _EVENT_TOOL_SCHEMA
from emily_core.tools.query_tool import _QUERY_TOOL_SCHEMA
tools_schemas['query_data'] = _QUERY_TOOL_SCHEMA
from emily_core.tools.task_tool import _TASK_TOOL_SCHEMA
tools_schemas['record_task'] = _TASK_TOOL_SCHEMA
from emily_core.tools.meeting_tool import _MEETING_TOOL_SCHEMA
tools_schemas['record_meeting'] = _MEETING_TOOL_SCHEMA

for name, schema in tools_schemas.items():
    summary = _build_params_summary(schema)
    status = 'OK' if summary else 'EMPTY'
    print(f'{status}: {name} — summary has {len(summary)} chars')

print('All core CRUD tools verified')
"
→ 预期输出：4 行 OK，每行 summary 有内容
```

**失败处理**：如果某个场景的摘要为空，检查对应 schema 的 `properties` 中 `description` 字段是否存在，以及 `_build_params_summary` 逻辑是否正确处理了该场景。如果编译失败，回退到对应模块定位问题。

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "V1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
