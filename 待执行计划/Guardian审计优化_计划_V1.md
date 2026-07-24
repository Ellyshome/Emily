# Guardian 步骤审计优化 — AI 执行计划

> **基于需求**：Session 审计过度——一次问答触发 30 次 Guardian LLM 调用，其中 26 次审计纯指令文本（无工具输出/无 RAG），全部返回 `{"issues": []}`，纯属 token 浪费
> **计划版本**：v1.0
> **目标**：在 node3 调用点和 `review_step` 内部双重守门，跳过"无实质数据"的步骤审计；保留 reply 审计不变。预期每轮 Guardian 调用从 ~30 次降至 ~7 次，token 消耗下降 ~70%

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **成本优化工程师**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。改动量很小（2 处、约 12 行），但需保证语义正确、可回滚、可验收。

---

## 硬约束（违反即失败）

1. **不改 `review_reply`**：node4 的回复出站审计保持原样，始终审核（越权泄露/敏感信息维度只在 reply 层有意义）
2. **不改 `guardian_step.md` / `guardian_reply.md` prompt 模板**：审核维度不变，只改"是否调用"的守门条件
3. **不改 `StepResult` 数据结构**：不新增/修改字段
4. **不改归档逻辑**：`node3_execute` 末尾的 `prompt_info_node3` 归档段落保持遍历全部 step（记录 prompt 字符数供回溯，不涉及 LLM 调用，开销可忽略）
5. **行为等价原则**：被跳过的 step 当前返回的就是 `{"issues": []}` → `None`，跳过后仍返回 `None`，对 `wi.warnings` / `sr.guardian` 的影响完全一致，不得改变非空 issues 的处理路径
6. **守门字段三元组固定**：`tool_calls` / `rag_results` / `db_results` 三个字段任一非空即视为"有实质数据"，需审计；三者皆空才跳过。不得用 `output` 字段判断（`output` 可能是纯指令文本也可能是工具结果文本，不可靠）
7. **每模块验收**：M1 验收通过才做 M2，M2 通过才做验收

---

## 上下文（执行前必读）

### 问题背景

通过 mitmproxy 抓包（`emily-data/logs/llm_trace.jsonl`）发现一次问答触发了 30 次 Guardian LLM 调用：

| 指标 | 数值 |
|------|------|
| Guardian 调用总数 | 30 次 |
| 总 token 消耗 | 8,461（prompt 8,258 + completion 203）|
| **空 step 审计（无工具无 RAG）** | **26 / 30 = 87%** |
| 有工具调用的 step 审计 | 仅 3 次 |
| reply 审计 | 4 次 |

### 根因分析

**调用链**：
1. `SessionAgent._recognize_intent` 把多条历史消息识别为复合请求，拆成多个 WorkItem（抓包案例拆出 6 个）
2. 每个 WorkItem 走 SOP（如 SOP-002-REC 事件记录有 6 个 step）
3. `WorkItemAgent.node3_execute` 对**每个** `StepResult` 都 `asyncio.create_task(self._guardian.review_step(sr))`，无条件调用
4. 每个 WorkItem 末尾 `node4_summary` 再调 1 次 `review_reply`

**26 次空审计的内容样本**（来自 jsonl）：
- `step-01`: "意图判定：判断用户消息是否为事件记录请求..."（纯指令）
- `step-02`: "提取事件标题（title）..."（纯指令）
- `step-04`: `{'event_date': 'today'}`（SkillExecutor 提取的参数，非工具输出）
- `step-05`: "提取描述（description）..."（纯指令）

