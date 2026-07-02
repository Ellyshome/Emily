# ProjectAgent-需求规格 — 审核意见

> **审核日期**：2026-07-01
> **审核角色**：资深架构师、经验丰富的后端程序员、SRE/运维专家
> **原始文档**：[ProjectAgent-需求规格.md](ProjectAgent-需求规格.md)
> **审核结论**：⚠️ 需修改后可行

---

## 一、总体评价

这份需求文档整合了 6 份已有设计文档 + 代码现状勘查，产出了一份结构清晰、分阶段推进的需求规格。三层 Agent 作用域补齐的动机充分，与 PlanTaskScheduler 的职责边界区分（时间驱动 vs 状态驱动）是全文最精准的定位描述。Phase 1/2/3 的渐进式路线 + LLM 成本控制表格体现了务实的工程思维——高频操作用纯 SQL，低频深度分析才用 LLM，资金成本可控。

几个问题需要在实施前澄清。最严重的是：**需求假定 SMNodeRepository 已存在并提供 `list_stale()` / `list_milestones_near_deadline()` 等方法，但磁盘上根本没有这个类**（`sm_node_repo.py` 不存在）。这意味着 Phase 1 的所有依赖都是空缺的，需要和实施计划一起补齐。另外 EmilyShell 的审计能力在 v2.0 架构变更中被移除了，但需求文档未体现这一变更——"全权限进入 Docker"的定位与"零审计日志"之间存在张力，运维场景下审计反而是刚需。

整体来看方向正确、分阶段节奏合理，但依赖补缺和审计能力需要在下个版本中处理。

---

## 二、分维度审核

### 2.1 完整性 — 资深架构师

**审核结论**：⚠️ 有改进空间

1. **SMNodeRepository 缺失未标注**：需求多处假定 `SMNodeRepository.list_stale()`、`list_milestones_near_deadline()`、`list_by_status()`、`count()`、`get_downstream_nodes()` 等方法可用，但经代码勘查，`emily_core/repositories/sm_node_repo.py` **文件不存在**。这些查询方法是 Phase 1 的关键路径依赖。建议：
   - 在需求文档 §7.2（复用表）中增加一节"待新增的 Repository 方法"，明确列出需要新增的 SMNodeRepository 方法清单
   - 将 SMNodeRepository.create_base() / .query_methods() 作为 Phase 1 的前置任务（P0-blocking）

2. **邮箱模块依赖已验证**：`emily_core/providers/email/smtp_provider.py` 和 `imap_provider.py` 已存在，需求文档 §6 关于邮箱集成的前置条件成立。但 `ops_startup_report` 表的冷启动报告与邮箱冷启动报告之间的关系未定义——是"存入 DB 的同时发邮件"，还是"DB 存一份、邮件发一份"？建议明确两者关系。

3. **OutboundEventBus 已验证存在**：已确认 `emily_core/outbound_bus.py` 存在且支持 `publish(event_type, data)` 接口。但当前 `event_type` 只定义了 `reply / progress / file_send / session_closed` 四种。告警推送需要新增一种类型（如 `alert`），需求文档未提及这个扩展。建议 §8.2（可观测性）中补充 OutboundEventBus 事件类型扩展说明。

4. **告警冷却重启丢失未评估风险**：Phase 1 的告警冷却用内存字典，进程重启后冷却重置。这意味着容器重启（无论是人为还是 OOM）后，所有卡滞节点会重新推送一遍告警。对于一个卡滞 30 天的节点，每次重启就多一条重复消息。建议至少在需求中标注此风险点，并给出推荐的缓解策略（如"DB 持久化冷却可推迟到 Phase 3，但需用户知晓重启行为"）。

### 2.2 架构合理性 — 资深架构师

**审核结论**：✅ 通过（有 1 个建议优化项）

1. **三层作用域补齐的设计决策正确**：文档最核心的架构决策——新增项目级 Agent 使作用域从两层变为三层——在概念上是正确的。系统确实缺少"无人值守的项目级协调"角色，ProjectAgent 填补了这个空缺。

