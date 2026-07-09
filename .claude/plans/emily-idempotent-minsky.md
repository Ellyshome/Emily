# 模块拆分改造需求 — 审核报告

> **审核日期**：2026-07-09
> **审核角色**：Emily开发者资深架构师、技术总监、数据库架构师、资深后端工程师
> **原始文档**：[模块拆分改造需求.md](需求文件/全景节点与计划任务/模块拆分改造需求.md)
> **审核结论**：⚠️ 需修改后可行

---

## 一、总体评价

这份需求文档的核心洞察力很强——准确识别了 PlanTask 与 Node 模块在「业务任务追踪」维度的重叠，并提出了「业务归节点、系统归调度器」的拆分方向。合规链（`_validate_compliance_chain`）和 VIRTUAL-NODE 的存在本身就是架构在自我纠正的信号，这份文档终于正视了这个问题。

文档的 Node 模块改造部分（§3）设计扎实：`responsible_user_id` 必填+FK约束、`node_type` 三级分类、`submission_status` 状态机、双重驱动模型——这些都是经过推敲的设计，与 Node 现有架构契合度高。

但调度器部分（§4）存在结构性缺陷：**SchedulerJob 仍采用封闭 `action_type` 枚举，与用户明确提出的「总线+Hook模式」需求矛盾**。此外，节点截止时间提醒的去向未解决，数据迁移的数据质量问题被低估，`node_type` 缺少行为约束。这些都是实施前必须解决的阻塞性问题。

---

## 二、分维度审核

### 2.1 B. 架构合理性 — Emily开发者资深架构师

**审核结论**：⚠️ 有改进空间

**1. 调度器架构与「总线+Hook」需求矛盾**

§4.4 将 `action_type` 设计为封闭枚举（`generate_morning_report`、`cleanup_expired_sessions` 等6种），这意味着新增一个调度动作需要修改核心代码——这与 PipelineBUS 的声明式注册模式完全相反。用户明确提出希望「方便模块化增加/移除计划工作」，当前设计无法满足。

**建议**：采用与 PipelineBUS 一致的 **JobHandler 注册表 + 声明式配置** 模式：

```python
# 调度动作处理器基类（类似 BusinessFlowTool）
class SchedulerJobHandler(ABC):
    action_type: str          # 唯一标识
    description: str          # 一句话描述

    @abstractmethod
    async def execute(self, params: dict) -> JobResult: ...

# 注册表（类似 BusinessFlowToolRegistry）
class JobHandlerRegistry:
    def register(self, handler: SchedulerJobHandler) -> None: ...
    def get(self, action_type: str) -> SchedulerJobHandler | None: ...

# 每个 handler 独立文件（scheduler/jobs/morning_report.py）
class MorningReportHandler(SchedulerJobHandler):
    action_type = "generate_morning_report"
    async def execute(self, params: dict) -> JobResult: ...

# 声明式配置（scheduler_config.json）
{
  "jobs": [
    {"name": "晨报", "action_type": "generate_morning_report",
     "cron": "0 7 * * *", "params": {"push_to_group": "项目群"}},
    {"name": "Session清理", "action_type": "cleanup_expired_sessions",
     "interval_seconds": 3600, "params": {"max_idle_minutes": 120}}
  ]
}
```

新增调度动作 = 写一个 Handler 类 + 加一行配置，**不改调度器核心代码**。

**2. 节点截止时间提醒的去向未解决**

当前 PlanTaskScheduler 的核心职责之一是 `handle_overdue_tasks()`（超时提醒）和 `handle_near_deadline_tasks()`（临期提醒）。改造后节点的 `deadline` 字段仍有截止时间，但文档未说明谁来负责提醒。调度器「不持有业务状态」的原则与「节点到期需要提醒」的需求存在冲突。

**建议**：在调度器中增加一个通用 `check_node_deadlines` 动作 handler，它不持有业务状态，而是按调度周期调用 NodeService 查询即将到期/已超期的节点，然后通过 OutboundEventBus 推送提醒。调度器只负责「定时查、定时推」，不持久化提醒状态。

**3. `node_type` 缺少行为约束**

`TASK` 定义为「具体任务（叶子）」，但数据模型层面没有任何约束阻止一个 TASK 节点拥有子节点。如果 TASK 可以挂子节点，它就不是叶子。

**建议**：在 `NodeService.create_node()` 中增加校验——当 `parent_node_id` 指向一个 `TASK` 类型节点时拒绝创建（TASK 不可作为父节点）。

### 2.2 D. 数据设计 — 数据库架构师

**审核结论**：⚠️ 有改进空间

**1. 现有节点 `responsible_user_id` 回填的数据质量风险被低估**

文档说「迁移脚本：从 `creator_id` 回填」，但当前 `project_nodes` 表中 `creator_id` 可能为空或指向已删除用户。如果直接加 NOT NULL + FK 约束，迁移会因数据质量失败。

**建议**：迁移分三步：
1. 先加 `responsible_user_id` 列（nullable，无FK）
2. 执行回填脚本：`creator_id` 有效则用 `creator_id`，否则查找 `owner_dept_id` 对应部门负责人，再否则设为系统管理员 UUID
3. 回填完成后加 NOT NULL + FK 约束

