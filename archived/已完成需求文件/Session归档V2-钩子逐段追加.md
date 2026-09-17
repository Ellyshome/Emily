# Session 归档 V2：Hook 逐段追加 — 从"事后批量渲染"到"总线实时扒取"

## Context（为什么做这件事）

当前归档方案（V1）存在四个根因级问题：

1. **排序是人为选择，不是自然结果**：`_render_turn()` 在整轮完成后一次性渲染，将 `lines` 列表按硬编码顺序拼接（👤 → 🤖 → 🔧）。排序全靠代码中元素的排列顺序，与实际执行时序无关。改一下代码顺序就变了，但时序逻辑不应依赖渲染者的"选择"。

2. **意图识别天然缺位**：`SessionAgent._recognize_intent()` 在 BUS pipeline 之前运行，其 LLM 调用不共享 WorkItem 的 `pipeline_run_id`。归档按 `pipeline_run_id` 查 DB，永远漏掉第一步"模型思考"。

3. **call_category 全标为 intent**：`PipelineBUS.run()` 调 `set_context()` 不传 `call_category`，fallback 将所有 `json_mode` 调用标为 "intent"。归档中 4 次 LLM 调用全显示 `#1 intent #2 intent #3 intent #4 intent`，读者无法区分哪次是规划、哪次是回复合成、哪次是审核。

4. **提示词注入过程不可见**：各阶段向 LLM 注入不同 system prompt（session.md → planner.md → guardian_step.md → workitem.md → guardian_reply.md），变量替换后差异巨大。但归档只记模板名和字符数，审查者无法回溯"模型当时看到了什么"，无法判断幻觉是否源于 prompt 上下文不足或变量注入错误。

**核心洞察**：这四个问题有共同的根因——**归档是在"事后"而非"实时"写入的**。事后收集必然面临排序选择、数据遗漏、分类推断三大难题；而实时追加则让写入顺序自然等于执行顺序，数据随发生随写入，分类由节点上下文自动确定。

**决策**：从"事后批量渲染"改为"BUS Hook 逐段追加"——每个 BUS 节点完成后，由 `ArchiveHook` 实时追加该节点的贡献到归档 md 文件。意图识别在 BUS 之前，有独立的写入点。最终回复在 BUS 之后，也有独立写入点。时序自然正确，不再需要人为选择渲染顺序。

---

## 目标产物

每个 Session（`conversation_id`）一个 md 文件，结构与 V1 相同但**单轮内的段落由 Hook 逐段追加，顺序等于执行顺序**：

````markdown
# Emily 会话归档：李景利

> 会话ID: 123456002  ·  开始: 2026-07-22 23:21:08 (UTC+8)
> 人员: 李景利（工程部经理、甲方代表 · 翠湖地产建设集团 · level 4）
> Session Prompt: session.md (模板 2749 字，变量见快照)

## 会话快照（拉起时）
（与 V1 完全相同，Session 创建时一次性写入）

---
## 第 1 轮 · 23:21:16

### 👤 用户                        ← turn_start 写入
（用户消息原文）

### 🔍 意图识别                    ← _recognize_intent 完成后写入
（sop/意图/置信度 + Prompt 信息 + LLM 调用明细）

### 🔧 意图验证 (Node1)            ← after:wi_node1 ArchiveHook 写入
### 🔧 规划 (Node2)                ← after:wi_node2 ArchiveHook 写入
### 🔧 执行+验收 (Node3)          ← after:wi_node3 ArchiveHook 写入
### 🔧 成果总结 (Node4)            ← after:wi_node4 ArchiveHook 写入
（每段含：阶段结果 + Prompt 信息 + LLM 调用按 call_category 分组）

### ⚠️ 系统审核标记                ← turn_end 写入（如有 Guardian warnings）
### 🤖 Emily                       ← turn_end 写入

---
## 会话归档
- 归档时间/原因/总轮数
````