2. **与 PlanTaskScheduler 的职责边界清晰**：时间驱动 vs 状态驱动的区分精炼且可操作。Phase 3 的"发现→投递 PlanTask→PlanTaskScheduler 持续跟踪"闭环设计是全文最优雅的部分——ProjectAgent 只做一次"发现+创建"，不越界管理后续生命周期。

3. **ops_scheduler 作为可插拔探针框架的定位恰当**：将 Tick 循环从 ProjectAgent 抽出作为独立模块的决策降低了耦合——新增运维检查只需实现 Probe 接口，不改 Tick 核心。但**目录结构规划 (p473-494) 将 `ops/` 标注为 "Phase 3 预留"**，与实际需要（Phase 1 就需要 Tick Scheduler + StaleProbe）矛盾。建议：`ops/` 目录应在 Phase 1 就完整搭建，标注 Phase 1 实际产出，Phase 2/3 仅新增探针子模块。

4. **EmilyShell 作为独立进程访问 DB 的架构值得肯定**：需求文档 §10.1 明确"Shell 通过 Repository 直接访问 DB，不依赖 FastAPI 进程"——这是正确的架构决策（紧急运维通道不能绑在 FastAPI 存活性上）。但 `__main__.py` 自举 Config/DB/Repo/LLMClient 的过程会重复不少 bootstrap 逻辑，与 `emily_core/bootstrap.py` 存在代码重复风险。建议评估是否可复用 `bootstrap.py` 的部分逻辑，而非全量重写。

### 2.3 实现可行性 — 经验丰富的后端程序员

**审核结论**：⚠️ 有改进空间（2 个阻塞性问题）

1. **`sm_node_repo.py` 不存在（阻塞）**：这是最直接的编程事实——需求文档 §7.2 说 SMNodeRepository 已有 `list_stale()` 等方法，但文件不存在。Phase 1 无法在缺少 SMNodeRepository 的情况下实现。**必须作为一种实施计划的前置任务来修复**。建议在需求文档中增加 §7.3 "SMNodeRepository 新增方法清单"，逐方法列出签名和 SQL 映射。

2. **邮件模块路径标注不精确**：需求文档 §6 说复用 `emily_core/infrastructure/email/`，但实际路径是 `emily_core/providers/email/` + `emily_core/services/email_service.py`。Providers 实现 SMTP/IMAP 能力，Service 做薄封装。建议修正文档中的路径引用。

3. **Advisory Lock 键的哈希冲突风险评估**：`hashtext('project_agent:global_tick')` 作为 PostgreSQL advisory lock 的 key 是正确的模式（与 PlanTaskScheduler 使用的 advisory lock 模式一致）。但需求未说明如果已有一个名为 `project_agent:global_tick` 的 PostgreSQL 对象（如一个表），`hashtext()` 会产生相同的哈希值，导致锁冲突。建议在需求中标注使用 `pg_try_advisory_lock()`（非阻塞）而非 `pg_advisory_lock()`（阻塞），避免 Shell 的 `force_tick` 与 ProjectAgent 的 `_tick()` 互相阻塞。

4. **`project_agent_config.py` 是否必要**：遵循项目现有 Config 模式（一个全局 `Config` dataclass + env→config 映射），新增的 5 个配置字段应直接追加到 `emily_core/config.py` 中，而非创建独立的 `project_agent_config.py`。独立文件增加了 Config 管理的复杂度而不增加价值——建议去掉此文件，直接在现有 Config 中添加字段。

### 2.4 数据设计 — 资深架构师

**审核结论**：⚠️ 有改进空间

1. **`ops_finding` 表缺少 `probe_name` 字段**：需求文档 §4.4 的 ProbeFinding 结构未包含 `probe_name`，但实施计划中的 `list_recent_findings` 命令需要按探针名分组展示。建议在 ProbeFinding 中增加 `probe_name: str` 字段，并在需求文档的 §4.4 表中为其增加一行。