**2. 现有 `node_deliverables` 的 `submission_status` 默认值不合理**

所有现有成果默认 `PENDING`，但很多已有 `current_amount > 0`（已更新过进度），甚至节点已经是 `COMPLETED`。这些成果的 `submission_status` 应为 `CONFIRMED` 而非 `PENDING`。

**建议**：迁移脚本按以下逻辑回填：
- 节点状态 `COMPLETED` → 成果 `submission_status = 'CONFIRMED'`
- 成果 `current_amount >= target_amount` → `submission_status = 'CONFIRMED'`
- 成果 `current_amount > 0` 且 < target → `submission_status = 'SUBMITTED'`
- 其余 → `PENDING`

**3. `SchedulerExecution` 缺少周期标识**

旧 `PlanTaskInstance` 有 `period_key`（如 `"2024-W25"`、`"2024-M06"`），用于幂等检查和周期追溯。新 `SchedulerExecution` 没有此字段，循环作业（CRON/INTERVAL）无法区分不同周期的执行。

**建议**：在 `SchedulerExecution` 增加 `period_key` 字段。

**4. 新表缺少索引设计**

`SchedulerJob` 和 `SchedulerExecution` 的 DDL 没有定义索引。建议：
- `scheduler_jobs`: `idx_sj_status`（按状态查询活跃作业）、`idx_sj_next_execution`（调度器核心查询）
- `scheduler_executions`: `idx_se_job_status`（按作业+状态查询）、`idx_se_created_at`（按时间范围查询）

### 2.3 C. 实现可行性 — 资深后端工程师

**审核结论**：⚠️ 有改进空间

**1. `create_periodic_node` 引发循环依赖风险**

调度器 handler 需要 import NodeService 来创建节点，而 NodeService 位于 `services/` 层。如果调度器也位于 `services/` 层，同层模块互相依赖是可以的（当前 PlanTaskService 已经 import node_repo）。但如果调度器未来需要调用其他 service，需要明确依赖边界。

**建议**：调度器的 handler 通过接口注入依赖（类似 WorkItemAgent 的 injector 模式），而非直接 import Service 类。这样调度器核心不依赖任何具体 Service，只有 handler 实现类持有具体依赖。

**2. 迁移阶段 2→3 的切换时机不明确**

文档说「SOP-009 指向新工具」「新创建的业务任务走 Node 路径」，但：
- 旧工具（`record_plan_task` 等）在阶段 2 是否还注册？
- 如果同时注册新旧工具，LLM 路由时如何选择？
- 阶段 2 到阶段 3 的切换条件是什么？（如：所有活跃 PlanTask 实例已迁移完成？）

**建议**：增加明确的切换条件，如「阶段 2 持续至 plan_task_instances 表中 WAITING/SUBMITTED 状态记录数 = 0」。

**3. Application 层缺失**

当前 PlanTask 有 `PlanTaskApplication` 封装业务编排逻辑。文档将其标记为「删除」，但调度器的作业管理（创建作业、激活、执行、查看结果）仍需 Application 层编排。

**建议**：新增 `SchedulerApplication`（或 `SchedulerApp`），负责作业 CRUD、激活/停用、手动触发等编排逻辑，与 Service 层的纯调度执行解耦。

### 2.4 H. 与现有系统一致性 — Emily开发者资深架构师

**审核结论**：✅ 通过（有改进空间）

**1. 分层约束 ✅**
改造后的调用链仍然符合 `API→Core→Session→WorkItem→Application→Service→Repository→DB`，没有越层调用。

