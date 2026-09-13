# LangGraph 编排内核化 — 现有系统模块拆解清单 V1

> **定位**：对现有 Emily 系统做模块级盘点，逐个给出归属判断（进图 / 出图 / 手动化 / 保留），作为内核迁移与后续逐块实现的输入。
> **日期**：2026-09-11
> **来源**：基于仓库实测调研（session、workitem、tools、scheduler、application、services、repositories、permission 全量走查），结论均标注文件路径与行号
> **说明**：本清单不分配模块编号 `M{N}`。按宪法 §1.2，编号由 req-plan 统一分配；本清单只做归属判断与处置动作。

---

## 一、拆解口径

判断一个模块归哪一类，用四个问题，命中即归属：

| 归档 | 判据 | 处置方向 |
|---|---|---|
| A 进图（编排内核） | 它决定"下一步做什么、走哪条路" | 改写成 StateGraph 节点与条件边 |
| B 出图·能力端口 | 它被调用来读写业务数据或产出成果 | 保留实现，只把对外契约显式化 |
| C 出图·宿主外壳 | 它管的是进程、连接、推送、定时、通道 | 移出内核，独立选成熟组件实现 |
| D 先手动化 / 暂缓 | 只在无人值守时才需要，且与内核耦合浅 | 降级为手动入口，后期以插件回归 |
| E 治理与数据 | 它是权限、审计、台账、存储本身 | 形态不动，只保留并稳定接口 |

一条硬约束贯穿全表：出图的只能是被调用的能力与资源，不能是第二个编排器，否则会退回旧的派发范式。

---

## 二、A 类：进图的模块

| 模块 | 现状 | 处置 |
|---|---|---|
| `session/loop.py` | 自写 ReAct 主循环（`for iteration in range` L238）、计划步进（`while not cursor.is_complete` L289）、会话池 TTL 清扫（`while True` L829） | 主循环与步进改为条件边；TTL 清扫剥离到外壳 |
| `session/capability_plan.py` | `PlanCursor` 手写分层 DAG（级联跳过 L134、分层 `next_runnable` L84-104） | 改为子图加并行分支；粗排能力范围从 SOP 扩到全部能力 |
| `session/orchestrator.py` | 旧链路 DAG 规划（`plan()`、`on_wi_done` 动态追加） | 规划语义并入图；旧链路退役时一并处置 |
| `session/capability_runner.py` | 一个 SOP 一个能力，内部复用 `SessionScheduler._run_one`（L253-258） | 保留为能力子图入口，契约显式化 |
| `session/suspend_registry.py` | 纯内存挂起登记表 | 由 `interrupt()` 加检查点取代，该文件退场 |
| `session/confirm_queue.py`、`session/confirm_dialog.py` | 待确认优先级队列 + 确认对话化（复用 EventApplication） | 确认语义保留，队列由中断状态取代 |
| `session/focus_lock.py` | 焦点软锁绑定 WorkItem id | 待评估：多数场景可由会话状态表达 |
| `session/session_state.py` | 状态枚举与转换表（自述为骨架 L5-6） | 合并或删除 |
| `workitem/scheduler.py` | `run_dag` 层内并行（L129）、`_run_one` 建 BusContext 调图（L247-268） | 保留为薄适配层，编排语义上移到图 |
| `workitem/langgraph_engine/` 全族 | 唯一存在的 StateGraph（`graph.py:44-114`），含 nodes、state、agent/loop、checkpointer、hook_adapter | 作为内核基准形态，其余模块向它对齐 |
| `workitem/workitem.py`、`workitem_state.py` | 状态机定义与转换（L134、L34） | 状态语义保留，流转由图的边表达 |
| `api/sse/outbound.py`、`api/sse/node_events.py` | `while True` SSE 事件循环（L39、L54） | 事件源改用框架事件通道，端点保留在外壳 |

## 三、B 类：出图的能力端口

工具共 45 个（`tools/registry.py` 三段注册：基座 4、业务 24、项目 17，含条件注册）。它们全部是无状态 handler，本身不含循环或状态机，只需要把参数 schema 与读写模式显式化。

