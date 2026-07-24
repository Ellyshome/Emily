# Session 归档"看起来奇怪"的诊断报告

## Context

用户观察归档文件 `emily-data/session_archives/2026-07-23_黄志强_12345601.md` 后产生三个疑惑：
1. 没有 LLM 的思考内容
2. 没有工具调用的实际执行命令（参数显示 `{}`）
3. 工具返回有问题的信息，agent 没有错误尝试（Guardian 标了幻觉，但 Emily 照样编造回复）

本报告定位三个现象的根因，区分"设计意图"与"实现 bug"。本次任务为**诊断分析**，是否修复由用户决定。

---

## 诊断结论总览

| # | 现象 | 根因类型 | 根因 |
|---|------|---------|------|
| 1 | LLM 调用行 model 显示 `?`、无 `[json]` 标记、无思考内容 | **bug + 设计** | end 阶段 trace data 未携带 model/json_mode；思考链(reasoning_content)从未持久化；archive 设计上只记摘要 |
| 2 | 工具调用 `参数: {}` | **bug** | archive writer 读 `tc.tool_arguments`，但 `ToolCallRecord` 字段名是 `tool_input` |
| 3 | Guardian 标幻觉后 agent 不纠正 | **设计** | RealGuardian 设计为"只标记不拦截"，无重试/纠正闭环 |

---

## 问题 1：没有 LLM 的思考内容

### 1a. model 显示 `?`、缺 `[json]` 标记 —— 实现 bug

**证据链：**

