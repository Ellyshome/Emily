# DevOps Agent — 运维诊断 Agent 需求规格

> **版本**：V1
> **状态**：待评审
> **最后更新**：2026-07-02

---

## 1. 定位

### 1.1 一句话

DevOps Agent 是 Emily 系统的**被动式运维诊断工具**。当系统内部工具/脚本出现未预期异常时被自动拉起，或在管理员手动唤起时启动。完成问题评估、根因排查、制定修复计划后报告管理员，等待审批。**不自动执行修复**。

### 1.2 不是什么

- **不是后台守护进程** — 无 Tick 循环、无心跳、无健康检查。每次被拉起 → 完成当前阶段工作 → 释放
- **不是自动修复机器人** — 不自己动手改代码、改配置。修复计划是建议书，执行按钮在管理员手里
- **不是 OpsMonitor** — OpsMonitor 做项目感知（复盘、晨报），DevOps Agent 做系统诊断（错误排查、修复计划）。两者通过 `_devops_flag` 信号连接

---

## 2. 背景与动机

当前系统中所有工具/脚本的异常处理路径是单一的：

```python
except Exception as e:
    logger.error("...", exc_info=True)
    # 然后就没了
```

日志写入了，但没人去读。脚本连续失败了 3 次、Digest 刷新一直超时、节点重算脚本抛 KeyError——这些问题会一直沉默到有用户反馈，或者更糟的是，一直不被人发现。

需要一个能"接住"这些异常、做初步诊断的角色。判断不了的就报告管理员，能判断的就制定修复计划让管理员审批。

---

## 3. 激活方式

### 3.1 场景 A：异常钩子（自动）

任何 Emily 内部模块在捕获未预期异常时，将上下文打包交给 DevOps Agent 做初步评估：

```python
try:
    result = await tool.execute(params)
except Exception as e:
    logger.error("Tool %s failed: %s", tool_name, e)

    # 打包异常上下文
    error_ctx = ErrorContext(
        source=f"{tool_name}.execute",
        error_type=type(e).__name__,
        error_message=str(e),
        traceback=traceback.format_exc(),
        context={"tool_name": tool_name, "params": params},
        severity=Severity.UNKNOWN,  # Agent 判断后更新
    )

    # 异步交给 DevOps Agent，不阻塞当前执行路径
    asyncio.create_task(devops_agent.handle_error(error_ctx))
```

触发源包括但不限于：

| 来源 | 示例场景 |
|------|----------|
| OpsMonitor | Digest 刷新连续失败、复盘 LLM 调用超时 |
| PlanTaskScheduler | LLM 周期推算失败、归档操作抛异常 |
| 任意工具 handler | `handle_record_event` 等抛非预期异常 |
| 资料库 RAG 模块 | `hit_test` API 不可达 |
| 邮件模块 | SMTP 发送连续超时 |

### 3.2 场景 B：管理员手动唤起

```bash
docker exec -it emily-core python -m emily_core.devops_agent --interactive
```

进入交互式 CLI REPL。管理员可以：

- "查看最近 24 小时所有未处理异常"
- "帮我排查 plan_task 模板 PT-001 的 LLM 推算为什么连续失败"
- "查看修复计划 DP-20260702-001 的详情"
- "执行修复计划 DP-20260702-001"（审批后执行）
- "放弃修复计划 DP-20260702-001，我已经手动修了"

也可以从 IM 通道发起（通过 SessionAgent 转达），例如管理员在 QQ 中 @Emy "执行修复计划 DP-xxx"。

### 3.3 防抖

同一 `source + error_type` 在冷却时间内（默认 30 分钟）不重复拉起 Agent 做全流程诊断。冷却内的重复异常仅记录计数，等冷却结束后如果问题仍存在再触发新一次诊断。

---

## 4. 标准工作流