| 能力域 | 承载位置 | 备注 |
|---|---|---|
| 查询、知识检索、三书检索、OCR | `tools/query_tool.py`、`knowledge_search_tool.py`、`meta_cognition_tool.py`、`ocr_tool.py` | 只读能力，不受写门禁约束 |
| 事件、任务、会议、文件（12 个工具） | `application/event_app.py` 等 4 个 app + `repositories/*` | 事件有 pending→confirmed/cancelled 轻量状态机 |
| 用户记忆、群记忆 | `services/user_memory_service.py`、`group_memory_service.py` | 群记忆无独立工具入口，仅 Session 内部调用 |
| 全景节点（8 个）+ 节点任务（5 个） | `application/node_app.py`、`services/node_service.py`、`node_state_machine.py` | 四态状态机 + 成果提交 FSM + 签认等级门槛，是领域资产 |
| 专家库（4 个）+ 专家评审 | `services/expert_manual_loader.py`、`repositories/expert_repo.py`；评审实现在 `langgraph_engine/nodes.py:555` | 评审当前是图节点，属强耦合点，见第七节 |
| RAG 录入原子层（parse / extract_table / chunk / embed） | `tools/parse_document_tool.py` 等 | 入库状态机在 `knowledge_chunk_repo.py:184-223` |
| 邮件、聊天归档、待解决问题 | `services/email_service.py`、`chat_archive_service.py`、`pending_issues.py` | 无状态机或仅有文件分区规则 |
| 世界书、认知书、规则书 | `services/world_book_*.py`、`system_description_*.py`、`rule_book_loader.py` | 三书分别在数据库、数据库、文件三处承载 |
| 进化与自省 | `services/evolution/*`、`cognition_drift_detector.py`、`initialization_checker.py` | 规则与补丁各有一套状态机 |
| 文档解析、成果匹配、去重 | `services/file_parser_service.py`、`node_deliverable_matcher.py`、`dedup_checker.py` | 匹配阈值 HIGH 0.85 / MEDIUM 0.6 |

有一处反向依赖必须先切断：`tools/expert_manage_tool.py` 直接 import `langgraph_engine.state.get_bus_context`（L161、L177），能力层反向依赖了图的内部状态，这与 D2 相冲。

## 四、C 类：出图的宿主外壳

| 模块 | 现状 | 处置 |
|---|---|---|
| 会话池（`SessionLoopPool`，`loop.py:734-860`） | 进程内内存，TTL 与并发上限 | 移到外壳，用带过期的外部存储承载 |
| 灰度开关 `session/path_router.py` | 读配置决定新循环或旧链路 | 保留为外壳配置 |
| 出站与节点事件总线 | `outbound_bus.py`、`node_event_bus.py` + 两个 SSE 端点 | 通道保留在外壳，事件改由框架产出 |
| HTTP 与看板 | `api/server.py`、`api/routes/*`、`api/monitor_app.py`、`api/middleware/auth.py` | 外壳不动 |
| API 与配置装配 | `bootstrap.py`、`config.py` | 保持薄，只做装配 |
| 调度引擎 | `scheduler/engine.py`、`service.py`、`application.py`、`handler_registry.py` | 移出内核，见第五节 |
| 脚本面 | `scripts/`、`emily-core/emily_core/scripts/*`、`api/routes/scripts_runner.py`、`static/scripts_tool/` | 保留为运维面 |
| 通道与前端 | `data/plugins/emily_agent`（薄插件）、`wechat-gateway/`、`wechat/` | 外壳不动 |
| 容器与基础设施 | napcat、astrbot、emily-embed、postgres（pgvector）、mitmproxy | 基础设施不动 |
| 快照采集 | `snapshot/collector.py` | 外壳采集任务 |
| 会话归档写入 | `services/session_archive_writer.py`、`repositories/session_archive_repo.py` | 归 E 类，见第六节 |

## 五、D 类：先手动化与暂缓