**与 V1 的关键差异**：
- 单轮内从 3 段（👤 → 🤖 → 🔧）变为 6-7 段，每段对应一个执行阶段
- 写入时机从"整轮完成后一次性渲染"变为"每个阶段完成时实时追加"
- 段落顺序自然等于执行顺序，无需人为选择
- Guardian 审核标记从混入回复正文变为独立段落
- **每个阶段记录 Prompt 注入信息**：模板名 + 渲染后字符数 + 关键变量值摘要。审查者可回溯"模型当时看到了什么"——判断幻觉是否源于上下文不足、变量注入错误或 SOP 文缺失

**Prompt 归档策略**：不写入渲染后 prompt 全文（单次渲染 1500-3500 字，5 次 prompt 合计 6000-15000 字，会严重膨胀归档）。只记录：
1. **模板名**（如 `planner.md`）——可从 `emily-data/prompts/` 找到原文
2. **渲染后字符数**——反映模型实际接收的上下文量
3. **关键变量值摘要**——用 `变量名=值/字数` 格式，每个变量最多 80 字，长值截断并标注字数

全文可从模板 + 变量值完整还原，但归档只记摘要，避免膨胀。

---

## 实施步骤

### 1. 修复 call_category 按阶段正确标注

**目标**：每条 LLM 日志的 `call_category` 准确反映其所属阶段。

**改动文件**：
- [llm_logger.py](emily-core/emily_core/infrastructure/logging/llm_logger.py)
- [bus.py](emily-core/emily_core/workitem/pipeline/bus.py)
- [real_guardian.py](emily-core/emily_core/workitem/pipeline/real_guardian.py)

#### 1a. llm_logger.py — 新增 `set_stage()` / `set_category()` + 改进 fallback

新增两个类方法（仅更新 `_current_context` 中指定字段，不重置其他字段）：

```python
@classmethod
def set_stage(cls, stage: str) -> None:
    """更新当前 pipeline 节点名称（不重置 pipeline_run_id 等其他字段）。"""
    cls._current_context["current_stage"] = stage

@classmethod
def set_category(cls, category: str) -> None:
    """临时 overlay call_category（供 Guardian 等独立调用方使用）。"""
    cls._current_context["call_category"] = category
```

改进 `_on_llm_call_end` 中 fallback 推断（llm_logger.py:59-68）：当 `call_category` 为空时，基于 `current_stage` 映射——`wi_node1→intent`、`wi_node2→planning`、`wi_node3/wi_node4→execution`；其余回退到旧的 `call_type` 推断（含 "intent"/"json" → intent，含 "plan" → planning，否则 execution）。

DB model 已定义合法值：`intent/planning/execution/guardian/compression/consolidation/param_extract`（models.py:1327），无需改动。

#### 1b. bus.py — 节点循环追加 `set_stage`

在 `PipelineBUS.run()` 的节点循环（bus.py:168-169），`context.current_stage = node.name` 之后追加 `LLMInteractionLogger.set_stage(node.name)`。

#### 1c. real_guardian.py — Guardian overlay `call_category="guardian"`

在 `review_step()` 和 `review_reply()` 的 LLM 调用前后，用 `set_category("guardian")` 临时 overlay，`finally` 中恢复 `prev_category`。导入 `LLMInteractionLogger`。

---

### 2. 意图识别 LLM 调用纳入归档 + 各节点 Prompt 信息存储

**目标**：`SessionAgent._recognize_intent()` 的 LLM 调用出现在归档中；节点 handler 在渲染 prompt 后将 Prompt 注入信息存入 `BusContext.baggage`，供 ArchiveHook 读取。

**改动文件**：
- [session_agent.py](emily-core/emily_core/session/session_agent.py)
- [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py)
- [evolution_llm_interaction_repo.py](emily-core/emily_core/repositories/evolution_llm_interaction_repo.py)

#### 2a. session_agent.py — `_recognize_intent` 设置日志 context

在 LLM 调用前设置 context，使用专用 `pipeline_run_id` 标记 + `call_category="intent"`：

```python
intent_run_id = f"intent-{self.conversation_id[:8]}"
LLMInteractionLogger.set_context(
    pipeline_run_id=intent_run_id,
    conversation_id=self.conversation_id,
    user_id=self.context.user_id,
    call_category="intent",
)
try:
    result = await self._llm.chat_messages(full_messages, json_mode=True)
    ...
finally:
    LLMInteractionLogger.clear_context()  # 清除，不干扰后续 BUS pipeline
```