**2. Hook 配置兼容性 ⚠️**
当前 `hook_config.json` 中有 `plan_task_match` 类型 hook（[hook.py:114](emily-core/emily_core/workitem/pipeline/hook.py#L114)）。PlanTask 模块重写后此 hook 需要更新或删除，但文档未提及。

**3. BusinessFlowTool 注册 ⚠️**
当前 `__init__.py` 中 `_register_plan_task_tools()` 注册了 4 个工具。重写后新工具的注册时机和注入方式需要与现有 `register_all()` 流程对齐，文档未详细说明。

### 2.5 I. 战略合理性 — 技术总监

**审核结论**：⚠️ 有改进空间

**1. 这解决的是真实问题还是想象的问题？** ✅ 真实问题。
合规链 + VIRTUAL-NODE + 双 Deliverable 不同步——这些都是当前架构的实际痛点，不是为架构对称而制造的需求。

**2. 有没有更简单的办法？** ⚠️ 有一个更保守的替代方案。
当前方案是「彻底拆分：PlanTask 业务部分归 Node，剩余部分重写为 Scheduler」。更保守的做法是「PlanTask 保留为 Node 的薄视图层」——PlanTaskInstance 变成 Node 的投影/视图，不真正删除 PlanTask 表，而是让 PlanTask 的 CRUD 操作直接读写 Node 数据。这避免了大规模数据迁移和工具重写，但会留下双表冗余。**当前方案在架构整洁性上更优，但实施成本更高。需要团队评估工期预算。**

**3. 命名与实际能力是否匹配？** ✅ 匹配。
「全景节点」= 业务进度图（名实一致），「系统调度器」= 时间驱动动作触发器（名实一致）。

**4. 假设的受众是否正确？** ⚠️ 需补充。
文档假设 TASK 节点的交互通过现有 Node API 完成，但当前 Node API 是管理员导向的（审批、批量操作）。TASK 级别的交互（执行人提交成果、发起人确认/退回）需要更轻量的 API 路径——可能需要专门的「我的任务」端点，而非复用管理员级别的 `/api/v1/nodes/...` 路由。

---

## 三、改进建议

### 3.1 必须修改（阻塞性问题）

| # | 问题 | 建议 |
|---|------|------|
| 1 | **调度器架构与总线+Hook需求矛盾**：封闭 `action_type` 枚举无法模块化扩展 | 重写 §4 为 **JobHandler 注册表 + 声明式配置 + Hook 拦截** 模式，与 PipelineBUS 对齐 |
| 2 | **节点截止时间提醒去向未解决** | 调度器增加 `check_node_deadlines` handler，定时查询 Node 并推送提醒 |
| 3 | **数据迁移质量风险**：`responsible_user_id` 回填可能因脏数据失败 | 三步迁移：先加列→回填（含兜底逻辑）→再加约束 |
| 4 | **现有 deliverable 的 submission_status 回填逻辑错误** | 按节点状态和成果进度智能回填，不能全默认 PENDING |

### 3.2 建议优化（非阻塞）

| # | 问题 | 建议 |
|---|------|------|
| 5 | `node_type` 缺少行为约束（TASK 不应有子节点） | `create_node()` 增加父节点类型校验 |
| 6 | `SchedulerExecution` 缺少 `period_key` | 增加 `period_key` 字段 |
| 7 | 新表缺索引设计 | 补充 `scheduler_jobs` 和 `scheduler_executions` 的索引 |
| 8 | 迁移阶段切换条件不明确 | 定义量化切换条件（如活跃 PlanTask 实例数 = 0） |
| 9 | 调度器缺 Application 层 | 新增 `SchedulerApplication` |
| 10 | TASK 节点交互路径未设计 | 增加「我的任务」轻量 API 端点 |
| 11 | `hook_config.json` 中 `plan_task_match` hook 遗留 | 文档中标注需同步更新/删除 |
| 12 | `create_periodic_node` 循环依赖风险 | handler 通过接口注入依赖而非直接 import |

---

## 四、替代方案

### 方案 A：当前方案 + 调度器总线改造（✅ 推荐）

- **思路**：按文档执行 Node 扩展，同时将调度器重设计为 JobHandler 注册表 + 声明式配置 + Hook 拦截模式，与 PipelineBUS 架构一致
- **优势**：架构最整洁，彻底消除双系统冗余，调度器可模块化扩展
- **劣势**：实施成本高（Node 扩展 + 调度器重写 + 数据迁移 + SOP 重写 + 工具重注册）
- **适用场景**：有充足工期（预计 2-3 周），希望一劳永逸

### 方案 B：PlanTask 作为 Node 的薄视图层

- **思路**：不删除 PlanTask 表，而是让 PlanTaskService 的 CRUD 操作底层读写 Node 数据。PlanTaskInstance 成为 TASK 节点的投影，对外保持旧 API 不变
- **优势**：实施成本低（只改 PlanTaskService 内部实现），旧工具/API/SOP 不用改，零数据迁移
- **劣势**：双表冗余持续存在，合规链虽简化但未根除，长期维护成本高
- **适用场景**：工期紧张（预计 3-5 天），需要快速止血

## 五、方案对比

| 维度 | 方案 A（当前+总线改造） | 方案 B（薄视图层） |
|------|------------------------|-------------------|
| 架构整洁度 | 高——单一真相源 | 中——双表冗余 |
| 实施工期 | 2-3 周 | 3-5 天 |
| 数据迁移风险 | 中——需三步迁移 | 无——不改表 |
| 调度器扩展性 | 高——JobHandler 注册 | 低——仍为封闭枚举 |
| 长期维护成本 | 低 | 高——双系统持续维护 |
| 用户认知改善 | 显著——统一入口 | 有限——旧入口仍在 |
| 向后兼容性 | 需过渡期 | 完全兼容 |

---

## 六、审核总结

**核心建议**：采用方案 A（当前方案 + 调度器总线改造），但必须先解决 4 个阻塞性问题——调度器重设计为总线+Hook、节点提醒机制补位、数据迁移三步走、deliverable 智能回填。

**下一步行动**：
1. 将 §4 调度器部分重写为 JobHandler 注册表 + 声明式配置 + Hook 模式（对齐 PipelineBUS）
2. 补充节点截止时间提醒机制设计（`check_node_deadlines` handler）
3. 细化数据迁移方案（三步迁移 + deliverable 智能回填）
4. 补充 TASK 节点的轻量 API 路径设计
5. 修订需求文档后进行二次审核

---
*本报告由 AI 需求审核委员会生成，基于 Emily 项目上下文与行业最佳实践。仅供参考，最终决策由人工做出。*
