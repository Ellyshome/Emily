# PRD：图检查点持久化（修复 MemorySaver 内存丢失缺陷）

> **来源**：`需求/Pi_Agent对比分析报告_业务设计篇.md` §6-1（339 行）
> **级别**：🔴 死代码/断线（可靠性硬伤）｜**优先级**：P0
> **日期**：2026-09-11 ｜ **状态**：待评审

---

## 1. 问题核实（当前代码复验）

**结论：缺陷存在。** 证据如下（2026-09-11 在当前工作树核实）：

| 项 | 证据 |
|---|---|
| 检查点用内存实现 | `emily-core/emily_core/workitem/langgraph_engine/graph.py:13` `from langgraph.checkpoint.memory import MemorySaver`；`:110` `graph = gs.compile(checkpointer=MemorySaver())` |
| 图执行依赖 thread_id 持久性 | `workitem/scheduler.py:338` `config = {"configurable": {"thread_id": context.pipeline_run_id}}` |
| resume 依赖检查点存续 | `scheduler.py:340-344` 续接走 `graph.ainvoke(Command(resume=resume_input), config=config)`；`_check_interrupt`（`:370-387`）用 `graph.get_state(config)` 读快照 |
| interrupt 挂起点 | `langgraph_engine/agent/loop.py:226-232`，`tool_node` 的 ask_user 分支调 `interrupt(question)`，WorkItem 被标为 `WAITING_FOR_INPUT`（问题文本已持久化到 DB，但断点上下文没有） |
| 依赖现状 | `requirements.txt:28` 仅 `langgraph>=0.2.0`；环境实测 `langgraph 1.2.2` + `langgraph-checkpoint 4.1.1`，**未安装** `langgraph-checkpoint-postgres` / `langgraph-checkpoint-sqlite` |
| 可复用的基础设施 | Postgres 容器已存在（`docker-compose-minimal.yml:62-64`，`pgvector/pgvector:pg16`，`emily-postgres`），连接配置已有 `config.py:59` `database_url` |

## 2. 问题分析（原因）

1. **半套持久化语义**：interrupt/resume 的"业务侧"做了——问题文本、WAITING_FOR_INPUT 状态都写进了 DB；但 LangGraph 的"技术侧"（断点所在节点、已积累的图 state）只存在于 `MemorySaver` 的进程内存。两半不对称，导致"看起来可续跑、重启后实际不可续跑"。
2. **重启即失效的具体表现**：进程重启后，`WAITING_FOR_INPUT` 的 WorkItem 在 DB 中仍等待用户回复；用户回复到达时 `Command(resume=...)` 指向的 thread 在 `MemorySaver` 中已不存在，resume 必然失败，该 WorkItem 永久卡死（只能人工改状态兜底）。
3. **内存无界增长**：每个 `pipeline_run_id` 都会在 `MemorySaver` 中留一个 thread（含全部图 state），进程不重启就不释放，长跑内存只增不减。
4. **成因**：图引擎按 LangGraph 教程默认写法 compile（MemorySaver 是最省事的默认），后续接上 interrupt/resume 时未回头审视 checkpointer 的生命周期与进程生命周期不一致的问题。

## 3. Bug 复现方法（兼作修复后自验证）

### 3.1 机制级复现（无 LLM/DB 依赖，秒级，确定性）

新建 `scripts/repro_checkpoint_persistence.py`，用与 `graph.py:110` 完全同构的 compile 方式（`MemorySaver` + 固定 thread_id），把"interrupt → 进程重启 → resume"拆成两次独立进程运行：

