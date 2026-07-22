# Session 归档：DB 薄索引 + 实时追加 md 文件

## Context（为什么做这件事）

`需求/意见.md` 指出：当前 Session 归档存在 DB（`session_archives` 表）违背其「人类复查」本意——它存的是最近 40 条消息的 JSON 快照（截断 2000 字、**无工具调用、无 LLM 思考**），且只在 Session 注销时写一次。用户期望归档是**按时间+人物命名、含对话+工具调用+LLM 思考的 md 文件**，且**每轮实时写入**（而非结束才存）。

**关键洞察**：用户想要的内容（对话/工具调用/LLM 调用）**已实时存在于 operational DB 表**——`messages`、`pipeline_execution_logs`、`evolution_llm_interaction_logs`，按 `conversation_id` + `pipeline_run_id` 索引。`session_archives` 是唯一「违背本意」的部分。因此方案是**从现有表渲染出派生 md 文件**，而非新建日志采集链路。

**决策（已与用户确认）**：
1. `session_archives` DB 表 → **薄索引**（元数据 + `md_file_path`，弃用 JSON 大字段）
2. md 文件 → **每轮实时追加**（Session 创建写文件头，每回合追加一节，归档写结尾）

**分工统一**：DB = 机读源真（实时、可 SQL 查询）；md = 人读复查产物（派生、按时间+人物命名）。回应意见第 2 点「是否统一存放方法」——按用途分层而非物理合并。

---

## 目标产物

每个 Session（`conversation_id`）一个 md 文件：`emily-data/session_archives/{开始日期}_{人员}_{conv_id前8位}.md`

```markdown
# Emily 会话归档：孙建国

> 自动生成，供人工复查。含对话、工具调用、LLM 调用记录。
> 会话ID: a1b2c3d4-xxxx  ·  开始: 2026-07-22 14:30:00 (UTC+8)
> 人员: 孙建国（参建执行 · 中天建设集团 · level 2）

---

## 第 1 轮 · 14:30:12

### 👤 用户
我当前在什么全景节点里？

### 🤖 Emily
你当前所在节点是 SG-JG-01...

### 🔧 执行追踪（WorkItem · sop=query_current_node · DONE · 320ms）
- 意图: sop=query_current_node, 置信度=high, 复合=否
- 工具调用:
  - ✓ query_node (120ms)  参数: {"project_id":"ECOCITY-26"}
- LLM 调用 (2):
  - #1 intent [json] deepseek-chat · 350ms · 1200 tok · 摘要: {"sop_id":"query_current_node"...}
  - #2 execution deepseek-chat · 280ms · 900 tok · 摘要: 你当前所在...

---

## 会话归档
- 归档时间: 2026-07-22 15:45:00
- 归档原因: expired (TTL 600s 无活动)
- 总轮数: 5
```

---

## 实施步骤

### 1. 新增 `SessionArchiveWriter`（核心，文件 I/O + 渲染）
**新文件**：`emily-core/emily_core/services/session_archive_writer.py`

参照 [event_journal.py](emily-core/emily_core/services/event_journal.py) 的追加式 md 模式 + [paths.py](emily-core/emily_core/infrastructure/paths.py) `resolve_data_path` 三级路径解析。

```python
class SessionArchiveWriter:
    def __init__(self, archive_dir: str, enabled: bool = True): ...
    def _path_for(self, conversation_id, user_name, started_at) -> Path
        # {日期}_{人员 sanitized}_{conv_id[:8]}.md；同 conv_id 续接则复用现有文件
    def ensure_header(self, conversation_id, user_name, started_at, context) -> str
        # 文件不存在则创建+写头部；返回 path（幂等）
    def append_turn(self, path, turn_idx, inbound_msg, reply_content,
                    workitems: list, llm_logs: list) -> bool
        # 渲染并追加一轮（含用户消息、回复、各 WorkItem 的意图/工具/结果 + LLM 调用明细）
    def append_footer(self, path, turn_count, archive_reason) -> bool
```

- **纯渲染函数**（`_render_turn` / `_render_header` / `_render_footer`）独立可测，无 I/O。
- 所有文件 I/O 包 `try/except`，失败只 `logger.warning`，**绝不阻断 Agent 主流程**（与 EventJournal/AgentTraceService 同原则）。
- 人员名做文件名 sanitize（移除 `\/:*?"<>|` 等非法字符）。

