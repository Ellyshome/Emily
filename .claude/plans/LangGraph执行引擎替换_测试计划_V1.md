# LangGraph执行引擎替换 — 验证测试计划

> **计划日期**：2026-07-28
> **计划版本**：V1
> **依据文档**：[LangGraph执行引擎替换_计划_V1.md](..\..\需求\待执行计划\LangGraph执行引擎替换_计划_V1.md)
> **测试对象**：`emily_core/workitem/langgraph_engine/` 新引擎（含 error_analysis 纠错闭环）+ feature flag 切换 + 旧 `PipelineBUS` 回退安全
> **测试环境**：Docker Compose（emily-core v1.0 + emily-postgres） | LLM: deepseek-v4-flash | Engine: langgraph / pipeline_bus

---

## 一、测试目标

验证 LangGraph 执行引擎替换的 **6 项核心能力**：

1. **正常路径**：langgraph 引擎下 L2 录入类对话行为与旧 `pipeline_bus` 一致
2. **error_analysis 纠错闭环**：node3 执行失败 → error_analysis 分析 → 按错误类型路由（重规划/重试/abort），LLM trace 有 `call_category=error_analysis` 记录
3. **代码预分类省 LLM**：权限失败不调 LLM，直接 abort（省钱+安全）
4. **feature flag 切换**：`workitem_engine: langgraph` 启用新引擎；切回 `pipeline_bus` 旧引擎仍正常
5. **PipelineBUS 回退安全**：旧引擎代码（PipelineBUS/BusContext/WorkItemState/confirm_queue）完整保留，切换后立即可用
6. **运行时稳定性**：容器无 ERROR 日志、无重启、内存稳定

## 二、测试环境

| 项目 | 说明 |
|------|------|
| Docker Compose | `docker-compose-napcat.yml` |
| emily-core | FastAPI :18080, healthy |
| emily-postgres | PostgreSQL `emily`, healthy |
| LLM | deepseek-v4-flash |
| 当前引擎 | `pipeline_bus`（默认） |
| 切换后引擎 | `langgraph`，max_replan=1 |
| 预设数据 | 无需预埋 |

### 2.1 测试用户

| 角色 | 用户名 | UUID | Level | 用途 |
|------|--------|------|-------|------|
| 访客 | 周文斌 | `8c316f0c-6adc-4748-a870-780cb0838f3d` | 1 | 权限边界测试 |
| 执行级 | 张正宏 | `ce996655-d346-4c43-a4ac-5da60dc20e2b` | 3 | L2 录入测试 |
| 管理级 | 李景利 | `25fdc32a-17ad-4978-b9cc-2b19b77e0bfd` | 4 | L3 高风险测试 |

## 三、测试用例设计

### 分组 A：pipeline_bus（旧引擎基准回归）— 切 langgraph 前执行

| 编号 | 分类 | 测试用例 | 前置条件 | 输入/操作 | 预期行为 | 验证方式 |
|------|------|---------|---------|-----------|---------|---------|
| TC-A1 | 正常路径·基准 | 旧引擎 L2 录入：创建事件 | workitem_engine=pipeline_bus（默认） | `emy-test --managed --llm --message "帮我创建事件：样板段放线完成" --sender "张正宏" --sender-id "ce996655-d346-4c43-a4ac-5da60dc20e2b"` | 返回事件创建成功的自然语言回复（含事件标题/状态），不报内部错误 | emy-test 回复 + 日志无 ERROR |
| TC-A2 | 正常路径·基准 | 旧引擎 L1 查询 | 同上 | `emy-test --managed --llm --message "查询最近的事件" --sender "张正宏" --sender-id "ce996655-d346-4c43-a4ac-5da60dc20e2b"` | 正常返回事件列表或说明 | emy-test 回复 |

### 分组 B：langgraph 引擎核心验证