```python
"""repro_checkpoint_persistence.py — 复现 MemorySaver 不跨进程存续缺陷。

用法:
  python scripts/repro_checkpoint_persistence.py            # Phase A: 跑到 interrupt 后退出（模拟服务进程）
  python scripts/repro_checkpoint_persistence.py --resume   # Phase B: 全新进程 resume（缺陷在此暴露）

退出码: 0 = 断点可跨进程续跑（修复后预期）; 1 = 缺陷存在（MemorySaver 重启即丢）。
"""
import sys
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver   # 与 graph.py:13 同源
from langgraph.types import interrupt, Command


class S(TypedDict, total=False):
    answer: str


def tool_node(state: S) -> dict:
    # 与 workitem/langgraph_engine/agent/loop.py:232 的 ask_user→interrupt 同语义
    reply = interrupt("请补充信息")
    return {"answer": reply}


def build():
    g = StateGraph(S)
    g.add_node("tool_node", tool_node)
    g.add_edge(START, "tool_node")
    g.add_edge("tool_node", END)
    # 与 workitem/langgraph_engine/graph.py:110 同构
    return g.compile(checkpointer=MemorySaver())


CFG = {"configurable": {"thread_id": "repro-thread-1"}}


def main() -> int:
    graph = build()
    if "--resume" not in sys.argv:
        graph.invoke({"answer": ""}, CFG)
        print("Phase A: 已挂起于 interrupt，进程即将退出（模拟服务重启）。请用 --resume 重跑。")
        return 0
    # Phase B：全新进程、全新 checkpointer 实例，同 thread_id
    snap = graph.get_state(CFG)
    if not tuple(snap.next or ()):
        print("REPRO OK: 缺陷复现 — 重启后 thread 不存在，resume 无法落到断点"
              "（MemorySaver 是进程内存）。对应生产：WAITING_FOR_INPUT 的 WI 永久卡死。")
        return 1
    result = graph.invoke(Command(resume="用户回复"), CFG)
    print("PASS: 断点跨进程续跑成功，answer =", result.get("answer"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**当前（缺陷在）预期结果**：

- 第二次运行 `--resume`（新进程）输出 `REPRO OK: 缺陷复现 …`，退出码 1——`get_state` 拿到的 `next` 为空，断点彻底丢失。

**修复后预期结果（自验证）**：

- 按 §4.2 将 compile 行改为 `build_checkpointer(...)`（Postgres 后端；同步脚本可用 `PostgresSaver`，或把脚本改 async 用 `AsyncPostgresSaver`），先跑一次无参、再跑 `--resume`：Phase B 能读到 Phase A 的断点并注入 resume 值跑完，输出 `PASS: 断点跨进程续跑成功`，退出码 0。
- 该脚本即 §5 验收标准第 1 条的自验证载体。

### 3.2 生产级复现（真实链路，可选）

1. 触发一个真实 WorkItem，让 agent 调用 `ask_user` 进入 `WAITING_FOR_INPUT`（日志出现 `Scheduler interrupt detected: WI … question=…`）；
2. `docker restart emily-core`；
3. 用户回复该问题；
4. **当前**：`Command(resume=...)` 无法命中断点，WI 永久停留 `WAITING_FOR_INPUT`；**修复后**：从断点续跑至终态。

## 4. 修复方案

### 4.1 方案选型

| 方案 | 说明 | 判断 |
|---|---|---|
| A. Postgres checkpointer（**采纳**） | `AsyncPostgresSaver` 复用已有 `emily-postgres` 容器 | 与主库同生命周期，运维零新增；异步 API 与 `ainvoke` 匹配 |
| B. SQLite checkpointer | `AsyncSqliteSaver` 落盘文件 | 可用，但引入第二存储介质，容器部署需挂卷；仅作 Docker 外本地开发的备选 |
| C. 仅改文档声明"不支持重启续跑" | 不改代码 | 否决：缺陷本质是可靠性硬伤，绕不过 |

### 4.2 实施步骤

1. **加依赖**：`emily-core/requirements.txt` 增加 `langgraph-checkpoint-postgres>=2.0`（与 langgraph 1.2.2 兼容版本，落地时以 pip 解析为准）。
2. **新建工厂**：`workitem/langgraph_engine/checkpointer.py`，提供 `build_checkpointer(config)`：
   - `database_url` 非空或默认参数可连 `emily-postgres` 时 → `AsyncPostgresSaver.from_conn_string(...)`；
   - 连接失败/显式配置禁用时 → 回退 `MemorySaver()` 并打 WARNING（保留现行为作兜底，不阻断启动）。
3. **建表迁移**：在 EmilyCore 启动建图处（`_build_pipeline_bus` 一带）调用 `await checkpointer.setup()`（自动建 `checkpoints` / `checkpoint_writes` / `checkpoint_blobs` 表，幂等）。
4. **替换 compile**：`graph.py:110` 改为 `gs.compile(checkpointer=build_checkpointer(config))`，同时更新 `:112` 的日志文案。
5. **checkpoint 清理（防膨胀）**：WorkItem 进入 `done/failed` 终态后，通过 `adelete_thread(thread_id)` 删除对应检查点（可放在 scheduler 终态落库处，try/except 包裹，失败仅告警）。
6. **重启恢复巡检（补业务侧闭环）**：启动时扫描 DB 中处于 `WAITING_FOR_INPUT` 的 WorkItem，将"检查点已不存在（重启前遗留）"的条目标记为失败/待人工，避免用户回复后撞 resume 异常。

### 4.3 不做什么

- 不引入 Redis 等新中间件；不改动图结构与节点逻辑；不做跨机共享调度（单实例部署下无此需求）。

## 5. 验收标准

1. 自动化验证：`ask_user` 触发 interrupt → **重启 emily-core 进程** → 用户回复 → WorkItem 从断点续跑至终态，且断点前已产生的图 state（已执行的工具结果等）不重复执行。
2. `WAITING_FOR_INPUT` 的 WorkItem 在无重启场景下 resume 行为与现在完全一致（回归）。
3. 终态 WorkItem 的检查点行被清理，`checkpoints` 表不随运行时长无界增长。
4. Postgres 不可达时服务仍可启动（回退 MemorySaver + WARNING 日志），不出现启动崩溃。

## 6. 风险与回滚

- **风险**：checkpoint 写入给主库增加流量（每超级步一次）；`emily-postgres` 单点故障会阻断图执行中段。→ 检查点表与业务表同库已可接受；后续如有需要再拆独立库（`database_url` 可配置）。
- **回滚**：配置开关 `langgraph_checkpointer: str = "postgres" | "memory"`，置 `memory` 即回到现状。

---

## 7. 落地与验证记录（2026-09-11）

### 7.1 实现
- 新增 `emily-core/emily_core/workitem/langgraph_engine/checkpointer.py`：`LazyPostgresCheckpointer`（懒加载 AsyncPostgresSaver，首次异步调用才建连+`setup()` 幂等建表；失败回退 MemorySaver + WARNING）+ `build_checkpointer(config)` + `startup_recovery(core)`。
- `graph.py`：`compile(checkpointer=build_checkpointer(config))`；`scheduler.py`：`_check_interrupt` 改 `await graph.aget_state`（同步 get_state 无法用于异步 saver），新增 `_cleanup_checkpoint` 在 WI 终态删除 thread。
- `api/server.py` lifespan 调 `startup_recovery`；`config.py` 新增 `langgraph_checkpointer`；`bootstrap.py` 加 `EMILY_LANGGRAPH_CHECKPOINTER`、`EMILY_EXPERT_REVIEW_ENABLED` 映射；`requirements.txt` 加 `langgraph-checkpoint-postgres` + `psycopg[binary]` + `psycopg-pool`。

### 7.2 与设计的差异（须知）
1. **懒加载**：Core 初始化是同步的，无法在其中建异步连接，故 checkpointer 用代理懒加载，首次 `ainvoke` 时才真正连库。
2. **脚本用法**（实际落地，非 PRD 内联草案）：`python scripts/repro_checkpoint_persistence.py`（Phase A）→ `--resume`（新进程 Phase B）；`--legacy-memory` 用 MemorySaver 模拟修复前；连接串 `--conn` / `EMILY_DATABASE_URL`，默认 `127.0.0.1:25432`。Windows 下脚本自动切 SelectorEventLoop 并用单连接（psycopg_pool 在 Proactor 下不可用）。
3. **计划第 6 步"按 WI 标记失败"未实现**：核实确认 `WorkItem` 无 DB 表/repo（纯内存对象），进程重启后无人持有挂起 WI，无法按其标记。`startup_recovery` 改为：急切初始化 + 按 `checkpoint_resume_window_seconds` 清扫超期残留 thread。**"重启自动续跑"仍待 WorkItem 持久化补齐**（另立需求）。

### 7.3 实测结果
| 场景 | 命令 | 结果 |
|---|---|---|
| 修复前模拟 | `repro_checkpoint_persistence.py --legacy-memory --resume` | `REPRO OK`，退出码 1（断点丢失） |
| 修复后跨进程 | `repro_checkpoint_persistence.py` → 新进程 `--resume` | `PASS: 断点跨进程续跑成功，answer='用户回复'`，退出码 0 |
| 生产建图 | `bootstrap.init` + `_ensure_initialized` | checkpointer=`LazyPostgresCheckpointer`，`startup_recovery` 返回 `{ready:True, degraded:False}` |
| 回归 | `pytest tests/` | 21 passed（含新增 12 项） |
