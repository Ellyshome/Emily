# OpsMonitor-需求_V1 — 审核意见

> **审核日期**：2026-07-01
> **审核角色**：技术总监、资深架构师、SRE/运维专家
> **原始文档**：[OpsMonitor-需求_V1.md](OpsMonitor-需求_V1.md)
> **审核结论**：✅ 可行

---

## 一、总体评价

这份需求文档是三轮架构讨论的直接产出，将原"ProjectAgent"概念重新定位为 PlanTaskScheduler 的扩展模块，定位准确、边界清晰、实现成本低。全文最精准的设计决策有三：① 不新建 Tick 循环而复用已有调度基础设施；② 明确"不是 Agent"——Phase 1 纯规则引擎、LLM 按需调用不常驻；③ Digest 作为 SessionAgent 的动态项目认知注入。

文档的"第 2.2 节 为什么不是独立 Agent"和"第 10 节 与废弃概念的对应"是同类需求文档中罕见的诚实——直接列出哪些不做、为什么不建独立循环、旧概念和新定位的映射。这两节大幅降低了后续实施计划的决策风险。

需要关注的问题集中在实现细节层面：`deadline` 的 VARCHAR 列日期比较、Digest 注入 SessionAgent 的具体时机、`ProjectNodeRepo` 新增方法的事务一致性。都不是架构性缺陷——实施计划阶段可逐一解决。

---

## 二、分维度审核

### 2.1 战略合理性（I）— 技术总监

**审核结论**：✅ 通过

1. **真实痛点**：`project_nodes` 表无人主动扫描是事实——`ProjectNodeRepo` 提供的全是被动查询（按 project_id 查、按 status 查），没有"找出有问题"的主动查询。需求解决的痛点真实存在。

2. **更简单的办法**：不建新模块、不建新 Tick 循环、复用在 PlanTaskScheduler 的 `_tick()` 中追加步骤——这已经是当前架构约束下最简单的方案了。没有进一步的简化空间。

3. **名实匹配**：叫"Monitor"而非"Agent"，因为 Phase 1 不做自主决策。这比原方案更准确。唯一可商榷的是"Ops"前缀——模块名称暗示运维（Operations），但功能覆盖了 Digest 生成（给 SessionAgent 喂数据）和按需 LLM，超出了传统运维 Monitor 的范围。不过"OpsMonitor"比"ProjectMonitor"或"HealthMonitor"更贴近务实命名，不需要改。

4. **受众匹配**：告警推送目标是节点负责人（`owner_dept_id`）和管理员，Digest 消费者是 SessionAgent。两者都是正确的受众，不涉及之前"EmilyShell 号称用户通道实际是 SRE 工具"的错配问题。

### 2.2 架构合理性（B）— 资深架构师

**审核结论**：✅ 通过

1. **复用 PlanTaskScheduler 的 Tick 循环是正确的**：避免两套锁管理、两套异常处理、两个调度间隔。追加步骤的方式对现有代码侵入最小——只需在 `_tick()` 的 try 块中追加 4 个方法调用。

2. **告警冷却的设计有理有据**：明确标注了"内存字典 + 重启丢失 = 重启后首次 Tick 重新推送"的局限，并给出了可接受的判断依据——运维人员重启时期望看到状态汇总。这个标注值得保留到实施计划中，避免实现时有人提出"为什么不用 Redis"的质疑。

3. **`project_nodes.deadline` 为 VARCHAR 的风险已标注**：需求 §4.2 明确标注了 deadline 列的日期格式问题和 Repo 层预处理要求。建议实施时优先考虑在 Repo 方法内统一转换，而非修改表结构（修改表结构需单独的需求评审）。

4. **一个建议**：OpsMonitor 的失败隔离声明在 §8.1（"新增步骤异常不影响 plan_tasks 维度的 7 个已有步骤"），但代码层面如何实现未明确。建议在实施计划中指定：`_tick()` 中新增步骤使用独立的 try/except 包裹，异常时 logger.error + 继续执行，不抛向上层。

### 2.3 实现可行性（C）— 经验丰富的程序员

