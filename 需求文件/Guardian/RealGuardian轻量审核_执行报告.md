# RealGuardian轻量审核 — 实施执行报告

> **生成日期**: 2026-07-01
> **计划文档**: [RealGuardian轻量审核_计划_V1.md](RealGuardian轻量审核_计划_V1.md)
> **需求文档**: [RealGuardian轻量审核-实施计划.md](RealGuardian轻量审核-实施计划.md)
> **执行状态**: ✅ 全部完成

---

## 一、执行概要

四个 Phase 全部完成。10 个文件变更（1 新建 / 7 修改 / 3 删除），0 行代码触及 Session/PipelineBUS/Hook 层。

---

## 二、模块功能

### 2.1 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `RealGuardian` | `workitem/pipeline/real_guardian.py` | 轻量输出审核类，单次 LLM `chat_json()` 调用，不维护跨请求状态 |
| `GuardianNote` | 同上 | dataclass — `source`（`"step:step-02"` / `"reply"`）+ `issues`（问题描述列表） |
| `GuardianVerdict` | `workitem/pipeline/interfaces/guardian.py` | 仅保留三态枚举（PASS/FLAG/REJECT），Guardian ABC 已移除 |

### 2.2 两个审核方法

```
RealGuardian
├── async review_step(step_result) → GuardianNote | None
│     审核单个 StepResult（工具调用/RAG引用/DB操作）。
│     在 node3 中 asyncio.create_task() 后台并进执行。
│
└── async review_reply(draft_reply, work_item) → GuardianNote | None
      审核最终回复草稿（幻觉/矛盾/越权/敏感信息）。
      在 node4 中直接 await 调用。
```

**审核维度**：

| review_step | review_reply |
|-------------|-------------|
| 虚构数据（与工具返回对比） | 幻觉（编造事实/编号/项目名） |
| RAG 错误引用/断章取义 | 与执行步骤结果矛盾 |
| 输出结论与工具返回矛盾 | 越权泄露（暴露无权查看的信息） |
| — | 敏感信息（密钥/密码/内部IP） |

### 2.3 嵌入位置

```
WorkItemAgent
├── node3_execute  ──→ Guardian 并进审核（asyncio.create_task + gather）
│     for each StepResult:
│       后台 task = create_task(guardian.review_step(sr))
│     await gather(所有 task)
│     发现问题 → sr.guardian = GuardianStepVerdict(verdict="FLAG", reason="...")
│              → wi.add_warning(f"[{note.source}] {issue}")
│
└── node4_summary  ──→ Guardian 出站审核
      draft = 组装回复
      note = await guardian.review_reply(draft, wi)
      发现问题 → wi.add_warning(f"[reply] {issue}")
      回复末尾 → draft + "\n\n⚠️ Emily 提醒（系统自动审核标记，供参考）:\n  • ..."
```

### 2.4 启用逻辑

```
LLM client 可用 → self._guardian = RealGuardian(llm_client=self._llm)
LLM client 不可用 → self._guardian = None（静默跳过，不抛异常）

无模式开关，无需环境变量配置。
```

### 2.5 异常策略

- **LLM 调用失败**：`return None`，日志 `debug` 级别记录
- **gather 异常**：`logger.warning` 记录，不阻断 node3 后续逻辑
- **整体原则**：**fail-open** — Guardian 失败永远不阻断主流程

---

## 三、改动文件清单

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `emily-core/emily_core/workitem/pipeline/real_guardian.py` | **新建** | RealGuardian + GuardianNote + 两个 system prompt |
| 2 | `emily-core/emily_core/workitem/workitem_agent.py` | **修改** | import 区 + 构造函数（Guardian 自动启用）+ node3（并进）+ node4（追加标记）+ 文档注释更新 |
| 3 | `emily-core/emily_core/workitem/pipeline/interfaces/guardian.py` | **替换** | 从 ABC + 枚举 → 仅枚举（GuardianVerdict 保留） |
| 4 | `emily-core/emily_core/workitem/pipeline/interfaces/__init__.py` | **修改** | 移除 Guardian 导出 |
| 5 | `emily-core/emily_core/workitem/pipeline/mocks/__init__.py` | **修改** | 移除 MockGuardian 导入和导出，更新已移除注释 |
| 6 | `emily-core/emily_core/workitem/pipeline/mocks/mock_guardian.py` | **删除** | — |
| 7 | `emily-core/emily_core/config.py` | **修改** | 删除 `guardian_mode` 字段 |
| 8 | `emily-core/emily_core/bootstrap.py` | **修改** | 删除 `EMILY_GUARDIAN_MODE` 环境变量映射 |
| 9 | `emily-core/emily_core/workitem/pipeline/bus.py` | **修改** | 更新一行注释（MockGuardian → RealGuardian） |
| 10 | `scripts/smoke_test.py` | **修改** | 删除对 `config.guardian_mode` 的引用 |