2. **5 张新表的事务语义未定义**：需求只说"DB 不可达时探针结果写入本地 JSONL"。但正常路径下的写入顺序（先 `ops_tick_log` → 再 `ops_probe_execution` → 再 `ops_finding`）和事务边界未定义。一次 Tick 只产生一个 DB 事务还是每个探针独立事务？建议明确：一次 Tick = 一个 DB 事务，全部成功才提交，任何探针失败导致该 Tick 全量 fallback 到 JSONL。

3. **复用表依赖 SMNodeRepository 方法（阻塞）**：同 §2.3 所述——SMNodeRepository 不存在，这是数据访问层的结构性缺口。

### 2.5 运维考量 — SRE/运维专家

**审核结论**：⚠️ 有改进空间

1. **EmilyShell 审计缺口是 v2.0 架构变更的遗留问题**：v2.0 实施报告明确记录了"审计 = 无（终端直接操作，无需审计）"的决策。但运维场景下，通过 Shell 执行的危险操作（如 `purge_data`）恰恰最需要审计。建议在 Phase 1 的 Shell 基础版中至少保留本地文件审计日志（最低成本、零依赖），记录时间、命令、操作者。

2. **ProjectAgent 健康检查端点设计良好**：需求文档 §8.2 说"通过 `GET /api/v1/health` 暴露 ProjectAgent 状态"。这复用了现有 `EmilyCore.health()` 的返回值结构，是正确的设计。当前 `health()` 方法已返回包含多个子系统状态的 dict，增加 `project_agent` 字段不改变任何已有 API 签名。

3. **Graceful degradation 策略具体且可操作**：DB 不可达 → JSONL fallback、邮箱不可达 → 跳过 MailProbe、本地文件写入失败 → 仅 warning。这个降级链在运维层面是合理的。但缺少一个关键场景：**如果 ProjectAgent 自身进程崩溃了，谁告诉运维人员？** 建议在需求中增加一个"watchdog"考量：容器启动时，如果 EmilyCore 正常运行但 ProjectAgent 反复退出，至少应在日志中产生明显的 ERROR 级别告警。

---

## 三、改进建议

### 3.1 必须修改（阻塞性问题）

| # | 问题 | 建议修改 |
|---|------|----------|
| **B1** | §7.2 声称 SMNodeRepository 已有 `list_stale()` 等方法，但文件不存在 | 新增 §7.3 "SMNodeRepository 新增方法需求"，逐方法列出签名和 SQL 查询逻辑。并将此作为 Phase 1 的前置条件（优先级 P0-blocking） |
| **B2** | `ops/` 目录标注为"Phase 3 预留"，但 Tick Scheduler + StaleProbe 是 Phase 1 就需要的 | 修正文件结构规划：`ops/scheduler.py`、`ops/probe_base.py`、`ops/probe_registry.py`、`ops/models.py`、`ops/repositories/ops_repo.py`、`ops/probes/stale_probe.py` 应标注为 Phase 1；仅 `ops/probes/health_probe.py`、`ops/probes/dependency_probe.py`、`ops/report_generator.py` 等标注为 Phase 2+ |
| **B3** | 邮件模块路径 `emily_core/infrastructure/email/` 不正确 | 修正为 `emily_core/providers/email/` + `emily_core/services/email_service.py` |

### 3.2 建议优化（非阻塞）