**审核结论**：⚠️ 有改进空间（1 个阻塞点）

1. **`ProjectNodeRepo` 新增 4 个方法可行**：当前 Repo 已有 `find_by_status()` 和 `count_children()` 方法，新增的 `find_stale()` / `find_milestones_near_deadline()` / `count_all()` / `count_by_status()` 遵循相同的 `@staticmethod` + `get_session()` + SQLAlchemy query 模式，实现难度低。

2. **Digest 注入 SessionAgent 的时机需要明确（阻塞）**：需求 §5.1 描述了 Digest 的数据流——`_refresh_digest()` → upsert `project_digest` 表 → `_load_session_prompt()` 读取注入。但 `_load_session_prompt()` 目前在模块级执行一次（`_SESSION_SYSTEM_PROMPT = _load_session_prompt()`），是 **import 时即执行的模块级变量**，不会后续重新加载。这意味着 SessionAgent 在整个进程生命周期中使用的是第一次加载的 prompt。如果 Digest 需要随项目推进而更新，必须明确：SessionAgent 是每次 `_recognize_intent()` 时重新注入 Digest，还是只有 `reload_prompt("session")` 时才刷新？建议在需求中增加一节明确：Digest 的注入点是在 SessionAgent 构造 system prompt 时（即 `_recognize_intent()` 调用前），而非加载 prompt 文件时。

3. **LLM 按需调用的冷却可以更简单**：需求设计了 `ops_monitor_llm_analysis_cooldown_minutes=60`，但触发条件本身已经足够稀疏（"5+ 节点同时卡滞"、"完成率周环比下降 >20%"），正常项目运营中这些条件极难在 60 分钟内连续触发。建议实施时先从最简单的冷却开始（内存时间戳），不需要建表。

4. **文件变更清单不完整**：§9.2 缺少 `emily_core/services/plan_task_service.py`——新 Repo 方法的调用可能需要经过 Service 层。如果 `_refresh_digest()` 需要访问 `events` 表（计算"最近 7 天新增事件"），还需要 `EventRepo` 或相应的查询方法。建议在实施计划中确认 Digest 的 SQL 是否跨多张表。

### 2.4 数据设计（D）— 资深架构师

**审核结论**：⚠️ 有改进空间

1. **`project_digest` 表的设计太简**：需求只写了"单行 upsert"。如果未来有多个 project，`project_id` 是否需要作为主键？当前项目中 `projects` 表存在但未使用，但预留 project_id 的成本极低（多一列，不影响单行 upsert 的行为）。建议：`project_digest` 表最小 schema 为 `project_id VARCHAR(100) PK, digest_text TEXT, generated_at VARCHAR(50)`。

2. **`ops_alert_log` 表的冷却意义需澄清**：需求 §6.1 说此表用于"冷却持久化 + 审计"，但 §4.1 又说 Phase 1 用内存字典做冷却。两者的关系不清晰——如果 Phase 1 用内存冷却，那 `ops_alert_log` 就只是审计表（记录已发送的告警），不参与冷却逻辑。建议：需求中明确区分"冷却表"和"审计表"——冷却可以用内存，审计必须持久化。`ops_alert_log` 定位为纯审计表，不做冷却依赖。

3. **deadline VARCHAR 排序风险**：`project_nodes.deadline` 是 VARCHAR(50)，不是 DATE。`SELECT * FROM project_nodes WHERE deadline < :warn_cutoff_iso` 这种字符串比较在格式不一（`2026-07-15` vs `2026/07/15` vs `2026年7月15日`）时会出错。需求已标注此风险，但建议在 §4.2 的 SQL 伪代码中增加一句约束："假设 deadline 统一为 YYYY-MM-DD 格式。Repo 方法需接受格式不统一时可比较转换"。

### 2.5 运维考量 — SRE/运维专家

**审核结论**：✅ 通过（1 个建议）

1. **进程重启导致告警冷却丢失的处理务实**：需求诚实标注了重启行为，且给出可接受的判断（运维人员期望看到状态汇总）。建议额外增加一个指标：`ops_monitor_tick_count`（自重启以来的 Tick 计数），在 `/health` 端点暴露，便于监控 OpsMonitor 是否在正常运行。