```
被拉起
  │
  └── 1. 问题评估 ──────────────────────────────
        ├── 读取 error + traceback + source + context
        ├── 查相关日志
        ├── 判断严重程度
        │     L1 — 可等待（单次偶发异常，不影响核心流程）
        │     L2 — 需尽快处理（重复出现、影响非关键功能）
        │     L3 — 系统级紧急（核心功能受阻、数据安全问题）
        ├── 判断是否需要深入排查
        │     L1 → 可跳过深入排查，仅记录
        │     L2/L3 → 必须深入排查
        └── 生成初步评估摘要 → 通知管理员

     2. 深入排查（如需要）────────────────────
        ├── 读相关模块源码
        ├── 检查数据库状态（连接、表结构、数据一致性）
        ├── 查配置文件
        ├── 检查外部依赖可达性
        └── 定位根因

     3. 制定修复计划 ──────────────────────────
        ├── 生成修复计划文档
        │     ├── 问题描述
        │     ├── 根因分析
        │     ├── 修复步骤（含代码 diff / SQL / 命令）
        │     ├── 每步的验证方法
        │     ├── 风险评估
        │     └── 回滚方案
        ├── 写入 devops_fix_plans 表
        │     status = 'pending_approval'
        └── 生成 plan_id（格式：DP-YYYYMMDD-NNNN）

     4. 报告管理员 ─────────────────────────────
        └── 通过 primary_channel 通知：
             "[DevOps Agent]
              检测到异常：{source} — {error_type}
              严重程度：L2
              评估摘要：{summary}
              修复计划已生成：{plan_id}
              请审核后执行，或直接放弃（如已手动修复）。"

             Agent 释放 / 休眠。

     ─── 此处可能间隔数小时甚至数天 ───

     5. 管理员唤起 ─────────────────────────────
        ├── 管理员通过 IM / CLI 发起：
        │     "执行修复计划 {plan_id}"
        │     "放弃 {plan_id}，我来手动修"
        │     "重新诊断 {plan_id}"
        │
        ├── 如果批准执行：
        │     ├── 按计划步骤逐一执行
        │     ├── 每步完成后验证
        │     ├── 结果写入 plan status
        │     │     'executing' → 'completed' / 'partially_completed'
        │     └── 管理员随时可中止（"停止执行"）
        │
        └── 如果管理员放弃：
              └── plan status → 'cancelled_by_admin'
                  （修复计划完好保留，供后续参考）
```

---

## 5. 修复计划文档结构

每份修复计划是一个结构化文档，存储在 `devops_fix_plans` 表的 `plan_content` 列中（Markdown 格式）：

```markdown
# 修复计划 DP-20260702-0001

## 基本信息
- 创建时间：2026-07-02 14:30:00 UTC+8
- 来源：PlanTaskScheduler._calc_current_period
- 异常类型：LLMCalculationError
- 严重程度：L2

## 问题描述
PlanTaskScheduler 在推算循环任务 PT-001 的当前周期时连续 3 次失败。
LLM 返回了非 JSON 格式的响应，导致 chat_json 解析失败。

## 根因分析
经排查，LLM API 近期升级了模型版本，新版本的 system prompt 与
用户 prompt 的 JSON 输出格式指令存在冲突，偶发返回非 JSON 文本。

## 修复步骤

### 步骤 1：更新 prompt 中的输出格式指令
- 文件：emily_core/services/plan_task_scheduler.py L440-L446
- 操作：在 chat_json 调用前追加 strict JSON output 指令
- 预期结果：LLM 稳定返回 JSON 格式
- 验证：调用 3 次 _calc_current_period，确认全部返回合法 JSON

### 步骤 2（备选）：降级处理
- 如果步骤 1 无效，在 JSON 解析异常时增加一次 retry
- 文件：同上
- 验证：模拟 LLM 返回非 JSON → 确认自动 retry 生效

## 风险评估
- 低风险。仅修改 prompt 文本，不改变业务逻辑
- 最坏情况：prompt 修改后 LLM 仍不稳定 → 回退原 prompt

## 回滚方案
恢复 plan_task_scheduler.py 中 prompt 文本到修改前版本，重启容器。

## 验证方法
1. 容器内运行 3 次周期推算，全部返回合法 JSON
2. 观察 24h 内 PlanTaskScheduler 日志无 LLMCalculationError
```

---

## 6. 能力边界

### 6.1 可以做什么

| 操作 | 说明 |
|------|------|
| 读日志文件 | `/var/log/emily/` 下的所有日志 |
| 读源码 | `emily_core/` 下所有 .py 文件（只读） |
| 读配置 | `config.py` + 环境变量 + `core_config.json` |
| 查询数据库 | SELECT 操作（不写） |
| 检查外部依赖 | HTTP ping、DNS 解析、端口探测 |
| 生成修复计划 | 写入 `devops_fix_plans` 表 |
| 执行已审批的修复计划 | 按计划步骤执行（含写文件、重启服务等） |

### 6.2 不可以做什么

| 禁止项 | 说明 |
|------|------|
| 自行执行修复 | 任何修复操作必须等管理员审批后才能执行 |
| 修改数据库结构 | DDL 操作永远禁止 |
| 删除数据 | DELETE / DROP 操作永远禁止 |
| 修改权限配置 | 权限相关代码和配置不可触碰 |
| 触碰用户数据 | 不读、不改、不导出任何用户个人数据 |
| 修改数据库迁移脚本 | SQL 迁移文件为人工管理，不可自动修改 |

---

## 7. 交互方式

### 7.1 CLI 模式

```bash
docker exec -it emily-core python -m emily_core.devops_agent --interactive
```

进入 REPL，用自然语言交互。基于 `cmd.Cmd` 实现（与 EmilyShell 同模式）。

### 7.2 IM 模式

管理员通过 SessionAgent 在 QQ 中与 DevOps Agent 交互。SessionAgent 将消息路由给 DevOps Agent 处理（通过 `!devops` 前缀或 SessionAgent 的意图识别）：