| # | 优先级 | 问题 | 建议 |
|---|--------|------|------|
| **S1** | 高 | 内存冷却重启丢失 → 重复告警 | 在 §3.1.2 末尾增加风险标注："Phase 1 冷却仅内存级，容器重启后所有存量卡滞节点会重新推送一次。可接受的缓解：重启后第一次 Tick 静默（不发告警），从第二次 Tick 开始正常冷却。" |
| **S2** | 高 | EmilyShell v2.0 无审计日志 | 在 §5 中恢复审计要求：至少本地 JSONL 文件审计（零成本），DB 审计作为 Phase 2 的可选项 |
| **S3** | 中 | OutboundEventBus 缺少 `alert` 事件类型 | §8.2 补充说明：`event_type` 扩展为 `alert`，告警 payload 结构需定义 |
| **S4** | 中 | `project_agent_config.py` 是否必要 | 评估现有 Config 是否已能容纳 5 个新字段。如果可以，建议去掉独立配置文件，直接在 `config.py` 中添加字段 |
| **S5** | 低 | `ops_finding` 缺少 `probe_name` | §4.4 ProbeFinding 增加 `probe_name: str` 字段 |
| **S6** | 低 | Tick 事务语义未定义 | 建议在 §4.5（优雅降级）中增加一句："一次 Tick 内所有探针共享一个 DB 事务。任一探针异常 → 全量回退到 JSONL fallback" |

---

## 四、替代方案

### 方案 A：先补 SMNodeRepository，再启动 Phase 1（✅ 推荐）

**思路**：Phase 1 的 5 项配置 + Tick 循环骨架 + StaleProbe 全部依赖 SMNodeRepository 的方法。因此 Phase 1 应拆为两个子阶段：
- **Phase 1a**：创建 SMNodeRepository + `list_all()` / `list_stale()` / `list_milestones_near_deadline()` / `list_by_status()` / `count()` 方法。这些方法是纯 SQL 查询，不依赖 ProjectAgent 本身，可以独立开发和测试。
- **Phase 1b**：ProjectAgent 骨架 + Tick 循环 + StaleProbe + 告警冷却。此时 SMNodeRepository 已就绪，开发和测试不受阻塞。

**优势**：依赖可视、可并行开发（SMNodeRepository 由另一开发者同步进行）、每个子阶段都有独立的验收标准
**劣势**：增加了 1 个子阶段，总阶段数从 4 变为 5。但实际总工程量不变——只是更诚实地标注了依赖顺序
**适用场景**：当 SMNodeRepository 的缺失确实构成阻塞时（当前情况）

### 方案 B：Phase 1 内置 SQL，不依赖 SMNodeRepository

**思路**：ProjectAgent 的 StaleDetector 直接使用 SQLAlchemy raw session 查询 `sm_nodes` 表，不通过 Repository 层。所有查询作为 StaleDetector 的私有方法实现。后续 Phase 2 或 3 再提取为 SMNodeRepository 的正式方法。

**优势**：Phase 1 可立即启动，不受 Repository 缺失阻塞。更快的可见进展
**劣势**：违反项目"分层不跳"约束——StaleDetector → 直接 DB 访问绕过了 Repository 层。后续提取为 SMNodeRepository 时有重构成本（虽然不大）
**适用场景**：如果需要极速产出 Phase 1 MVP 且可以接受短期技术债

**推荐**：方案 A。原因是 SMNodeRepository 的缺失就是"需求不够精确"的结果——现在多花 0.5 天补齐 Repository 层，比后续在每个探针里写重复的 SQL 要省得多。

---

## 五、审核总结

**核心建议**：Phase 1 的依赖补缺是当前最紧迫的问题——SMNodeRepository 不存在而需求假定它已存在。建议将 Phase 1 拆为 1a（SMNodeRepository 补齐）+ 1b（ProjectAgent 骨架），并在修订版需求中逐方法列出 SMNodeRepository 的新增 API。

**下一步行动**：
1. 修订需求文档 §7，新增 §7.3 "SMNodeRepository 新增方法清单"，逐方法写出签名和 SQL 逻辑
2. 修正 B1、B2、B3 三个阻塞性问题（路径错误、目录标注错误、SMNodeRepository 假设错误）
3. 评估 S1~S6 的优先级，选择性纳入 v1.1 需求修订
4. 将邮件模块路径修正和 OutboundEventBus 事件类型扩展写入 §8 非功能需求
5. 修订完成后进入 req-plan 制定详细实施计划

---
*本报告由 AI 需求审核委员会（资深架构师 + 后端程序员 + SRE/运维专家）基于 Emily 项目上下文与行业最佳实践生成。仅供参考，最终决策由人工做出。*