这一节的结论有一条与预期不同，先说结论：你举例的"定时执行自检"目前其实没有在定时跑。`scripts/self_check.py` 与 `scripts/check_initialization.py` 在注册表里都是 `auto_run: null`、`scheduling_note: "纯手动"`（`emily-data/config/scripts_registry.yaml:779-793`、`:133-147`），全库没有任何脚本被调度器触发。

真正在跑的"自检"是调度作业 `system_health_check`（`scheduler/jobs/health_check.py:12-40`，内容是 DB `SELECT 1`）。它的声明写在 `emily-data/config/scheduler_config.json:32-40`，但该文件没有任何代码读取，真实作业行来自数据库表 `scheduler_jobs`。

| 项目 | 现状 | 处置建议 |
|---|---|---|
| `system_health_check` 定时作业 | handler 注册于 `__init__.py:584-588`，由引擎按 tick 扫描 DB 作业行触发 | 把手动化目标落在这里：DB 作业行置 `INACTIVE`，注册保留，另给一个手动入口 |
| **更正（M9 实测 2026-09-12）** | `scheduler_jobs` 实际仅 3 行（每周进度汇报创建、每日晨报、文件过期提醒），**不含自检作业行** | 自检并未在定时运行，"定时"前提不成立，无作业行可停用；另 `InitializationChecker` 的 T4-5 判据查 `action_type="morning_report"` 而实际为 `generate_morning_report`，**恒不可能通过**，已解耦为「调度能力可用（存在 ACTIVE 作业）」 |
| 其余作业 handler | 共 12 个（晨报、节点提醒、会话巡检、RAG 入库、日洞察、规则归纳、补丁验证、世界书更新、认知书更新、每日文件盘点、周进度建节点、数据同步、Webhook） | 逐个判断：真需要无人值守的保留，其余手动化 |
| `data_sync`、`webhook` | 两个恒返回失败的空实现 | 删除候选 |
| `check_file_expiry` 作业行 | DB 种子 JOB-003 已置 INACTIVE，且没有对应 handler | 删除候选 |
| 调度 Hook | `SchedulerHookRegistry` 已创建但零注册（`__init__.py:547`） | 删除或补上真实 Hook，不留死开关 |
| `maintain_node_template_index` | 唯一 `auto_run: bootstrap` 的脚本，启动时子进程执行 | 保留或改手动 |
| 调度引擎本体 | `asyncio.ensure_future` 常驻（`__init__.py:654`），Advisory Lock 加周期幂等 | 移出内核，需要时才启动 |
| 旧会话链路 | `session/session_agent.py`（意图识别 + 拆分 + DAG 派发） | 灰度收敛后退役，不长期双轨 |
| 死代码 | `workitem/pipeline/bus.py`（`run()` 无生产调用）、`node.py`、`real_guardian.py`（无调用方） | 清理，或显式标注保留原因 |

手动化要断开的耦合点，以 `system_health_check` 为例：数据库作业行（`scheduler_repo.py:98-106`、`:109-119`）；引擎扫描链（`engine.py:118-124`）；handler 注册（`__init__.py:584-588`）；手动通道虽有 `SchedulerApplication.trigger_job`（`application.py:35-44`），但它仍会写 `scheduler_executions` 与日志表，若要"纯手动不写调度表"，需要另给独立入口而不是复用它。

还有一条隐蔽耦合值得注意：`InitializationChecker` 的 T4-5 判定会去查 `scheduler_job_logs` 中晨报作业是否成功（`initialization_checker.py:227-234`）。也就是说自检结果依赖某个定时作业的日志，这正是你说的"定时与内部耦合"，手动化时要把这条一起解开。

## 六、E 类：治理与数据层