**删除的残留文件**（3 个）：

```
emily-core/emily_core/workitem/pipeline/mocks/mock_guardian.py    ← 源码
emily-core/emily_core/agent/__pycache__/guardian_agent.cpython-312.pyc   ← 冷备
emily-core/emily_core/agent/__pycache__/guardian_review.cpython-312.pyc  ← 冷备
```

**未触动的文件**（按需求）：
- `session/session_agent.py`
- `adapters/session/session_pool.py`
- `adapters/session/session_factory.py`
- `workitem/pipeline/bus.py`（仅注释变更）
- `workitem/pipeline/hook.py`
- `workitem/pipeline/context.py`
- `emily-core/emily_core/__init__.py`
- `hook_config.json`

---

## 四、设计决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | Guardian 为具体类而非 ABC | 只有一个实现（轻量单次 LLM 判定），不需要抽象层。YAGNI |
| 2 | 无模式开关（不设 mock/real/review） | LLM 可用则自动启用，不可用则静默跳过。模式开关增加配置维度却不增加能力 |
| 3 | `GuardianNote`（source + issues）而非 `GuardianVerdict`（PASS/FLAG/REJECT） | 新设计只需标记问题，不做拦截/替换。语义更匹配 |
| 4 | `GuardianStepVerdict` 保留 | 被 StepResult.guardian 字段和 mock_execution.py 引用，属于 execution 协议层 |
| 5 | node3 用 `asyncio.create_task()` + `gather()` 而非 `await` | 审核 Step1 与执行 Step2 并行，减少用户等待 |
| 6 | 冷备 pyc 一并删除 | 总线已深度重构，签名和调用路径不可用。pyc 不可读，参考价值为零 |
| 7 | 不放入 `real/` 子目录 | 只有一个文件 ~200 行，不值得建目录 |

---

## 五、验证结果

### 5.1 导入链验证

```
✅ RealGuardian imported OK
✅ GuardianNote fields: ['source', 'issues']
✅ GuardianVerdict: [PASS, FLAG, REJECT]
✅ Guardian ABC: removed
✅ MockGuardian: removed
✅ guardian_mode: removed from config
```

### 5.2 node3 并进模型验证

```
✅ create_task = True
✅ gather = True
✅ guardian_mode removed from node3 source
```

### 5.3 node4 追加标记验证

```
✅ warning_text append = True
✅ verdict_val removed from node4 source (no more PASS/FLAG/REJECT ternary)
```

### 5.4 烟雾测试

```
✅ [1] 闲聊短路 OK
✅ [2] 任务→WorkItem→4节点BUS OK
✅ [2b] Phase C mock executor OK
✅ [3] SessionPool 复用 OK
✅ [4] 终止 Session OK
✅ 全部冒烟用例通过
```

### 5.5 代码残留检查

```
✅ MockGuardian 关键词仅存在于 mocks/__init__.py 注释（已移除记录）
✅ guardian_mode 关键词 0 处出现在 .py 文件中
✅ 冷备 guardian_*.pyc 已全部删除
```

---

## 六、后续建议（P1/P2，不在本次范围）

| 任务 | 说明 | 预估 |
|------|------|------|
| 生产环境验证 | Docker 内启动 emily-core，配置 LLM API key，通过 emy-test CLI 发送真实消息，观察 Guardian 审核标记 | 0.5 天 |
| 深度审计 Hook | 需要多轮 ReAct 核查时，通过 `after:wi_node4` Hook 异步接入，不阻塞主回复 | 1 天 |
| Guardian 日志持久化 | 将审核结果写入数据库表，支持事后审计分析 | 0.5 天 |
| `__pycache__` 清除 | Docker 内执行 `find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`（bind-mount 不自动刷新） | 1 分钟 |

---

*本报告由 req-plan 执行后生成。*