2. **告警通过 OutboundEventBus 推送的路径已验证**：`PlanTaskScheduler` 已有 `self._outbound.publish("reply", {...})` 的调用模式，新增步骤直接复用，无需改动 OutboundEventBus 自身。

3. **一个遗漏**：需求没有提及 OpsMonitor 启用/禁用开关的运行时热重载。如果 `ops_monitor_enabled=false` 但系统已启动，是否需要重启？建议在 §8.1 中标注：开关变更需重启 Core 生效（与 PlanTaskScheduler 的 `scheduler_enabled` 行为一致）。

---

## 三、改进建议

### 3.1 必须修改（阻塞性问题）

| # | 问题 | 建议 |
|---|------|------|
| **B1** | Digest 注入时机不明确——`_load_session_prompt()` 是 import 时执行的模块级变量，不会随着 Digest 刷新而自动重新注入 | 在需求中新增 §5.4 "Digest 注入时机"，明确：Digest 不是注入到 prompt 文件，而是在 `SessionAgent._recognize_intent()` 构建 system prompt 时实时读取 `project_digest` 表并注入。这可能需要将 `_SESSION_SYSTEM_PROMPT` 从模块级变量改为每次调用时的懒加载逻辑 |

### 3.2 建议优化（非阻塞）

| # | 优先级 | 问题 | 建议 |
|---|--------|------|------|
| **S1** | 高 | `ops_alert_log` 定位模糊：冷却 vs 审计混在一起 | §6.1 中区分：冷却用内存字典（Phase 1），审计用 `ops_alert_log`（纯记录）。冷却不依赖 DB |
| **S2** | 高 | `project_digest` 表缺少 project_id 列 | 至少定义为 `(project_id VARCHAR(100), digest_text TEXT, generated_at VARCHAR(50))` |
| **S3** | 中 | deadline 的 SQL 伪代码应标注格式假设 | §4.2 SQL 伪代码追加一行："假设 deadline 为 YYYY-MM-DD 格式；Repo 方法内做兼容转换" |
| **S4** | 中 | 文件变更清单缺少可能的 Service/Event Repo | §9.2 追加一行："可能需要新增 EventRepo 查询方法（如 Digest 需要事件统计）" |
| **S5** | 低 | 缺少运行时 Tick 计数指标 | §8.2 追加：`ops_monitor_tick_count` 暴露于 `/health` |
| **S6** | 低 | 开关热重载未说明 | §8.1 追加："配置变更需重启容器生效，与 scheduler_enabled 行为一致" |

---

## 四、替代方案

当前方案在现有架构约束下已是最佳选择——复用 PlanTaskScheduler 的 Tick 循环、追加步骤、纯规则 + 按需 LLM、Digest 注入 SessionAgent。没有需要替代的部分。

唯一可以讨论的是替代实现路径：如果未来多个扫描项（卡滞检测、里程碑预警、健康度、依赖链校验）各自变得复杂，可以考虑从 `_tick()` 中提取为独立的 Monitor 调度器。但那是未来重构的事，当前需求不需要提前考虑。

---

## 五、审核总结

**核心建议**：明确 Digest 注入 SessionAgent 的时机——`_load_session_prompt()` 是模块级一次性变量，需要改为每次 `_recognize_intent()` 时实时读取 `project_digest` 表。

**下一步行动**：
1. 解决 B1：在需求中添加 §5.4 "Digest 注入时机"，澄清注入点在 SessionAgent 构建 system prompt 时而非加载 prompt 文件时
2. 评估 S1~S6 的优先级，选择性纳入 V2 修订
3. 修订完成后进入 `/req-plan` 制定实施计划
4. 实施计划中优先处理 deadline VARCHAR 比较策略 + Event Repo 方法补齐

---
*本报告由 AI 需求审核委员会（技术总监 + 资深架构师 + SRE/运维专家）基于 Emily 项目上下文与行业最佳实践生成。仅供参考，最终决策由人工做出。*
