# 工具参数 Schema 缺失 — 系统性排查报告

- **审计日期**: 2026-07-29
- **审计范围**: 所有 41 个已注册工具的参数 schema 可达性
- **关键发现**: 51% (21/41) 的工具 LLM 规划时看不到参数约束

---

## 一、问题根源

`_reg_biz()` 在 [registry.py L341-L343](file:///d:/app/Emily/emily-core/emily_core/tools/registry.py#L327-L347) 硬编码丢弃了所有参数 schema：

```python
def _reg_biz(reg, name, desc, handler, ...):
    reg.register(_tool(name, desc,
        {"type": "object", "properties": {}},  # ← 无论源文件定义了什么 schema，始终传空
        handler, ...))
```

同样的问题在 `_register_base()` 的 `query_data` 注册处（[L158-L160](file:///d:/app/Emily/emily-core/emily_core/tools/registry.py#L158-L160)）也存在，同样传了空 schema 而非使用 `_QUERY_TOOL_SCHEMA`。

---

## 二、全量审计结果

### 2.1 概览

```
总工具数: 41
  ├─ Schema 正常可达 LLM: 20 (49%)
  ├─ 源文件有 schema 但被丢弃: 16 (39%)  ← 可即时修复
  └─ 源文件无 schema，完全空缺:  5 (12%)  ← 需新建
```

### 2.2 Schema 正常可达的工具（20 个 — 无需处理）

| # | 工具名 | Schema 来源 |
|---|--------|-------------|
| 1 | `knowledge_search` | `knowledge_search_tool.py/_KNOWLEDGE_SEARCH_SCHEMA` |
| 2 | `ocr_document` | `ocr_tool.py/_OCR_SCHEMA` |
| 3 | `parse_document` | `parse_document_tool.py/_PARSE_SCHEMA` |
| 4 | `extract_table` | `extract_table_tool.py/_TABLE_SCHEMA` |
| 5 | `chunk_text` | `chunk_tool.py/_CHUNK_SCHEMA` |
| 6 | `embed_and_index` | `embed_tool.py/_EMBED_SCHEMA` |
| 7 | `write_user_memory` | `memory_tool.py` create_memory_tool 返回 |
| 8 | `create_node` | `node_tool.py/_CREATE_NODE_SCHEMA` |
| 9 | `query_node` | `node_tool.py/_QUERY_NODE_SCHEMA` |
| 10 | `update_node_progress` | `node_tool.py/_UPDATE_PROGRESS_SCHEMA` |
| 11 | `add_node_dependency` | `node_tool.py/_ADD_DEPENDENCY_SCHEMA` |
| 12 | `mount_child_node` | `node_tool.py/_MOUNT_CHILD_SCHEMA` |
| 13 | `update_nodes` | `node_tool.py/_UPDATE_NODES_SCHEMA` |
| 14 | `activate_nodes` | `node_tool.py/_ACTIVATE_NODES_SCHEMA` |
| 15 | `discard_nodes` | `node_tool.py/_DISCARD_NODES_SCHEMA` |
| 16 | `send_email` | `project.py/_SEND_EMAIL_SCHEMA` |
| 17 | `fetch_inbox` | `project.py/_FETCH_INBOX_SCHEMA` |
| 18 | `chat_archive` | `project.py/_CHAT_ARCHIVE_SCHEMA` |
| 19 | `manage_pending_issues` | `project.py/_PENDING_ISSUE_SCHEMA` |
| 20 | `voice_entry` | `project.py/_VOICE_ENTRY_SCHEMA` |

### 2.3 源文件有 schema 但被丢弃（16 个 — 需修 `_reg_biz`）

| # | 工具名 | 源文件 | Schema 常量 | LLM 有用度 | 关键约束 |
|---|--------|--------|-------------|-----------|----------|
| 1 | **`record_event`** | event_tool.py | `_EVENT_TOOL_SCHEMA` | ⭐⭐⭐ | `data.title`(10字), `data.event_type`(枚举), `project_id`, `force`(bool) |
| 2 | **`record_task`** | task_tool.py | `_TASK_TOOL_SCHEMA` | ⭐⭐⭐ | `data.title`(10字), `due_date`(YYYY-MM-DD), `data.assignee` |
| 3 | **`record_meeting`** | meeting_tool.py | `_MEETING_TOOL_SCHEMA` | ⭐⭐⭐ | `data.title`, `data.attendees`(array), `data.summary` |
| 4 | **`record_file`** | file_tool.py | `_FILE_TOOL_SCHEMA` | ⭐⭐⭐ | `data.file_category`(枚举), `data.purpose`(枚举), `data.filename` |
| 5 | **`query_data`** | query_tool.py | `_QUERY_TOOL_SCHEMA` | ⭐⭐⭐ | 12 个属性，含 `query_type`(10枚举), `time_range`(4枚举), `limit`(integer) |
| 6 | `query_files` | file_tool.py | `_QUERY_FILES_SCHEMA` | ⭐⭐ | `file_category`(7枚举), `keyword`, `limit`(integer) |
| 7 | `update_file_category` | file_tool.py | `_UPDATE_CATEGORY_SCHEMA` | ⭐⭐⭐ | `file_no`(格式示例), `file_category`(7枚举) |
| 8 | `send_file` | file_tool.py | `_SEND_FILE_SCHEMA` | ⭐⭐ | `file_no`(格式示例), `caption` |
| 9 | `link_file` | file_tool.py | `_LINK_FILE_SCHEMA` | ⭐⭐⭐ | `module_type`(6枚举: NODE_STARTUP_DOC 等) |
| 10 | `new_file_version` | file_tool.py | `_NEW_FILE_VERSION_SCHEMA` | ⭐⭐⭐ | `parent_file_no`, `version_label`, `new_filename` |
| 11 | `delete_file` | file_tool.py | `_DELETE_FILE_SCHEMA` | ⭐ | `file_no`(格式示例) |
| 12 | `list_file_versions` | file_tool.py | `_LIST_FILE_VERSIONS_SCHEMA` | ⭐ | `file_no`(格式示例) |
| 13 | `link_to_master` | file_tool.py | `_LINK_TO_MASTER_SCHEMA` | ⭐⭐ | `file_no` + `master_file_no`(格式示例) |
| 14 | `unlink_attachment` | file_tool.py | `_UNLINK_ATTACHMENT_SCHEMA` | ⭐ | `file_no`(格式示例) |
| 15 | `list_attachments` | file_tool.py | `_LIST_ATTACHMENTS_SCHEMA` | ⭐ | `master_file_no`(格式示例) |
| 16 | `update_file_purpose` | file_tool.py | `_UPDATE_PURPOSE_SCHEMA` | ⭐⭐ | `purpose`(4枚举: EVIDENCE/RECORD/DESIGN/REFERENCE) |

### 2.4 源文件无 schema（5 个 — 需从零创建）

| # | 工具名 | 源文件 | 缺失的关键参数 |
|---|--------|--------|---------------|
| 1 | `create_task_node` | node_task_tool.py | `project_id`, `title`, `executor_id`, `deadline_at`, `parent_node_id`, `description` |
| 2 | `submit_node_deliverable` | node_task_tool.py | `node_id`, `content`, `file_url`, `file_name` |
| 3 | `confirm_node_deliverable` | node_task_tool.py | `deliverable_id`, `node_id` |
| 4 | `return_node_deliverable` | node_task_tool.py | `deliverable_id`, `reason` |
| 5 | `query_my_nodes` | node_task_tool.py | `project_id`, `node_type`, `limit`, `status_filter` |

---

## 三、影响分层

### 3.1 直接影响：核心 CRUD 工具 LLM 盲填参数

当前 LLM 在 node2 规划时，对 `record_event` 的 `{available_tools}` 看到的是：

```
- record_event: 记录项目事件
```

**没有 `project_id`、没有 `event_type` 枚举、没有 `data` 嵌套结构、没有 `title` 的 10 字限制。**

这就是为什么 FK 违反会发生的直接原因——LLM 不知道 `project_id` 应该是一个 UUID，它看到用户说了"翠湖庭院"就填进去了。

### 3.2 间接影响：枚举值的自动校验缺失

`event_type` 有 `enum: ["construction_progress", "acceptance_check", "milestone_reached", ...]`，但 LLM 看不到。如果 LLM 编造了一个不在枚举中的值（如 `"日常巡检"`），工具 handler 收到后可能导致静默错误或数据污染。

### 3.3 工具描述也被丢弃

除了 schema 外，`_reg_biz` 注册时工具描述也被截取为简短字符串。例如 `record_event` 注册描述是"记录项目事件"（6 字），而 `_EVENT_TOOL_DESCRIPTION` 包含字段分级（[必有]/[应有]/[可有]）和 guardian 规则说明，全被丢弃。

---

## 四、修复路线

### 4.1 即时修复：`_reg_biz` 接受 schema 参数

**[registry.py L327-L347](file:///d:/app/Emily/emily-core/emily_core/tools/registry.py#L327-L347)** — 修改函数签名，增加 schema 参数：

```python
def _reg_biz(reg, name, desc, handler, params=None,
             category="business", permission_flag="write"):
    schema = params if params else {"type": "object", "properties": {}}
    reg.register(_tool(name, desc, schema, handler,
                       category=category, permission_flag=permission_flag))
    return 1
```

然后每个 `_reg_biz` 调用侧补上 `params=_XXX_SCHEMA`。

### 4.2 统一修复：`_register_base` 的 query_data

[registry.py L158-L160](file:///d:/app/Emily/emily-core/emily_core/tools/registry.py#L158-L160) — 将 `_QUERY_TOOL_SCHEMA` 作为 schema 参数传入。

### 4.3 补缺：5 个 node_task 工具

为 `node_task_tool.py` 的 5 个工具分别定义 JSON Schema 常量，参照 node_tool.py 的模式。

### 4.4 增强：`_build_params_summary` 暴露非枚举约束

当前 `_build_params_summary` 只提取 `enum` + `required`。修复 schema 传递后，需同时增强摘要函数，将 `description`、`format`、嵌套对象名等也暴露给 LLM。详见 `workitem_agent.py L60-L83`。

---

## 五、修复优先级

| 优先级 | 工具组 | 数量 | 理由 |
|--------|--------|------|------|
| P0 | `record_event` + `query_data` | 2 | 最常用；record_event 是 FK 违反的直接触发者；query_data 是最频繁的查询入口 |
| P1 | `record_task` + `record_meeting` | 2 | 与 record_event 同属核心 CRUD |
| P2 | 13 个 file 工具 | 13 | 参数较简单但枚举/格式约束对 LLM 同样关键 |
| P3 | 5 个 node_task 工具 | 5 | 需从零创建 schema，工作量大 |

---

## 六、附加发现：`tools_consistency.py` 已感知此问题

[tools_consistency.py L98-L99](file:///d:/app/Emily/emily-core/emily_core/infrastructure/tools_consistency.py) 已有注释：

```python
# 无 schema 常量的工具（write_user_memory / node_task 5 个）不在此映射，V12 对其跳过。
```

但该注释只覆盖了 node_task 和 write_user_memory 6 个工具，**完全没有提及 16 个有 schema 但被 `_reg_biz` 丢弃的工具**。审计模块本身也有盲区——它假设"有 schema 常量 = 已正确注册"，没有做注册时 schema 和源文件 schema 的交叉对比。