### 2. 查询本回合 LLM 调用明细
**新增 repo 方法**：在 `evolution_llm_interaction_logs` 对应 repo（或新建 `EvolutionLLMInteractionRepo`）加：
```python
@staticmethod
def list_by_pipeline_run_ids(run_ids: list[str]) -> list[EvolutionLLMInteractionLog]:
    # WHERE pipeline_run_id IN (...) ORDER BY call_sequence
```
（表已有 `pipeline_run_id` 索引列，见 [models.py](emily-core/emily_core/infrastructure/database/models.py) `EvolutionLLMInteractionLog`。）

### 3. 在 WorkItem 上保留 `pipeline_run_id`（小改动，供回查 LLM 日志）
**改** [workitem.py](emily-core/emily_core/workitem/workitem.py)：WorkItem dataclass 加字段 `pipeline_run_id: str = ""`。
**改** [scheduler.py:104-111](emily-core/emily_core/workitem/scheduler.py#L104-L111) `_run_one`：创建 BusContext 后 `wi.pipeline_run_id = context.pipeline_run_id`。

> 注：`deepseek-chat` 不产生 `reasoning_content`，「LLM 思考」即意图推理 + 各次 LLM 调用的 `response_summary` + 工具调用决策——均已落库。若日后切 reasoner 模型，再扩展 `LLMInteractionLogger` 记录 `reasoning_content`（本计划不涉及）。

### 4. 实时追加接入 SessionAgent
**改** [session_agent.py](emily-core/emily_core/session/session_agent.py)：
- `__init__` 增 `archive_writer=None` 参数，存 `self._archive_writer`。
- `_handle_impl` 起始处 `self._last_turn_workitems = []`；正常路径在 `done = run_all_with_message(...)` 后 `self._last_turn_workitems = done`（fast-reply/未匹配/SYS-confirm 路径保持空列表）。
- `handle()` 在 `reply is not None` 分支内、`_record_turn` 后，**单点**调用：
  ```python
  self._append_archive_turn(message, reply, self._last_turn_workitems)
  ```
- 新方法 `_append_archive_turn`：
  1. `path = self._archive_writer.ensure_header(conv_id, user_name, started_at, context)`
  2. 收集 `run_ids = [wi.pipeline_run_id for wi in workitems if wi.pipeline_run_id]`
  3. `llm_logs = EvolutionLLMInteractionRepo.list_by_pipeline_run_ids(run_ids)`（`asyncio.to_thread` 包裹，sync repo）
  4. `turn_idx = len(self.context.message_history)//2 + 1`
  5. `self._archive_writer.append_turn(path, turn_idx, message, reply.content, workitems, llm_logs)`
  6. 全程 try/except fail-open。

### 5. 归档结尾 + DB 薄索引
**改** [session_context.py:372-412](emily-core/emily_core/session/session_context.py#L372-L412) `persist_and_consolidate` / `_persist_archive`：
- `_persist_archive` 不再写 `message_history_snapshot` / `context_snapshot` JSON（传空串），改传 `md_file_path`。
- `md_file_path` 由 SessionAgent 在首回合 `ensure_header` 时得到并暂存（`self.context.archive_md_path` 或 SessionAgent 属性），归档时取用。
- 归档时调 `archive_writer.append_footer(path, turn_count, archive_reason)`。

**改** [session_archive_repo.py](emily-core/emily_core/repositories/session_archive_repo.py) `create()`：增 `md_file_path: str = ""` 参数（旧两个 snapshot 参数保留默认 `""` 以向后兼容，不再赋实值）。

### 6. 模型 + 配置 + 接线
**改** [models.py:211](emily-core/emily_core/infrastructure/database/models.py#L211) `SessionArchive`：增 `md_file_path = Column(String(500), default="")`。旧 `message_history_snapshot`/`context_snapshot` 列**保留不删**（`create_all` 不 ALTER、不 DROP，避免迁移风险；只是不再写入）。新列注册到 `init_db` 的 `_PENDING_COLUMNS` 映射（参照 CLAUDE.md §9 踩坑：新列仅对已注册表生效）。

**改** [config.py](emily-core/emily_core/config.py)：增 `session_archive_enabled: bool = True`、`session_archive_dir: str = ""`。
**改** [core_config.json](emily-data/config/core_config.json)：增 `"session_archive_enabled": true`、`"session_archive_dir": ""`。

**改** [__init__.py](emily-core/emily_core/__init__.py)（参照 `_init_m8c_services` EventJournal 接线模式）：
- 初始化 `SessionArchiveWriter`：`resolve_data_path(config.session_archive_dir, "/app/session_archives", "emily-data/session_archives")`。
- 存 `self._session_archive_writer`，注入 `SessionFactory` → `SessionAgent`。

**改** [session_factory.py](emily-core/emily_core/adapters/session/session_factory.py)：构造 SessionAgent 时传入 `archive_writer`（从 Core 取）。

---

## 涉及文件清单

| 文件 | 改动 |
|------|------|
| `services/session_archive_writer.py` | **新增** 渲染+文件 I/O |
| `repositories/session_archive_repo.py` | `create()` 增 `md_file_path` |
| `infrastructure/database/models.py` | `SessionArchive` 增 `md_file_path` 列 + `_PENDING_COLUMNS` 注册 |
| `workitem/workitem.py` | WorkItem 增 `pipeline_run_id` 字段 |
| `workitem/scheduler.py` | `_run_one` 回填 `wi.pipeline_run_id` |
| `session/session_agent.py` | `__init__` 接收 writer；`handle` 单点追加；`_append_archive_turn` 新方法；`archive` 传 md_file_path |
| `session/session_context.py` | `_persist_archive` 改薄索引 + footer |
| `adapters/session/session_factory.py` | 传 archive_writer |
| `config.py` / `core_config.json` | 增 2 个配置项 |
| `__init__.py` | 初始化 + 注入 writer |
| evolution_llm_interaction repo | 增 `list_by_pipeline_run_ids` |

---

## 验证

1. **单元**：`_path_for` 命名（日期+人员+conv8）、sanitize 非法字符；`_render_turn` 对空 workitems（fast-reply）/有 workitems/失败 workitems 三种输出正确。
2. **端到端（emy-test）**：
   ```powershell
   # 取真实用户 UUID
   docker exec emily-postgres psql -U emily -d emily -c "SELECT id,username,permission_level FROM users WHERE status='active' LIMIT 5;"
   # 第 1 条消息
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我当前在什么全景节点里？" --sender "孙建国"
   ```
   - 发第 1 条后**立即**检查 `emily-data/session_archives/2026-07-22_孙建国_*.md` 已存在且含第 1 轮（验证实时，非归档才存）。
   - 发第 2 条（如「帮我创建事件：样板段放线完成」）后检查 md 含 2 轮，第 2 轮有工具调用 + LLM 调用明细。
3. **DB 验证**：
   ```powershell
   docker exec emily-postgres psql -U emily -d emily -c "SELECT conversation_id,user_name,turn_count,md_file_path,archive_reason FROM session_archives ORDER BY archived_at DESC LIMIT 5;"
   ```
   确认 `md_file_path` 非空、`message_history_snapshot` 为空。
4. **归档结尾**：等 Session TTL 过期（或调小 `session_ttl_seconds` 测试）后，检查 md 文件末尾有「## 会话归档」节。
5. **fail-open**：临时把 `session_archive_dir` 指向不可写路径，确认 Agent 仍正常回复、只记 warning。

---

## 文档同步（改动后）
- [docs/数据库设计.md](docs/数据库设计.md)：`session_archives` 表补 `md_file_path` 列 + 标注 snapshot 列已弃用。
- [docs/业务模块与运转全景.md](docs/业务模块与运转全景.md)：Session 归档流程更新为「实时 md 追加 + DB 薄索引」。
- [docs/技术踩坑备忘录.md](docs/技术踩坑备忘录.md)：追加「新增列须注册 `_PENDING_COLUMNS`」若本计划首次踩到。
- [docs/代码文件目录.md](docs/代码文件目录.md)：补 `session_archive_writer.py` 一句话。