`clear_context()` 在 BUS.run() 之前执行，BUS.run 入口重新 `set_context()`，不存在冲突。

#### 2b. evolution_llm_interaction_repo.py — 新增按 conversation_id 查询

新增 `list_by_conversation_id(conversation_id, *, since="", session=None)`，按 conversation_id 查询 LLM 交互日志（供归档收集意图识别阶段的日志）。DB 已有 `idx_elil_category_created` 索引（models.py:1346），查询效率可接受。

#### 2c. 各节点 Prompt 信息存储到 BusContext.baggage

节点 handler 在渲染 prompt 后，将 Prompt 注入信息存入 `BusContext.baggage`，供 ArchiveHook 读取归档。

**5 个 Prompt 注入点及关键变量**：

| 时序 | 节点 | 模板 | 关键变量 | 存储 baggage key |
|------|------|------|----------|-----------------|
| 1 | SessionAgent | session.md | sop_catalog, current_datetime, user_name, project_name, authorized_node_ids, ... | SessionAgent 层直接写入（不在 BUS 内） |
| 2 | Node2 | planner.md | sop_text, user_input, available_tools | `baggage["prompt_info_node2"]` |
| 3 | Node3 Guardian | guardian_step.md | step_id, output, tool_info, rag_info | `baggage["prompt_info_node3"]` |
| 4 | Node4 | workitem.md | available_tools, sop_text, user_input, step_results, warnings, + Session级变量 | `baggage["prompt_info_node4"]` |
| 5 | Node4 Guardian | guardian_reply.md | draft_reply, user_input, sop_id, steps_summary | `baggage["prompt_info_node4_guardian"]` |

**存储格式**（dict）：

```python
prompt_info = {
    "template": "planner.md",       # 模板名
    "rendered_chars": 1560,         # 渲染后总字符数（模型实际看到的上下文量）
    "variables": {                  # 关键变量值摘要（每个值最多 80 字）
        "sop_text": "847字",        # 长 text 只记字数
        "user_input": "帮我查一下翠湖庭院项目的整体进度情况",  # 短值可完整保留
        "available_tools": "query_data, record_event, ... (6个)",
    },
}
```

**示例（Node2，workitem_agent.py:260-265 渲染 system_prompt 后）**：

```python
context.set("prompt_info_node2", {
    "template": "planner.md",
    "rendered_chars": len(system_prompt),
    "variables": {
        "sop_text": f"{len(sop_text)}字" if len(sop_text) > 80 else sop_text[:80],
        "user_input": wi.user_input[:80],
        "available_tools": f"{len(tool_entries)}个",
    },
})
```

Node3/Node4 同模式：在各自 prompt 渲染后按上表 key 存入 baggage。SessionAgent 意图识别段（时序 1）不在 BUS 内，存到 `self._last_intent_prompt_info` 实例变量，供 `_append_archive_intent` 读取。

**Guardian prompt 长度**：Guardian prompt 在 `RealGuardian._build_step_prompt()` 内构建并直接传给 `chat_json()`，不返回渲染后长度。从 `step_results` 和 GuardianNote 反推关键变量值（step_id、output 字数等），渲染后字符数标注"（未追踪）"。

---

### 3. 新增 ArchiveHook — BUS 逐段追加核心

**目标**：在每个 BUS 节点完成后，实时追加该节点的贡献到归档 md 文件。

**改动文件**：
- [hook.py](emily-core/emily_core/workitem/pipeline/hook.py) — 新增 `ArchiveHook` 类 + `HOOK_TYPE_MAP` 注册
- [bus.py](emily-core/emily_core/workitem/pipeline/bus.py) — `_build_hook_from_spec` 增加 `archive` 分支
- [hook_config.json](emily-data/config/hook_config.json) — 4 个节点追加 ArchiveHook 挂载点
- [__init__.py](emily-core/emily_core/__init__.py) — `_collect_injected_services` 注入 `archive_writer`