| 模块 | 现状 | 处置 |
|---|---|---|
| 权限判定 | `permission/auth_engine.py`（三维短路 DENY > 授权 > 矩阵）、`level.py`（6 级树形继承）、`code_compiler.py`（密级）、`cache.py` | 语义全部保留，接口不变 |
| 行级隔离 | `permission/row_security.py`，对 events、tasks、files、messages 注入 `company_id` 过滤 | 保留；对复杂查询 fail-open（L116-121）是待收敛点 |
| 审计与业务事件 | `pipeline/hook.py` 的 AuditHook、`business_event_logs` | 保留，随图边界重新挂点 |
| 台账与归档 | `events`/`tasks`/`meetings`/`files`/`messages` 等表、`session_archive_writer` | 形态不动 |
| 数据访问层 | `repositories/`（纯 DAO，业务规则一律上移） | 不动，图只经能力端口访问 |

## 七、需要先决策的两处

第一处是专家评审的归属。它现在是执行图内的节点（`graph.py:62-96` 注册并连边，`nodes.py:555` 实现），同时 `WorkItem` 上有 `expert_id`、`expert_required` 三个字段，路由闭包按字段决定是否进评审（`graph.py:126-172`）。它究竟是"内核编排的一个判定分支"还是"一个可独立调用的能力"，会直接影响图的结构，建议在计划阶段优先定。

第二处是权限门禁的表达方式。现在门禁有三层：图内 Hook 桥接（`hook_adapter.py`）、能力装配时的工具裁剪（`tool_adapter.py`）、以及执行期兜底（`fallback_policy.py`）。进图之后这三层应当收敛成几个明确的判定节点，而不是三处并存。

## 八、处置动作汇总

| 动作 | 对象 |
|---|---|
| 改写成图 | `session/loop.py` 主循环、`capability_plan.py` 粗排、`orchestrator.py` 规划、`session_state.py` |
| 保留为能力并显式化契约 | 45 个工具、6 个 application、全部领域 services |
| 剥离到外壳 | 会话池、事件通道、SSE 端点、调度引擎、脚本面、快照采集、通道与容器 |
| 由框架能力取代 | `suspend_registry.py`（中断）、`confirm_queue.py`（中断状态）、SSE 自写循环（框架事件流） |
| 降级为手动 | `system_health_check` 及不必要常驻的作业 handler |
| 清理 | `pipeline/bus.py`、`pipeline/node.py`、`real_guardian.py`、`data_sync.py`、`webhook.py`、无 handler 的作业行、零注册的调度 Hook |
| 切断反向依赖 | `tools/expert_manage_tool.py` 对 `langgraph_engine.state` 的直接引用 |
| 退役 | 旧链路 `session/session_agent.py`（灰度收敛后） |

## 九、顺序建议

先做切口最小的三件事，它们都不动内核形态却立刻降低耦合：清理死代码与空实现、把 `system_health_check` 一类作业改手动并解开 `InitializationChecker` 对作业日志的依赖、切断工具层对图内部状态的引用。

再进契约冻结（能力 schema 与读写模式显式化），因为粗排要覆盖全部能力，前提是每个能力都有可校验的入参。

最后才动图化与外壳剥离，且外壳剥离建议与图化分开落地，避免一次变更同时改执行形态与运行环境。

## 十、本轮调研附带发现

| 发现 | 说明 |
|---|---|
| 配置与实现不一致 | `emily-data/config/scheduler_config.json` 全库无代码读取，真实作业在 DB 表；配置里的 `enabled` 字段不生效 |
| 静态清单漂移 | `infrastructure/tools_consistency.py` 列出 40 个工具，运行时实际最多 45 个，ocr、parse_document、extract_table、chunk_text、embed_and_index 未纳入 |
| 文档与代码不一致 | `workitem/__init__.py:12` 注释称 PipelineBUS 等已删除，但 `pipeline/bus.py`、`node.py` 仍在 |
| 死开关 | 调度 Hook 注册表零注册；仓库根 `scripts/` 无脚本被调度（仅一个 bootstrap 例外） |
| 缺手动入口 | 调度的手动触发只有程序内 API，`api/routes/` 无调度路由，控制台也没有 |
| 自检受作业影响 | `InitializationChecker` T4-5 依赖晨报作业日志 |