- archive 渲染格式 [session_archive_writer.py:464](emily-core/emily_core/services/session_archive_writer.py#L464)：`f"  - #{idx} {category} {mode_tag} {model} · {latency}ms · {tokens} tok"`
- archive 实际输出 `#1 intent  ? · 759ms · 4578 tok` → 解析得 `category=intent`、`mode_tag=""`、`model="?"`
- 即 `log.model=""` 且 `log.json_mode=False`
- 日志写入 [llm_logger.py:108-111](emily-core/emily_core/infrastructure/logging/llm_logger.py#L108-L111)：`model=data.get("model", "")`、`json_mode=data.get("json_mode", False)`
- 日志只在 end 阶段写入 [llm_logger.py:65-67](emily-core/emily_core/infrastructure/logging/llm_logger.py#L65-L67)：`if data.get("phase") != "end": return`
- **client.py 的 trace callback**：
  - start 阶段 [client.py:97-105](emily-core/emily_core/infrastructure/llm/client.py#L97-L105) 传了 `model`、`json_mode` ✓
  - end 阶段（普通分支 [client.py:190-199](emily-core/emily_core/infrastructure/llm/client.py#L190-L199) / tool_call 分支 [client.py:160-170](emily-core/emily_core/infrastructure/llm/client.py#L160-L170)）**没有传 `model`、`json_mode`** ✗

**根因：client.py end 阶段的 trace data dict 漏传 `model` 和 `json_mode`，导致写入 DB 的日志这两个字段为空，archive 渲染回退为 `?` / 无 `[json]`。**

### 1b. 没有完整 prompt / 回复 / 思考链 —— 设计意图

- archive 的 LLM 调用明细只显示 `response_summary`（前 200 字），见 [session_archive_writer.py:466](emily-core/emily_core/services/session_archive_writer.py#L466)
- `_render_snapshot` 注释明确 [session_archive_writer.py:124-127](emily-core/emily_core/services/session_archive_writer.py#L124-L127)："大文本字段只显示有+字符数，不写全文（避免归档正文膨胀；全文可回查 DB）"
- DeepSeek 的 `reasoning_content`（思考链）在 [client.py:145](emily-core/emily_core/infrastructure/llm/client.py#L145) 被读取，但**只返回给调用者**（且仅 tool_call 分支返回，普通分支丢弃），**从未写入 trace data / 日志表** —— 思考内容根本没被持久化，无法回查

---

## 问题 2：没有工具调用的实际执行命令（参数 `{}`）

### 根因：archive writer 读错字段名

**证据链：**

- `ToolCallRecord` 定义 [execution.py:28-32](emily-core/emily_core/workitem/pipeline/interfaces/execution.py#L28-L32)：字段是 **`tool_input`**（不是 `tool_arguments`）
- `_real_execute` 写入 [workitem_agent.py:529](emily-core/emily_core/workitem/workitem_agent.py#L529)：`tool_input=tool_params` ✓
- `SkillExecutor` 写入 [executor.py:132](emily-core/emily_core/skill/executor.py#L132)：`tool_input=tool_params` ✓
- **archive writer 读取** [session_archive_writer.py:393](emily-core/emily_core/services/session_archive_writer.py#L393) 和 [session_archive_writer.py:589](emily-core/emily_core/services/session_archive_writer.py#L589)：
  ```python
  args = getattr(tc, "tool_arguments", "{}") or "{}"
  ```
  读的是 `tool_arguments` —— **ToolCallRecord 没有这个字段**，getattr 永远返回默认值 `"{}"`

**根因：字段名不匹配。工具实际收到了参数（在 `tc.tool_input` 里，含 query_type + 注入的 _user_id/_message_id/_session_scope 等），但 archive 渲染读 `tool_arguments` 取不到，显示空 `{}`。**

> `tool_arguments` 这个名字来自 client.py tool_call 返回值 [client.py:177](emily-core/emily_core/infrastructure/llm/client.py#L177)（LLM function-calling 的产物），与 `ToolCallRecord` 无关，疑似作者混淆。

### 附带现象：第 1、2 轮"工具调用:" 段完全为空

- 第 1、2 轮走 skill_definition 路径（SOP-005-QRY.skill.yaml），step-01 的 query_type 是 `source: user_input` + `required: true`
- `ParamExtractor._extract_from_user_input` [param_extractor.py:120-160](emily-core/emily_core/skill/param_extractor.py#L120-L160) 调 LLM 提取，若返回 `{"value": null}` 或提取值不在 enum，且 required → `raise ValueError` [param_extractor.py:60-64](emily-core/emily_core/skill/param_extractor.py#L60-L64)
- `SkillExecutor` 捕获异常 [executor.py:164-170](emily-core/emily_core/skill/executor.py#L164-L170)，返回 `StepResult(success=False, output="步骤执行异常: ...")`，**无 tool_calls**，然后 break
- 这与第 1 轮 node4 回复"缺少必要的查询类型参数"互相印证 —— query_type 提取失败导致 step-01 中断，未产生工具调用记录

---

## 问题 3：工具返回有问题的信息，agent 没有错误尝试

### 3a. Guardian 只标记不拦截 —— 设计意图

- `RealGuardian` 类文档 [real_guardian.py:56-63](emily-core/emily_core/workitem/pipeline/real_guardian.py#L56-L63)："轻量输出审核 —— 单次 LLM chat_json，**只标记不拦截**"
- node4_summary [workitem_agent.py:630-669](emily-core/emily_core/workitem/workitem_agent.py#L630-L669)：`review_reply` 发现幻觉后只 `wi.add_warning(f"[reply] {issue}")`，然后 `wi.result_text = draft + warning_text` —— **把警告追加到编造回复的末尾，不重新合成、不重试、不纠正**
- 第 3 轮正是这个路径：Emily 编造"您当前在「翠湖庭院」项目的「施工建设」阶段..."，Guardian 标记幻觉，最终以"编造回复 + ⚠️ 系统审核标记"一起发出

**根因：Guardian 被设计为事后标记器，不是闭环纠正器。发现幻觉不会触发重新规划/重新合成。**

### 3b. 第 3 轮 query_type="node" 是无效查询类型 —— 加剧幻觉的输入缺陷

- 第 3 轮 LLM 规划（llm_planner 路径）生成 `query_type: "node"`
- 但 `_QUERY_TYPE_TO_TABLE` [query_tool.py:94-99](emily-core/emily_core/tools/query_tool.py#L94-L99) 只有 event/task/meeting/file/message/conversation/user/project/journal/summary，**没有 "node"**
- `handle_query_data` [query_tool.py:112](emily-core/emily_core/tools/query_tool.py#L112)：`query_type = params.get("query_type", "event")` —— "node" 不等于 "event"，不会被回退，保持 "node" 进入 QueryService，返回空/无效结果（output=11字）
- **为什么 LLM 会编 "node"**：node2 `_llm_plan` 给 LLM 的 tools_text 只有 `- {name}: {description}` [workitem_agent.py:264-269](emily-core/emily_core/workitem/workitem_agent.py#L264-L269)，**没传 `tool.parameters`（含 query_type enum）**，LLM 不知道合法值。对比 `SkillExecutor._llm_resolve_params` [executor.py:225](emily-core/emily_core/skill/executor.py#L225) 会传 schema，但 node2 规划没传

### 3c. 工具空结果 + LLM 合成 → 幻觉

- node3 工具返回 11 字的空/无效结果，step_results 几乎无可用信息
- node4 `_llm_synthesize_reply` [workitem_agent.py:680-814](emily-core/emily_core/workitem/workitem_agent.py#L680-L814) 用 workitem.md prompt 合成回复，LLM 在缺乏真实数据时基于 user_input（"我现在在什么节点里？"）和 session 级变量（project_name=翠湖庭院）**编造**了具体节点
- Guardian 识别出编造但只标记不拦截 → 最终发出编造内容

---

## 可选修复方案（按风险/收益排序）

### 修复 A：archive writer 字段名（问题 2，低风险高收益）

[session_archive_writer.py:393](emily-core/emily_core/services/session_archive_writer.py#L393) 和 [session_archive_writer.py:589](emily-core/emily_core/services/session_archive_writer.py#L589) 两处 `tool_arguments` → `tool_input`。修复后参数正常显示。纯渲染层改动，无副作用。

### 修复 B：client.py end 阶段补传 model/json_mode（问题 1a，低风险）

[client.py:160-170](emily-core/emily_core/infrastructure/llm/client.py#L160-L170) 和 [client.py:190-199](emily-core/emily_core/infrastructure/llm/client.py#L190-L199) 两处 end 阶段 trace data 补 `"model": self.model, "json_mode": json_mode`。修复后 archive 显示真实 model 名 + `[json]` 标记。

### 修复 C：node2 规划注入工具 schema（问题 3b，中风险）

[workitem_agent.py:264-269](emily-core/emily_core/workitem/workitem_agent.py#L264-L269) 的 tools_text 增加 `tool.parameters` 摘要（至少含 required 字段和 enum），让 LLM 规划时知道合法 query_type。避免编造 "node" 这类无效值。

### 修复 D：Guardian 闭环纠正（问题 3a，高风险，设计变更）

node4 检测到 reply 幻觉时，触发一次重新合成（把 Guardian issues 注入 prompt 让 LLM 自我纠正）。这是设计层面的变更，需评估延迟/成本/死循环风险。**不建议本次一并做**，宜单独立项。

### 修复 E：思考链持久化（问题 1b，中风险）

client.py 把 `reasoning_content` 纳入 trace data 的 response_summary 或单独字段，llm_logger 写入日志表，archive 可选展示。需确认 DeepSeek reasoning_content 是否稳定返回 + token 成本。

---

## 验证方式

- 修复 A/B 后：用 emy-test 发一条查询消息，重新触发归档，检查新归档文件 LLM 调用行是否显示真实 model + `[json]`，工具调用行是否显示真实参数
- 修复 C 后：发"我现在在什么节点里？"，检查 node2 规划的 tool_params.query_type 是否落在合法 enum 内
- 命令：`uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我现在在什么节点里？" --sender "黄志强"`（先用 psql 确认 users 表有该用户）

---

## 执行记录（2026-07-23）

### 已执行修复

#### 修复 A：archive writer 字段名 ✅

**文件**: [session_archive_writer.py](file:///d:/app/Emily/emily-core/emily_core/services/session_archive_writer.py#L393)

两处 `tool_arguments` → `tool_input`（第393行 `_render_skill_snapshot`、第589行 `_render_wi_snapshot`）。

#### 修复 B：client.py end 阶段补传 model/json_mode ✅

**文件**: [client.py](file:///d:/app/Emily/emily-core/emily_core/infrastructure/llm/client.py#L163)

两处 end 阶段 trace data 补传 `"model": self.model, "json_mode": json_mode`（第163行 tool_call 分支、第193行 text/json 分支）。

#### 修复 C：node2 规划注入工具 schema ✅

**文件**: [workitem_agent.py](file:///d:/app/Emily/emily-core/emily_core/workitem/workitem_agent.py#L61)

- 新增 `_build_params_summary()` 函数（第61-84行），从工具 JSON Schema 提取 required 标记和 enum 约束
- node2 `_llm_plan` 的 tools_text 注入参数摘要（第269行），输出示例：
  ```
  参数: query_type*(event|task|meeting|file|message|conversation|user|project|journal|summary), time_range(today|this_week|this_month|all)
  ```

### 未执行的修复

| 修复 | 原因 |
|------|------|
| D (Guardian 闭环纠正) | 高风险设计变更，计划明确建议单独立项 |
| E (思考链持久化) | 中风险，需先确认 DeepSeek reasoning_content 稳定性 |

### 验收结果：15/15 全部通过

**修复 A 验证（2项）**
- archive_writer 无 `tool_arguments` 残留
- 2 处 `tool_input` getattr 已就位

**修复 B 验证（3项）**
- tool_call 分支 end phase 含 `model` + `json_mode`
- text 分支 end phase 含 `model` + `json_mode`

**修复 C 验证（10项）**
- 真实 query_tool schema：`query_type*` 正确标记 required，含完整合法枚举值
- `time_range` 无 `*`（非 required），枚举正确
- 空 schema、None、无 enum 属性等边界情况全部正确处理

### 待系统级验证

需启动完整系统后运行：

```
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我现在在什么节点里？" --sender "黄志强"
```

检查：
- 新归档文件 LLM 调用行是否显示真实 model 名 + `[json]` 标记
- 工具调用行是否显示真实参数（不再 `{}`）
- node2 规划的 `query_type` 是否落在合法 enum 内