#### 3a. hook.py — 新增 ArchiveHook

`ArchiveHook(Hook)` dataclass，字段 `archive_writer: Any = None`（SessionArchiveWriter 实例，注入）。`execute(context)` 行为：

1. 若 `archive_writer` 为 None / 未 enabled / `baggage["archive_md_path"]` 为空 → 直接 `HookResult.allow()`
2. 查询本节点 LLM 日志：`EvolutionLLMInteractionRepo.list_by_pipeline_run_ids([run_id])`（`asyncio.to_thread` 包裹）
3. 按 `call_category` 过滤：node2→planning，node3→execution+guardian，node4→execution+guardian，避免重复写入已记录的日志
4. 从 `baggage[f"prompt_info_{stage}"]` 读取 Prompt 信息（node4 额外读 `prompt_info_node4_guardian`）
5. 调 `archive_writer.render_node_section(...)` + `archive_writer.append_section(path, content)`
6. 异常非阻断（`logger.warning` + `HookResult.allow()`，与 AuditHook 同原则）

**关键设计点**：
- after 阶段 fire-and-forget，异常不阻断
- `archive_writer` 通过 `_collect_injected_services` 注入，与 ProgressHook 的 `progress_sender` 注入模式一致
- 归档文件路径通过 `context.baggage["archive_md_path"]` 跨节点传递，由 SessionAgent 在 turn_start 时设置

#### 3b. bus.py — `_build_hook_from_spec` 增加 archive 分支

`elif hook_type == "archive": if "archive_writer" in injected_services: kwargs["archive_writer"] = injected_services["archive_writer"]`

#### 3c. hook_config.json — 4 个节点追加 ArchiveHook

在 `after:wi_node1/2/3/4` 数组末尾各追加一项：`{ "type": "archive", "name": "archive.nodeN", "enabled": true }`。ArchiveHook 排在 AuditHook 之后（priority 默认 10，按注册顺序执行）。

#### 3d. __init__.py — `_collect_injected_services` 注入 archive_writer

`if self._session_archive_writer is not None: injected["archive_writer"] = self._session_archive_writer`

---

### 4. SessionArchiveWriter 扩展 — 逐段追加方法

**目标**：从"一次性渲染整轮"扩展为"逐段追加"，同时保留 V1 的 header/footer 方法不变。

**改动文件**：
- [session_archive_writer.py](emily-core/emily_core/services/session_archive_writer.py)

#### 4a. 新增渲染方法（纯函数，无 I/O）

- `render_turn_start(turn_idx, user_message, turn_time="") -> str` — 渲染轮次开头：`## 第 N 轮` 标题 + `### 👤 用户` 段
- `render_intent_section(intent_data, llm_logs, prompt_info=None) -> str` — 渲染意图识别段：sop/意图/置信度 + Prompt 信息 + LLM 调用明细
- `render_node_section(node_name, work_item, llm_logs, prompt_info=None, prompt_info_guardian=None) -> str` — 渲染单个 BUS 节点段落。node1→路由确认，node2→风险等级+步骤，node3→工具调用+Guardian 并进审核，node4→回复合成+Guardian 出站审核。LLM 调用按 `call_category` 分组（intent/planning/execution/guardian）
- `render_turn_end(reply_body, guardian_warnings="") -> str` — 渲染轮次结尾：⚠️ 系统审核标记（如有）+ 🤖 Emily 回复 + 分隔线
- `_split_reply_and_warnings(reply_content) -> tuple[str, str]` — 将 reply_content 中的 Guardian warning 段分离（marker: `\n\n⚠️ Emily 提醒`）

#### 4b. `_render_prompt_info` 格式 spec

```python
@staticmethod
def _render_prompt_info(prompt_info: dict) -> list[str]:
    """渲染 Prompt 注入信息段落。

    格式：
      - Prompt: planner.md (渲染后 1560 字)
        - 关键变量: sop_text=847字 · user_input="帮我查..." · available_tools=6个
    """
```