```
管理员: !devops 查看最近的异常
Emy: [DevOps Agent] 过去 24 小时共 3 条异常：
     1. LLMCalculationError — PlanTaskScheduler — L2 — DP-20260702-0001 (待审批)
     2. ConnectionTimeout — EmailService — L1 (已自动恢复)
     3. ...

管理员: 执行 DP-20260702-0001
Emy: [DevOps Agent] 开始执行修复计划 DP-20260702-0001...
     步骤 1/2: 更新 prompt 输出格式指令 ✅
     步骤 2/2: 验证 LLM 响应 ✅
     修复完成。请观察 24h 确认问题已解决。
```

---

## 8. 与 OpsMonitor 的协作

```
OpsMonitor._nightly_review() / refresh_digest_if_stale()
  │
  ├── 正常执行 → 返回结果
  │
  └── 异常发生
        ├── logger.error(...)
        └── 打包 ErrorContext → devops_agent.handle_error(ctx)
              │
              └── DevOps Agent 被拉起 → 诊断 → 报告管理员
```

OpsMonitor 不直接调 DevOps Agent 的方法。异常钩子统一在 `ErrorContext` 打包层中实现，让 DevOps Agent 的接入点保持单一。

---

## 9. 数据库

### 9.1 新增表

| 表名 | 用途 | 关键列 |
|------|------|--------|
| `devops_fix_plans` | 修复计划文档 | plan_id (PK), source, error_type, severity(L1/L2/L3), plan_content (Markdown), status(pending_approval/approved/executing/completed/partially_completed/failed/cancelled_by_admin), created_at, approved_at, executed_at |
| `devops_error_log` | 异常记录（含防抖冷却） | source, error_type, first_seen_at, last_seen_at, count, severity, fix_plan_id (FK→devops_fix_plans) |

> `devops_error_log` 用于防抖——同一 `source + error_type` 在冷却期内不重复跑全流程诊断。冷却期默认 30min。

---

## 10. 配置项

| 键 | 类型 | 默认值 | 环境变量 |
|----|------|--------|----------|
| `devops_agent_enabled` | bool | true | `EMILY_DEVOPS_AGENT_ENABLED` |
| `devops_agent_error_cooldown_minutes` | int | 30 | `EMILY_DEVOPS_AGENT_ERROR_COOLDOWN` |
| `devops_agent_plan_retention_days` | int | 90 | `EMILY_DEVOPS_AGENT_PLAN_RETENTION_DAYS` |
| `devops_agent_llm_timeout_seconds` | int | 180 | `EMILY_DEVOPS_AGENT_LLM_TIMEOUT` |

> 通知通道共用 `users.primary_channel` 字段，不单独配置。

---

## 11. 非功能需求

### 11.1 可用性

- **fail-open**：DevOps Agent 自身执行失败不影响调用方的主流程。异常钩子是 `asyncio.create_task()` 异步执行，不阻塞原工具的错误处理
- **防抖**：同一问题不重复诊断
- **幂等**：同一个 `plan_id` 被重复要求执行时，检查当前状态，已完成的计划拒绝重复执行

### 11.2 可观测性

- 每次诊断记录到 `devops_error_log` 表
- 修复计划全生命周期可追溯（plan_content + status 变更）
- DevOps Agent 状态通过 `/health` 端点暴露

### 11.3 安全

- 不自动执行修复（核心安全策略）
- 修复计划中的每一步都有风险评估和回滚方案
- `devops_fix_plans` 表为只追加——不删除历史计划，保留审计链

---

## 12. 文件变更清单

### 12.1 新增文件

| 文件 | 说明 |
|------|------|
| `emily_core/services/devops_agent.py` | DevOpsAgent 类：handle_error / diagnose / generate_plan / execute_plan |
| `emily_core/services/error_context.py` | ErrorContext dataclass：统一的异常上下文打包格式 |
| `emily_core/services/devops_agent_cli.py` | CLI 入口（`python -m emily_core.devops_agent --interactive`） |
| `emily-data/prompts/devops_agent.md` | DevOps Agent 的 LLM system prompt |
| `需求文件/DevOpsAgent/DevOpsAgent-需求_V1.md` | 本需求文档 |

### 12.2 修改文件

| 文件 | 改动 |
|------|------|
| `emily_core/config.py` | +4 配置字段 |
| `emily_core/infrastructure/database/models.py` | +2 ORM（`DevopsFixPlan` / `DevopsErrorLog`） |
| `emily_core/__init__.py` | `_ensure_initialized()` 中创建 DevOpsAgent 实例；注入到各模块 |
| `emily_core/services/plan_task_scheduler.py` | `_tick()` 中增加 `_devops_flag` 检查点；异常处增加 ErrorContext 打包 |
| `emily_core/services/ops_monitor.py` | 异常处增加 ErrorContext 打包 |
| `emily_core/session/session_agent.py` | 增加 `!devops` 命令路由 |

---

*基于多轮架构讨论编写。DevOps Agent 与 OpsMonitor 的职责边界见 OpsMonitor 需求 V3 §1.2 和本文档 §8。*