这些 step 走的是 [workitem_agent.py:608-614](emily-core/emily_core/workitem/workitem_agent.py#L608-L614) 的"无工具步骤"分支，`sr.output = step.description`，而 `guardian_step.md` 的三个审核维度（虚构数据 / 错误引用 / 逻辑矛盾）**全部依赖工具返回数据或 RAG 引用片段**——没数据就必然返回 `{"issues": []}`，白白消耗 ~230 tokens/次。

### 架构决策

**为什么用"双守门"（调用点 + 内部兜底）而非单点**：
- 调用点（M1）过滤：避免无意义的 `asyncio.create_task` 调度开销，是主优化点
- 内部兜底（M2）：把"无数据不审计"固化为 `RealGuardian` 自身契约，避免将来其他调用方（如调度器、脚本）再调 `review_step` 时漏判
- 两处判断逻辑完全一致，互为保险，无冲突

**为什么不直接关掉 step 审计**：有工具调用的 step（如抓包中的 step-06 "越权查询 projects"）确实需要审"虚构数据/错误引用"，3 次/轮的 token 成本（~1000 tokens）是值得的花费。

**为什么不改归档**：`node3_execute` 末尾归档段遍历全部 step 记录 `step_prompt_chars`，只算字符数不调 LLM，开销可忽略；且记录"被跳过 step 的 prompt 本会长什么样"对回溯有价值。

### 已有的可复用组件

| 组件 | 位置 | 关键点 |
|------|------|--------|
| `StepResult` dataclass | [emily-core/emily_core/workitem/pipeline/interfaces/execution.py:100-120](emily-core/emily_core/workitem/pipeline/interfaces/execution.py#L100-L120) | `tool_calls`/`rag_results`/`db_results` 均为 `field(default_factory=list)`，空列表是 falsy |
| `RealGuardian.review_step` | [emily-core/emily_core/workitem/pipeline/real_guardian.py:71-99](emily-core/emily_core/workitem/pipeline/real_guardian.py#L71-L99) | 审核单步，返回 `GuardianNote` 或 `None` |
| `RealGuardian.review_reply` | [emily-core/emily_core/workitem/pipeline/real_guardian.py:103-127](emily-core/emily_core/workitem/pipeline/real_guardian.py#L103-L127) | 审核最终回复——**本次不改** |
| `node3_execute` guardian 段 | [emily-core/emily_core/workitem/workitem_agent.py:396-421](emily-core/emily_core/workitem/workitem_agent.py#L396-L421) | 创建并 gather guardian task 的位置 |

### 代码模式参照表

| 改动点 | 参照源 | 要模仿的要点 |
|--------|--------|-------------|
| 字段非空判断 | [real_guardian.py:138](emily-core/emily_core/workitem/pipeline/real_guardian.py#L138) `for tc in getattr(sr, "tool_calls", []) or []` | 用 `getattr(sr, "tool_calls", None)` 取值，空列表/None 都是 falsy |
| 守门跳过模式 | [real_guardian.py:77-78](emily-core/emily_core/workitem/pipeline/real_guardian.py#L77-L78) `if not self._llm: return None` | 早返回 `None`，与"无 LLM 时跳过"语义一致 |

---

## 模块依赖图

```
M1(node3_execute 调用点守门) ──→ M2(review_step 内部兜底)
```

**顺序**：M1 先做（主优化点，效果立即可验），M2 后做（内聚兜底）。两者逻辑独立，但 M2 是双保险，M1 验收通过后再加 M2 可避免同时改两处时定位混乱。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/workitem/workitem_agent.py` | 修改 | `node3_execute` 创建 guardian task 前加"有实质数据"过滤 |
| M2 | `emily-core/emily_core/workitem/pipeline/real_guardian.py` | 修改 | `review_step` 开头加无数据跳过兜底 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/workitem/workitem_agent.py` | 修改 | `node3_execute`（line 398-405）guardian task 创建循环加 `has_data` 守门 |
| `emily-core/emily_core/workitem/pipeline/real_guardian.py` | 修改 | `review_step`（line 77 后）加无数据早返回 |
| `emily-core/emily_core/workitem/pipeline/real_guardian.py` 的 `review_reply` | 不变 | — |
| `emily-data/prompts/guardian_step.md` | 不变 | — |
| `emily-data/prompts/guardian_reply.md` | 不变 | — |
| `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` | 不变 | — |
| `node3_execute` 末尾归档段（line 423-449） | 不变 | — |

---

## M1: node3_execute 调用点守门

**依赖**：无（首改模块）

**职责**：在 `node3_execute` 创建 guardian task 的循环中，跳过没有 `tool_calls`/`rag_results`/`db_results` 的 step，避免对纯指令/参数提取步骤发起无意义的 LLM 调用。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | guardian task 创建循环加 `has_data` 守门 | `emily-core/emily_core/workitem/workitem_agent.py` |

### 代码

#### `emily-core/emily_core/workitem/workitem_agent.py` — 修改 `node3_execute` 中 guardian task 创建段

**定位**：在 `node3_execute` 方法内，搜索锚点 `# Guardian 并进审核：每个 step 的 review 作为后台 Task`（约 line 396-405）。

**当前代码**（精确匹配）：

```python
        # Guardian 并进审核：每个 step 的 review 作为后台 Task，
        # 在全部步骤执行完后 gather() 汇合，不阻塞主链路
        guardian_tasks: list[asyncio.Task] = []
        if self._guardian:
            for sr in step_results:
                task = asyncio.create_task(
                    self._guardian.review_step(sr),
                    name=f"guardian_step_{sr.step_id}",
                )
                guardian_tasks.append(task)
```

**替换为**：

```python
        # Guardian 并进审核：每个 step 的 review 作为后台 Task，
        # 在全部步骤执行完后 gather() 汇合，不阻塞主链路。
        # 守门：只审计有实质数据（工具调用/RAG/DB 结果）的 step；
        # 纯指令/参数提取步骤无可审计数据，guardian 必然返回空，跳过省 token。
        guardian_tasks: list[asyncio.Task] = []
        if self._guardian:
            for sr in step_results:
                has_data = (
                    getattr(sr, "tool_calls", None)
                    or getattr(sr, "rag_results", None)
                    or getattr(sr, "db_results", None)
                )
                if not has_data:
                    continue
                task = asyncio.create_task(
                    self._guardian.review_step(sr),
                    name=f"guardian_step_{sr.step_id}",
                )
                guardian_tasks.append(task)
```

**改动要点**：
- 新增 `has_data` 三元组判断，任一非空即审计
- `continue` 跳过无数据 step，不创建 task
- 注释说明守门原因，便于后续维护者理解
- 不改动后续 `gather` / `zip` 逻辑（`guardian_tasks` 只含被审计的 step，`zip(step_results, notes)` 的对应关系仍正确——见下方"正确性论证"）

**正确性论证**（执行者需理解）：
- 原代码 `zip(step_results, notes)` 中 `notes` 与 `step_results` 等长对应
- 改动后 `guardian_tasks` 只含被审计的 step，`notes` 长度 = 被审计 step 数 < `step_results` 长度
- **但** `zip(step_results, notes)` 这一行会因长度不匹配而错位！

**⚠ 执行者必须同步检查 gather 后的 zip 逻辑**（约 line 408-419）：

```python
        if guardian_tasks:
            try:
                notes = await asyncio.gather(*guardian_tasks, return_exceptions=True)
                for sr, note in zip(step_results, notes):
                    if isinstance(note, GuardianNote) and note.issues:
                        for issue in note.issues:
                            wi.add_warning(f"[{note.source}] {issue}")
                        sr.guardian = GuardianStepVerdict(
                            verdict="FLAG",
                            reason="; ".join(note.issues),
                        )
            except Exception as e:
                logger.warning("Guardian gather failed: %s", e)
```

`zip(step_results, notes)` 在 `notes` 比 `step_results` 短时会截断，导致**只给前 N 个 step 写 guardian 标记，且可能写到错误的 step 上**。

**修复方案**：把 `zip(step_results, notes)` 改为按被审计的 step 子集 zip。将上面整段替换为：

```python
        if guardian_tasks:
            try:
                notes = await asyncio.gather(*guardian_tasks, return_exceptions=True)
                # 只对被审计的 step 写回 guardian 结果（与 guardian_tasks 创建时的过滤保持一致）
                audited_steps = [
                    sr for sr in step_results
                    if (getattr(sr, "tool_calls", None)
                        or getattr(sr, "rag_results", None)
                        or getattr(sr, "db_results", None))
                ]
                for sr, note in zip(audited_steps, notes):
                    if isinstance(note, GuardianNote) and note.issues:
                        for issue in note.issues:
                            wi.add_warning(f"[{note.source}] {issue}")
                        sr.guardian = GuardianStepVerdict(
                            verdict="FLAG",
                            reason="; ".join(note.issues),
                        )
            except Exception as e:
                logger.warning("Guardian gather failed: %s", e)
```

**说明**：`audited_steps` 的过滤条件与创建 task 时完全一致，顺序也与 `guardian_tasks` 一致（都按 `step_results` 原顺序过滤），故 `zip(audited_steps, notes)` 严格一一对应。

> **替代实现**（二选一，执行者择优）：也可在创建 task 时同步记录 `audited_steps` 列表，避免重复计算过滤。例如在 `for sr in step_results` 循环里 `has_data` 为真时既 `append` 到 `guardian_tasks` 也 `append` 到 `audited_steps`，随后 `zip(audited_steps, notes)`。这样更 DRY，推荐采用。

### M1 验收

1. **静态检查**：
   ```powershell
   docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
   docker compose -f docker-compose-napcat.yml restart emily-core
   docker logs --tail 30 emily-core 2>&1  # 确认无启动报错
   ```

2. **运行时验证**（触发复合请求，观察 guardian 调用数下降）：
   ```powershell
   # 先查一个真实用户
   docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 5;"

   # 记录当前 jsonl 行数
   docker exec mitmproxy wc -l /app/logs/llm_trace.jsonl

   # 发复合消息（会拆成多 WorkItem）
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成；再查一下最近的节点计划" --sender "真实用户名"

   # 统计新增的 guardian 调用
   docker exec mitmproxy grep -c "审核步骤\|审核回复" /app/logs/llm_trace.jsonl
   ```

3. **验收标准**：
   - 重启无报错
   - guardian 调用数相比改动前同场景**显著下降**（抓包基线 30 次/轮，优化后应 ≤ 10 次/轮）
   - **关键**：jsonl 中所有 `审核步骤` 调用的 system prompt 里，`工具调用记录:` 或 `RAG引用片段:` 至少有一处非空（即不再出现纯指令文本审计）
   - 回复内容正常，无报错

---

## M2: review_step 内部兜底

**依赖**：M1 验收通过

**职责**：在 `RealGuardian.review_step` 开头加"无实质数据早返回"，把守门逻辑固化为 Guardian 自身契约，防止将来其他调用方漏判。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | `review_step` 开头加无数据跳过 | `emily-core/emily_core/workitem/pipeline/real_guardian.py` |

### 代码

#### `emily-core/emily_core/workitem/pipeline/real_guardian.py` — 修改 `review_step` 方法

**定位**：`review_step` 方法开头，搜索锚点 `if not self._llm:`（约 line 77-79）。

**当前代码**（精确匹配）：

```python
    async def review_step(self, step_result: Any) -> GuardianNote | None:
        """审核单个 StepResult。返回问题列表或 None。

        作为 asyncio.create_task() 的目标函数使用，
        在后台与下一步执行并行跑。
        """
        if not self._llm:
            return None
        prompt = self._build_step_prompt(step_result)
```

**替换为**：

```python
    async def review_step(self, step_result: Any) -> GuardianNote | None:
        """审核单个 StepResult。返回问题列表或 None。

        作为 asyncio.create_task() 的目标函数使用，
        在后台与下一步执行并行跑。
        """
        if not self._llm:
            return None
        # 无实质数据则跳过——guardian 三维度（虚构数据/错误引用/逻辑矛盾）
        # 全部依赖工具返回或 RAG 引用，无数据时必然返回空列表，徒耗 token。
        # 此兜底与 node3_execute 调用点过滤逻辑一致，双重守门。
        if not (getattr(step_result, "tool_calls", None)
                or getattr(step_result, "rag_results", None)
                or getattr(step_result, "db_results", None)):
            return None
        prompt = self._build_step_prompt(step_result)
```

**改动要点**：
- 在 `if not self._llm` 之后、`_build_step_prompt` 之前插入守门
- 判断字段三元组与 M1 完全一致
- 注释说明"与调用点双重守门"的意图

### M2 验收

1. **静态检查**：重启 + 看日志无报错（同 M1）

2. **运行时验证**：重复 M1 的 emy-test 步骤，确认 guardian 调用数与 M1 后基本一致（M2 是兜底，不应再额外下降，因为 M1 已在调用点过滤）

3. **单元验证（可选但推荐）**：临时构造一个无数据的 `StepResult` 直接调 `review_step`，确认返回 `None` 且不发起 LLM 调用：
   ```python
   # docker exec emily-core python -c "..."
   docker exec emily-core python -c "
   import asyncio
   from emily_core.workitem.pipeline.real_guardian import RealGuardian
   from emily_core.workitem.pipeline.interfaces.execution import StepResult
   # 无 LLM client 也能验证守门（守门在 LLM 检查之后，需传一个 truthy llm）
   g = RealGuardian(llm_client=object())
   sr = StepResult(step_id='test', output='纯指令文本')
   result = asyncio.run(g.review_step(sr))
   print('无数据 step 返回:', result)  # 预期: None
   "
   ```
   预期输出：`无数据 step 返回: None`

4. **验收标准**：
   - 守门触发时返回 `None`，不调 LLM
   - 有数据的 step 审计行为不变（仍能返回 `GuardianNote`）

---

## 验收方案（整体）

### 场景对比

改动前后用**同一条复合消息**测试，对比 jsonl 中 guardian 调用：

| 指标 | 改动前基线 | 改动后预期 |
|------|-----------|-----------|
| guardian 调用数/轮 | ~30 | ≤ 10（典型 ~7） |
| 其中空 step 审计 | ~26 | 0 |
| 其中有工具 step 审计 | ~3 | ~3（不变） |
| reply 审计 | ~4 | ~4（不变） |
| token 消耗/轮 | ~8,461 | ~2,600 |

### 验收命令序列

```powershell
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 确认启动正常
docker logs --tail 30 emily-core 2>&1 | Select-String "ERROR|Traceback"
# 预期：无输出

# 3. 查真实用户 UUID
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 5;"

# 4. 记录基线行数
docker exec mitmproxy wc -l /app/logs/llm_trace.jsonl

# 5. 发测试消息（用上一步查到的真实用户名）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成；再查一下最近的节点计划" --sender "真实用户名"

# 6. 等待回复完成后，统计本轮 guardian 调用
# （用行数差定位本轮新增的行）
docker exec mitmproxy grep -c "审核步骤" /app/logs/llm_trace.jsonl
docker exec mitmproxy grep -c "审核回复" /app/logs/llm_trace.jsonl

# 7. 抽查：所有"审核步骤"调用的 system prompt 是否都含工具/RAG 数据
docker exec mitmproxy grep "审核步骤" /app/logs/llm_trace.jsonl | Select-String "工具调用记录: \n|RAG引用片段: \n"
# 预期：无匹配（即不再有空工具记录+空RAG的纯指令审计）
```

### 验收通过判据

全部为真：
1. emily-core 重启无报错
2. 测试消息正常收到回复
3. 本轮 `审核步骤` 调用数 ≤ 有工具调用的 step 数（纯指令 step 不再被审计）
4. `审核回复` 调用数 = WorkItem 数（reply 审计保留）
5. 回复内容未被错误标记（无 `{"issues": [...]}` 误报导致的 ⚠️ 提醒异常增加）

---

## 风险与回滚

### 风险评估

| 风险 | 等级 | 说明 | 缓解 |
|------|------|------|------|
| zip 错位写错 guardian 标记 | **高** | M1 若不同步修 `zip(step_results, notes)`，会错位 | M1 已强制要求同步改 zip，执行者必须两段一起改 |
| 漏判有数据的 step | 中 | 若某 step 的 `output` 含关键数据但 `tool_calls`/`rag`/`db` 都空（理论不应发生，但 SkillExecutor 路径需确认） | 验收时确认有工具调用的 step 仍被审计；若发现遗漏，可放宽守门条件 |
| 审计能力下降 | 低 | 被跳过的 step 本就返回空，无实际审计损失 | 行为等价，风险极低 |

### 回滚

两处改动均为纯增量（加 `if` 早返回 / `continue`），回滚只需删除新增代码块：

```powershell
git diff emily-core/emily_core/workitem/workitem_agent.py
git diff emily-core/emily_core/workitem/pipeline/real_guardian.py
# 确认 diff 仅含本次改动后
git checkout -- emily-core/emily_core/workitem/workitem_agent.py emily-core/emily_core/workitem/pipeline/real_guardian.py
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
```

---

## 文档同步

改动合入后，更新以下文档（按 CLAUDE.md §10 维护约定）：

| 文档 | 更新内容 |
|------|----------|
| [docs/业务模块与运转全景.md](docs/业务模块与运转全景.md) | Guardian 审核策略说明：补充"step 审计仅对有 tool_calls/rag_results/db_results 的步骤触发，纯指令步骤跳过" |
| [docs/技术踩坑备忘录.md](docs/技术踩坑备忘录.md) | 新增踩坑条目：Guardian 审计过度（30 次/轮、87% 空审计），根因 + 解决方案 + 抓包验证方法 |
| [docs/开发记录.md](docs/开发记录.md) | 记录本次成本优化决策：双守门方案、预期 token 节省 ~70% |

---

## 附：抓包数据明细（基线，改动前）

供执行者验收时对照。来自 `emily-data/logs/llm_trace.jsonl` 一次复合问答：

```
L3  审核步骤 step-01  tool=False rag=False 240t  意图判定：判断用户消息是否为事件记录请求...
L4  审核步骤 step-04  tool=False rag=False 226t  {'event_date': 'today'}
L5  审核步骤 step-05  tool=False rag=False 234t  提取描述（description）...
L6  审核步骤 step-02  tool=False rag=False 250t  提取事件标题（title）...
L7  审核步骤 step-03  tool=False rag=False 226t  {'event_type': 'general'}
L8  审核步骤 step-06  tool=True  rag=False 327t  越权查询projects   ← 唯一值得审计的
L10 审核回复                                   458t
L11 审核步骤 step-04  tool=False rag=False 230t  从用户消息中提取描述（description）
L12 审核步骤 step-05  tool=False rag=False 229t  从用户消息中提取优先级（priority）
L13 审核步骤 step-03  tool=False rag=False 237t  从用户消息中提取截止日期（due_date）
L14 审核步骤 step-01  tool=False rag=False 230t  从用户消息中提取任务标题（title）
L15 审核步骤 step-06  tool=True  rag=False 327t  越权查询projects   ← 唯一值得审计的
L16 审核步骤 step-02  tool=False rag False 229t  从用户消息中提取负责人（owner）
L18 审核回复                                   402t
L19 审核步骤 step-01  tool=False rag=False 241t  步骤执行异常: 参数 'query_type' 读取失败
L21 审核回复                                   355t
L22 审核步骤 step-02  tool=False rag=False 250t  提取事件标题（title）...
L23 审核步骤 step-05  tool=False rag=False 234t  提取描述（description）...
L24 审核步骤 step-01  tool=False rag=False 240t  意图判定...
L25 审核步骤 step-04  tool=False rag=False 226t  {'event_date': 'today'}
L26 审核步骤 step-06  tool=True  rag=False 327t  越权查询projects   ← 唯一值得审计的
L27 审核步骤 step-03  tool=False rag=False 226t  {'event_type': 'general'}
... (其余 reply 审计)
```

**关键观察**：30 次中仅 3 次（L8/L15/L26，都是 step-06 有工具调用）值得审计，其余 26 次审计纯指令文本或参数提取结果，全部返回 `{"issues": []}`。优化后这 26 次将被跳过。