- 模板名 + 渲染后字符数（0 显示"（未追踪）"）
- 关键变量用 `变量名=值` 格式，每个值最多 80 字，超长截断 + "..."
- `session_vars` 子字典单独展开为 "Session级: ..." 段

#### 4c. 新增 I/O 方法

`append_section(path, content) -> bool`：`open(path, "a", encoding="utf-8")` 追加写入，`OSError` 警告并返回 False。与 `EventJournal.append()` 同模式。

#### 4d. 保留但弃用 `_render_turn` / `append_turn`

V1 方法保留标记弃用，新流程不再调用。

---

### 5. SessionAgent 归档流程重构

**目标**：从"整轮完成后调用 `_append_archive_turn()`"改为"分阶段调用 `append_section()`"。

**改动文件**：
- [session_agent.py](emily-core/emily_core/session/session_agent.py)

#### 5a. turn_start — 写入轮次开头 + 用户消息

`handle()` 中 `_record_turn` 之前调 `_append_archive_turn_start(message)`：`_turn_counter += 1`，调 `render_turn_start` + `append_section`。

#### 5b. 意图识别 — 写入意图识别段

`_split_into_workitems` 中 `_recognize_intent` 完成后调 `_append_archive_intent(intent, message)`（async）：用 `intent_run_id = f"intent-{self.conversation_id[:8]}"` 查 LLM 日志，读取 `self._last_intent_prompt_info`（2c 中暂存），调 `render_intent_section` + `append_section`。

#### 5c. BUS.run 传入 archive context

`_handle_impl` 创建 BusContext 后，若 `self._archive_md_path`：`context.baggage["archive_md_path"] = self._archive_md_path` + `context.baggage["archive_turn_idx"] = self._turn_counter`。ArchiveHook 从 baggage 读路径。

#### 5d. turn_end — 写入审核标记 + Emily 回复

`handle()` 中 BUS 完成后调 `_append_archive_turn_end(reply)`：`_split_reply_and_warnings` 拆分，调 `render_turn_end` + `append_section`。

#### 5e. 移除旧 `_append_archive_turn`

各段已由 turn_start / intent / ArchiveHook / turn_end 分阶段写入，旧方法弃用或删除。

---

### 6. 需求文档更新

[需求文件/Session归档.md](需求/需求文件/Session归档.md) 标注 V1 已弃用，指向 V2 文档。

---

## 涉及文件清单

| 文件 | 改动类型 | 改动说明 |
|------|----------|----------|
| `emily-core/emily_core/infrastructure/logging/llm_logger.py` | 改 | 新增 `set_stage()` / `set_category()`，改进 fallback 推断 |
| `emily-core/emily_core/workitem/pipeline/bus.py` | 改 | 节点循环追加 `set_stage`，`_build_hook_from_spec` 增加 `archive` 分支 |
| `emily-core/emily_core/workitem/pipeline/hook.py` | 改 | 新增 `ArchiveHook` 类 + `HOOK_TYPE_MAP` 注册 |
| `emily-core/emily_core/workitem/pipeline/real_guardian.py` | 改 | LLM 调用前后 overlay `call_category="guardian"` |
| `emily-data/config/hook_config.json` | 改 | 4 个 after 挂载点追加 ArchiveHook |
| `emily-core/emily_core/__init__.py` | 改 | `_collect_injected_services` 注入 `archive_writer` |
| `emily-core/emily_core/services/session_archive_writer.py` | 改 | 新增逐段渲染方法 + `append_section`；弃用 `_render_turn` / `append_turn` |
| `emily-core/emily_core/session/session_agent.py` | 改 | 归档流程重构：turn_start / intent / turn_end 分阶段写入；BusContext 注入 archive_md_path；弃用 `_append_archive_turn` |
| `emily-core/emily_core/workitem/workitem_agent.py` | 改 | node2/node3/node4 在 prompt 渲染后存储 `prompt_info` 到 `BusContext.baggage` |
| `emily-core/emily_core/repositories/evolution_llm_interaction_repo.py` | 改 | 新增 `list_by_conversation_id` 方法 |
| `需求/需求文件/Session归档.md` | 改 | 标注 V1 已弃用，指向 V2 |

---

## 实施顺序与依赖

