# Session-WorkItem Agent Harness 补齐计划

## Context（为什么做）

当前 session-workitem 流是「一次性直线管道 + fail-open 降级」模型，缺少优秀 Agent harness 的两个核心机制：

1. **错误反馈重试**：工具/参数失败时，没有把错误信息反馈给 LLM 重新调参/重试
2. **审核修正循环**：Guardian 发现 issues 后，没有驱动 LLM 重新合成再审核的循环

### 现状证据

| 位置 | 现状 |
|------|------|
| [workitem_agent.py:667-668](emily-core/emily_core/workitem/workitem_agent.py#L667-L668) `_real_execute` | `if not sr.success: break` —— 工具失败即停止 |
| [executor.py:183-184](emily-core/emily_core/skill/executor.py#L183-L184) `SkillExecutor.execute` | 同样 `if not sr.success: break` |
| [param_extractor.py:60-64](emily-core/emily_core/skill/param_extractor.py#L60-L64) | 必填失败 `raise ValueError`；LLM 失败 `return mapping.default` |
| [real_guardian.py:57](emily-core/emily_core/workitem/pipeline/real_guardian.py#L57) | 类注释明确「只标记不拦截」 |
| [workitem_agent.py:730-736](emily-core/emily_core/workitem/workitem_agent.py#L730-L736) | Guardian issues 只追加到回复末尾 |
| [hook_config.json](emily-data/config/hook_config.json) | `on_error:wi_nodeN` 全是 audit，无 recovery/retry hook |
| [workitem_state.py:30-41](emily-core/emily_core/workitem/workitem_state.py#L30-L41) | FAILED 是终态，无 replan/retry 状态 |

### 用户决策（已确认）

- **范围**：两者都补（错误反馈重试 + 审核修正循环）
- **失败兜底**：统一升级为 FAILED —— 重试/修正耗尽 → WorkItem 进入 FAILED 终态，向用户明确报错，不再静默降级

### 设计原则

1. **区分两层 fail-open**（关键）：
   - **基础设施层**（LLM 完全不可用）：保留降级（CLAUDE.md 硬约束 9「回退链保留」）—— `harness_fail_open_on_llm_unavailable=True` 时仍走现有 fallback
   - **业务层**（LLM 可用但工具/审核失败）：重试/修正耗尽 → FAILED
2. **风险分级重试**：L1/L2 失败重试；L3（discard/delete）失败直接 FAILED（避免重复删除副作用）；权限失败直接 FAILED（重试不会改变权限）
3. **状态机不扩展**：重试/修正都在节点内部循环，不跨节点，复用现有 `EXECUTING → FAILED` 转换
4. **不改已有签名**：仅新增可选参数和新方法（CLAUDE.md 硬约束 1）
5. **配置驱动**：重试上限、修正轮数、可重试风险等级均走 Config

---

## 模块改动清单

| 模块 | 文件 | 改动 |
|------|------|------|
| M1 | [config.py](emily-core/emily_core/config.py) | 新增 4 个 harness 配置字段 |
| M2 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | `_real_execute` step 失败重试；新增 `_retry_step_with_error_feedback` |
| M2 | [executor.py](emily-core/emily_core/skill/executor.py) | `execute` step 失败重试；新增 `_retry_step` |
| M3 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | `node4_summary` 审核修正循环；新增 `_revise_reply_loop` |
| M3 | [real_guardian.py](emily-core/emily_core/workitem/pipeline/real_guardian.py) | 新增 `revise_reply` 方法 |
| M4 | [workitem_agent.py](emily-core/emily_core/workitem/workitem_agent.py) | node3/node4 失败传播：设 `context.should_abort` + `wi.error_message` + 有意义 `result_text` |
| M5 | `emily-data/prompts/step_retry.md` | 新增：错误反馈重试 prompt |
| M5 | `emily-data/prompts/reply_revise.md` | 新增：审核修正 prompt |

---

## M1: Config 扩展

在 [config.py](emily-core/emily_core/config.py) 的 `llm_*` 字段后追加：

```python
    # ── Agent Harness（错误反馈重试 + 审核修正循环）──
    harness_step_max_retries: int = 1
    """单步工具调用失败后的错误反馈重试上限（L1/L2 适用，L3/权限失败不重试）"""

    harness_reply_max_revise_rounds: int = 1
    """node4 reply 审核修正循环上限（Guardian 发现 issues 后驱动 LLM 重新合成）"""

    harness_retryable_risk_levels: list = field(default_factory=lambda: ["L1", "L2"])
    """允许自动错误反馈重试的风险等级；L3 默认不重试（避免重复删除等副作用）"""

    harness_fail_open_on_llm_unavailable: bool = True
    """LLM 完全不可用时是否保留现有降级（基础设施层 fail-open，CLAUDE.md 硬约束 9）"""
```

> 注意：Config 是否用 `field` 需看现有 dataclass 风格——若 config.py 已 `from dataclasses import dataclass, field` 则直接用；否则用 `list = ...` 默认值需可变默认值警告，统一用 `field(default_factory=...)`。

---

## M2: 错误反馈重试（node3 step 级）

### 触发点

两条执行路径都需要：
- [workitem_agent.py:501](emily-core/emily_core/workitem/workitem_agent.py#L501) `_real_execute`（RealExecutor 路径）
- [executor.py:47](emily-core/emily_core/skill/executor.py#L47) `SkillExecutor.execute`（Skill 路径）

### 机制

step 失败（`sr.success == False`）时，**不立即 break**，改为：

1. 判定是否可重试：
   - 风险等级 ∈ `harness_retryable_risk_levels`（L3 不可重试）
   - 失败类型 ≠ 权限失败（`session_api_ids` 不含 / AuthHook BLOCK 不可重试）
   - LLM 可用（否则走基础设施 fail-open）
2. 可重试 → 调 `_retry_step_with_error_feedback`：
   - 把 `错误信息 + 原 tool_params + 工具 schema + 用户输入` 喂给 LLM
   - LLM 重新推导 `tool_params`（`chat_json` 返回新参数）
   - 用新参数重试 `tool.handler`
3. 重试成功 → 正常 StepResult；重试失败 → StepResult(success=False) + 累计重试次数
4. 重试耗尽 → `break`，由 M4 传播为 FAILED

### 不可重试场景（直接 break → FAILED）

- L3 工具失败（`discard_nodes` / `return_node_deliverable`）
- 权限失败（`tool_name not in session_api_ids`）
- LLM 不可用（走 fail-open 降级，不重试）

### 新增方法（workitem_agent.py）

```python
async def _retry_step_with_error_feedback(
    self, step, tool, failed_params: dict, error_info: str, context: "BusContext",
) -> tuple[dict, str]:
    """错误反馈重试：把失败信息喂给 LLM 重新推导参数。

    Returns: (new_tool_params, retry_reason) —— retry_reason 为空表示 LLM
    推导失败（调用方应视为重试不可用）。
    """
    # 加载 step_retry.md prompt，format 错误信息 + 工具 schema + 用户输入
    # 调 self._llm.chat_json(prompt, user_message) 返回新参数 dict
    # 失败（LLM 异常 / 返回非 dict）→ return ({}, "llm_unavailable")
```

### Skill 路径（executor.py）

`SkillExecutor.execute` 内 step 失败时，调用方（WorkItemAgent._execute_skill）需感知失败并触发重试。两种实现：
- **方案 A（推荐）**：在 `executor.py` 内部加重试循环（step 失败 → 调注入的 `retry_callback` 或内置 ParamExtractor 重新推导）
- **方案 B**：`_execute_skill` 拿到 step_results 后，对失败 step 逐个重试

推荐方案 A，但 SkillExecutor 目前无 LLM 重试能力，需给 `SkillExecutionContext` 加 `retry_callback: Callable | None = None` 字段，由 WorkItemAgent 注入 `_retry_step_with_error_feedback`。

### 关键约束

- 重试不破坏 [executor.py:183](emily-core/emily_core/skill/executor.py#L183) 现有 `break` 语义——重试耗尽仍 break
- 重试次数记入 `wi.llm_call_count`
- 重试产生的 LLM 调用走 `LLMInteractionLogger`，`call_category="retry"`

---

## M3: 审核修正循环（node4 reply 级）

### 触发点

[workitem_agent.py:697-704](emily-core/emily_core/workitem/workitem_agent.py#L697-L704) `node4_summary` 里 `review_reply` 返回 issues 非空时。

### 机制

```python
# node4_summary 改造（伪代码）
draft = await self._llm_synthesize_reply(wi, ...)

if self._guardian and should_review_reply:
    for round_idx in range(self._config.harness_reply_max_revise_rounds + 1):
        note = await self._guardian.review_reply(draft, wi)
        if not note or not note.issues:
            break  # 审核通过
        # 修正：把 issues 反馈给 LLM 重新合成
        revised = await self._guardian.revise_reply(draft, note.issues, wi)
        if revised and len(revised) > 20:
            draft = revised
        else:
            break  # 修正失败
    
    # 循环结束仍有 issues → 按用户选择"统一 FAILED"
    if note and note.issues:
        wi.error_message = f"审核修正失败: {'; '.join(note.issues)}"
        wi.result_text = self._build_failed_reply(wi, note.issues)
        context.should_abort = True  # 触发 scheduler FAILED 转换
        return
```

### 新增方法（real_guardian.py）

```python
async def revise_reply(self, draft_reply: str, issues: list[str], work_item: Any) -> str | None:
    """根据 Guardian issues 驱动 LLM 重新合成 reply。
    
    Returns: 修正后的 reply 文本，或 None（LLM 不可用/失败）。
    """
    # 加载 reply_revise.md prompt，format draft + issues + 原始上下文
    # 调 chat_json 返回 {"reply": "..."}
```

### 失败兜底（M4 联动）

按用户「统一升级为 FAILED」：
- 审核修正循环耗尽仍有 issues → `wi.error_message` 记录 issues → `context.should_abort=True`
- `wi.result_text` 设为**有意义的错误回复**（不是空），让 SessionAgent 汇总时用户能看到：
  ```python
  def _build_failed_reply(self, wi, issues) -> str:
      return (
          f"⚠️ Emily 在处理「{wi.user_input[:50]}」时未能通过系统审核：\n"
          + "\n".join(f"  • {i}" for i in issues[:3])
          + "\n\n请换种说法或补充信息后重试。"
      )
  ```

### 关键约束

- L1 查询类仍跳过审核（M3 既有逻辑，[workitem_agent.py:695-696](emily-core/emily_core/workitem/workitem_agent.py#L695-L696)）—— 修正循环只在 L2/L3 跑
- `revise_reply` 失败（LLM 异常）→ 不进入 FAILED，保留原 draft + warnings（避免 LLM 抖动导致误杀）
- 修正产生的 LLM 调用 `call_category="revise"`

---

## M4: 状态转换与错误传播

### scheduler 已有逻辑（复用，不改）

[scheduler.py:133-147](emily-core/emily_core/workitem/scheduler.py#L133-L147) 已正确处理 `context.should_abort` → `wi.transition_to(FAILED)` + `wi.error_message`。M2/M3 只需正确设置 `context.should_abort` 和 `wi.error_message`。

### node3 失败传播（workitem_agent.py）

[node3_execute](emily-core/emily_core/workitem/workitem_agent.py#L409) 末尾，若任一 step 重试耗尽仍失败：

```python
# node3_execute 末尾追加
if any(not getattr(sr, "success", True) for sr in step_results):
    failed_step = next(sr for sr in step_results if not getattr(sr, "success", True))
    wi.error_message = f"步骤 {failed_step.step_id} 执行失败: {failed_step.output[:200]}"
    context.should_abort = True
    wi.result_text = self._build_failed_reply(wi, [failed_step.output[:100]])
    return  # 跳过 Guardian 审核（已失败）
```

### node4 失败传播

见 M3 伪代码，审核修正失败设 `context.should_abort=True`。

### FAILED 时 result_text 兜底

[scheduler.py:138-148](emily-core/emily_core/workitem/scheduler.py#L138-L148) 失败分支需确保 `wi.result_text` 非空（否则 SessionAgent 汇总回"Emily 已处理完毕。"误导用户）。在 scheduler FAILED 分支补：
```python
if not wi.result_text:
    wi.result_text = f"处理失败：{wi.error_message or '未知原因'}"
```

---

## M5: Prompt 模板

### `emily-data/prompts/step_retry.md`（新增）

错误反馈重试用 system prompt。变量：`{tool_name}` `{tool_description}` `{tool_parameters}` `{failed_params}` `{error_info}` `{user_input}`。指令：根据错误信息重新推导工具参数，只返回 JSON。

### `emily-data/prompts/reply_revise.md`（新增）

审核修正用 system prompt。变量：`{draft_reply}` `{issues}` `{sop_id}` `{user_input}` `{steps_summary}`。指令：根据审核发现的问题重新合成回复，只返回 `{"reply": "..."}`。

---

## 复用的现有组件

| 组件 | 位置 | 复用方式 |
|------|------|----------|
| `LLMClient.chat_json` | [client.py](emily-core/emily_core/infrastructure/llm/client.py) | 重试/修正的 LLM 调用（已支持 `model` override） |
| `RealGuardian._build_reply_prompt` | [real_guardian.py:170](emily-core/emily_core/workitem/pipeline/real_guardian.py#L170) | `revise_reply` 复用上下文构建逻辑 |
| `WorkItem.transition_to` | [workitem.py:73](emily-core/emily_core/workitem/workitem.py#L73) | 状态转 FAILED（复用现有校验） |
| `wi.error_message` / `wi.result_text` / `wi.add_warning` | [workitem.py](emily-core/emily_core/workitem/workitem.py) | 错误记录与回复 |
| Scheduler FAILED 转换 | [scheduler.py:133-147](emily-core/emily_core/workitem/scheduler.py#L133-L147) | 直接复用，不重写 |
| `_grade_skill_risk` | [workitem_agent.py:167](emily-core/emily_core/workitem/workitem_agent.py#L167) | 判定 step 是否可重试 |
| `load_prompt` 缓存机制 | prompt_loader | 新 prompt 自动缓存 |

---

## 构建顺序

```
M1(Config) ──→ M2(错误反馈重试) ──→ M3(审核修正循环) ──→ M4(失败传播) ──→ M5(Prompt)
                                                                    │
                              M5 的 prompt 被 M2/M3 引用，可先建骨架再迭代 ┘
```

建议串行：M1 → M5（prompt 骨架）→ M2 → M3 → M4，每模块 emy-test 验证后再下一个。

---

## 验证

### 端到端（emy-test + 真实用户）

```bash
# 0. 清缓存重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 查真实用户 UUID
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username FROM users WHERE status='active' LIMIT 3;"

# 1. 错误反馈重试验证（构造参数缺失的录入）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件" --sender "李景利"
# 预期：node3 step 失败 → 重试 1 次 → 仍失败 → WI FAILED → 回复含"处理失败"说明

# 2. 审核修正循环验证（构造易触发 issues 的查询，L2 录入类）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "李景利"
# 预期：若 Guardian 返回 issues → revise_reply 修正 → 再审核；日志出现 revise/retry

# 3. L3 不重试验证
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "废弃节点 XX-001" --sender "李景利"
# 预期：L3 失败直接 FAILED，不重试

# 4. 日志确认
docker logs --tail 80 emily-core 2>&1 | grep -E "retry_step|revise_reply|FAILED|should_abort"
# 预期：出现重试/修正/FAILED 日志

# 5. LLM trace 确认重试/修正调用
docker exec mitmproxy tail -15 /app/logs/llm_trace.jsonl
# 预期：含 call_category=retry / revise 的 LLM 调用

# 6. 基础设施 fail-open 保留验证
#    临时让 LLM 不可用（如改错 api_key 重启），发消息
#    预期：仍走 fallback steps + 硬编码回复，不崩（harness_fail_open_on_llm_unavailable=True）
```

### 通过标准

| 指标 | 目标 | 验证 |
|------|------|------|
| 工具失败重试 | L1/L2 失败触发 1 次重试 | 日志 `retry_step` |
| L3 失败不重试 | 直接 FAILED | 日志无 `retry_step` |
| 审核修正循环 | issues 非空触发 revise | 日志 `revise_reply` |
| 重试/修正耗尽 → FAILED | `wi.state=FAILED` + 有意义 result_text | emy-test 回复含"处理失败" |
| LLM 不可用仍降级 | 走 fallback 不崩 | emy-test 仍回复 |
| 权限失败不重试 | 直接 FAILED | 日志无 `retry_step` |

### 失败处理

- 重试不触发：检查 `harness_retryable_risk_levels` 是否含当前 `wi.risk_level`、`_retry_step_with_error_feedback` 是否被 `if not sr.success` 分支调用
- 修正循环不触发：检查 `should_review_reply`（L1 跳过审核）、`review_reply` 是否返回 issues
- FAILED 时回复空：检查 M4 的 `wi.result_text` 兜底是否生效

---

## 风险与权衡

1. **LLM 调用数增加**：每个失败 step +1 次重试调用，每个有 issues 的 reply +1 次修正调用。与"对话流优化"计划（降 LLM 调用数）方向相反——但 harness 只在**失败/有问题时**触发，正常路径零成本。可在 Config 调 `harness_step_max_retries=0` 关闭。
2. **"统一 FAILED"对体验的影响**：审核修正失败不再输出"最佳 draft + warnings"，而是报错。若实测发现误杀率高，可退回"修正失败 → 用最后 draft + warnings"（在 M3 伪代码里加一个 Config 开关 `harness_reply_fail_open_on_revise_exhausted`）。
3. **重试副作用**：L2 录入类（record_event 等）若 handler 非幂等，重试可能重复录入。需确认 handler 幂等性——`record_*` 系列 handler 已用 `object_id` 去重（见 [workitem_agent.py:601](emily-core/emily_core/workitem/workitem_agent.py#L601)），但建议 M2 实现时只对**参数错误**重试，对**handler 执行后失败**不重试（避免副作用）。

---

*本计划基于 2026-07-25 代码现状。实施前若 "对话流优化_计划_V1.md" 的 M1-M5 已落地，需先确认本计划引用的行号/方法仍存在。*