| 编号 | 分类 | 测试用例 | 前置条件 | 输入/操作 | 预期行为 | 验证方式 |
|------|------|---------|---------|-----------|---------|---------|
| TC-B1 | 正常路径 | 新引擎 L1 查询：查询最近事件 | workitem_engine=langgraph + docker restart | 同 TC-A2（sender=张正宏） | 返回事件列表或说明，行为与旧引擎一致 | emy-test 回复 + 日志含 `LangGraph engine built: 5 nodes` |
| TC-B2 | 正常路径 | 新引擎 L2 录入：创建事件 | 同上 | 同 TC-A1 | 事件创建成功，行为与旧引擎一致 | emy-test 回复 + DB 验证 events 表新增 1 行 |
| TC-B3 | 纠错闭环 | 新引擎 L3 高风险：废弃节点 → 触发 error_analysis | 同上，需存在可废弃的节点 | `emy-test --managed --llm --message "废弃节点 SG-001" --sender "李景利" --sender-id "25fdc32a-17ad-4978-b9cc-2b19b77e0bfd"` | node3 失败 → error_analysis 触达 → 分类 permanent_failure（L3 已执行）并 abort；不出现 node4 | 日志含 `error_analysis: type=permanent_failure` + `should_abort=True`；回复说明失败原因 |

### 分组 C：error_analysis 专项验证

| 编号 | 分类 | 测试用例 | 前置条件 | 输入/操作 | 预期行为 | 验证方式 |
|------|------|---------|---------|-----------|---------|---------|
| TC-C1 | 代码预分类 | 权限失败 → 代码直接 abort 不调 LLM | langgraph 引擎 | `emy-test --managed --llm --message "返回SG-001的成果" --sender "周文斌" --sender-id "8c316f0c-6adc-4748-a870-780cb0838f3d"` | 因 level=1 权限不足触发 error_analysis → 代码预分类 `permission_denied` → 直接 abort，**不调 LLM** | 日志含 `code-classified as PERMISSION_DENIED (no LLM)`；LLM trace **无** `call_category=error_analysis` 的调用 |
| TC-C2 | LLM 错误分析 | 参数错误 → LLM 分析 → 重规划 | langgraph 引擎 | 构造消息使 LLM 生成缺参数的 tool_call | error_analysis 调 LLM 分析 → 分类 `param_error` / `tool_mismatch` → node2 重规划 | LLM trace 有 `call_category=error_analysis`；node2 被调用 2 次 |

### 分组 D：feature flag 回退安全

| 编号 | 分类 | 测试用例 | 前置条件 | 输入/操作 | 预期行为 | 验证方式 |
|------|------|---------|---------|-----------|---------|---------|
| TC-D1 | 回退安全 | 切回 pipeline_bus 后正常 | 切回 pipeline_bus + restart | 同 TC-A1 | 行为与 TC-A1 基准一致，旧引擎正常工作 | emy-test 回复 + 日志无 `LangGraph engine built` |

## 四、验证手段优先级

每条用例的"验证方式"按以下优先级组合：

| 优先级 | 手段 | 证明什么 |
|--------|------|---------|
| 1 | emy-test 回复文本 | 用户视角的功能正确性 |
| 2 | `docker logs emily-core` | 运行时行为（引擎构建、error_analysis 触发、路由决策） |
| 3 | LLM trace (`llm_trace.jsonl`) | LLM 调用链、call_category、token 消耗 |
| 4 | DB 查询（events 表等） | 数据持久化 |

## 五、执行顺序与依赖

```
Phase 0: 环境快照（记录 DB 基线 + 日志时间戳）
  ↓
Phase 1: TC-A1, TC-A2（旧引擎基准，确认现有功能正常）
  ↓  --- 切换 workitem_engine=langgraph + 重启 ---
Phase 2: TC-B1, TC-B2（新引擎正常路径）
Phase 3: TC-B3, TC-C1, TC-C2（error_analysis 纠错闭环）
  ↓  --- 切回 workitem_engine=pipeline_bus + 重启 ---
Phase 4: TC-D1（回退验证）
  ↓
Phase 5: 环境对比 + LLM trace 分析 + 报告生成
```

**切换方式**：`core_config.json` 添加 `"workitem_engine": "langgraph"` → `docker compose restart emily-core`。切回时删除该行或设为 `"pipeline_bus"`。

## 六、通过标准

| 指标 | 目标 |
|------|------|
| 新引擎 L1/L2 行为一致性 | 回复语义等价于旧引擎（允许表述不同，不允许功能缺失） |
| error_analysis 触达 | node3 失败时必有 error_analysis 日志 |
| 代码预分类省 LLM | 权限失败 0 次 error_analysis LLM 调用（LLM trace 验证） |
| 回退安全 | 切回 pipeline_bus 后 TC-A1 复现通过 |
| 容器稳定性 | 全程无 ERROR 日志、无重启 |

---

*本计划基于 2026-07-28 代码现状与 Docker 环境真实数据。测试用户均从 `users` 表提取。*