1. **步骤 1**（call_category）— 最底层，无前置依赖，优先实施
2. **步骤 2**（意图识别 LLM 纳入 + 各节点 Prompt 信息存储）— 依赖步骤 1
3. **步骤 3**（ArchiveHook）— 依赖步骤 1 + 2（Hook 按call_category 过滤日志，从 baggage 读 prompt_info）
4. **步骤 4**（SessionArchiveWriter 扩展）— 依赖步骤 3（Writer 新增逐段渲染方法）
5. **步骤 5**（SessionAgent 重构）— 依赖步骤 4
6. **步骤 6**（需求文档）— 全部完成后同步更新

---

## 验证

1. **单元测试**：
   - `render_turn_start` / `render_intent_section` / `render_node_section` / `render_turn_end` 对各种输入（空/有值/异常）的输出正确
   - `_split_reply_and_warnings` 对含/不含 Guardian warning 的 reply 正确拆分
   - ArchiveHook.execute() 对无 writer / 无 path / DB 查询失败等情况的 fail-open 行为

2. **call_category 验证**：用 emy-test 发消息后，查询 DB：
   ```sql
   SELECT call_category, call_sequence, response_summary
   FROM evolution_llm_interaction_logs
   WHERE pipeline_run_id IN ('...', 'intent-...')
   ORDER BY call_sequence;
   ```
   确认 category 包含 intent/planning/execution/guardian 而非全标 intent

3. **端到端验证**（emy-test）：
   ```powershell
   docker exec emily-postgres psql -U emily -d emily -c "SELECT id,username,permission_level FROM users WHERE status='active' LIMIT 5;"
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目的整体进度情况" --sender "李景利"
   ```
   发消息后立即检查归档 md 文件，确认每个 node 段内 LLM 调用按 `call_category` 分组、意图识别段包含 intent LLM 调用、每段含 Prompt 注入信息。

4. **fail-open 验证**：临时禁用 `session_archive_enabled`，确认 Agent 正常回复、归档不写入

5. **崩溃容错验证**：模拟 node3 异常，确认 node1/node2 的段落已写入归档文件（不会丢失）

---

## 潜在风险与缓解

| 风险 | 缓解 |
|------|------|
| `_current_context` 类级字典在 asyncio 并发下被交错修改 | asyncio 单线程协作式并发，trace callback 在 await 恢复帧内同步执行，实际风险极低 |
| 归档文件路径通过 `baggage` 传递，多 WorkItem 同会话时可能冲突 | 当前架构每轮只创建一个 BusContext（一个 WorkItem），不会有多 WorkItem 并发写入同一文件的情况 |
| 已有 V1 归档文件格式不兼容 | V2 只影响新写入的 turn，已有文件不受影响。旧文件保留原格式 |
| node handler 存 prompt_info 到 baggage 可能遗漏（某些路径如 Skill 执行不调 LLM） | Skill 执行路径不产生 LLM 规划调用，prompt_info 为 None 或标注 "（Skill 定义，无 LLM 规划调用）"，ArchiveHook 对 None 做跳过处理 |

---

## 与 V1 的对比

| 维度 | V1（事后批量渲染） | V2（Hook 逐段追加） |
|------|---------------------|----------------------|
| 写入时机 | 整轮完成后一次性写入 | 每阶段完成时实时追加 |
| 段落顺序 | 人为选择（硬编码 lines 列表） | 自然正确（执行顺序 = 写入顺序） |
| 意图识别覆盖 | 天然缺位（不在 pipeline_run_id 内） | 独立写入点，自然覆盖 |
| call_category | 全标 intent（fallback 误判） | 按节点阶段正确标注 |
| 崩溃容错 | 中途丢失整轮 | 已写入的段落保留 |
| 架构一致性 | 旁路后补（_append_archive_turn） | 与 BUS Hook 体系一致 |
| Prompt 可见性 | 只记模板名+字符数（header 一行） | 每阶段记录模板名+渲染后字符数+关键变量摘要 |
| Guardian 标记 | 混入回复正文 | 独立 ⚠️ 系统审核标记段落 |
