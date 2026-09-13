# LangGraph 编排内核化 — 阻断处理记录 V1

> **日期**：2026-09-11 23:30 至 2026-09-12 00:05
> **处理人**：AI 开发助手（按 req-verify 报告 V1 的阻断项执行）
> **对象**：测试报告 V1 中的 B1（检查点回退，高）、B3（运行容器挂载漂移，中）
> **结论**：B1、B3 均已解除并有实测证据；处理过程中另外暴露 3 个更底层的隐患，其中 2 个为高危潜在故障，已登记待处理

---

## 一、B1 检查点回退

### 根因

镜像陈旧。镜像内的 `/app/requirements.txt` 是构建时烤入的旧版本，`grep` 结果只有 `psycopg2-binary>=2.9.0`，**缺少 LangGraph 执行引擎一节的 `langgraph`、`langgraph-checkpoint-postgres`、`psycopg[binary]`、`psycopg-pool`**。镜像里存在的 `langgraph 1.2.10` 及 `langchain-core` 等来自传递依赖，而 Postgres 检查点包与 psycopg v3 完全缺失，因此运行时回退到内存态。

证据：

```
=== IMAGE requirements.txt ===
9:psycopg2-binary>=2.9.0
（无 langgraph / langgraph-checkpoint-postgres / psycopg 行）

=== 处理前启动日志 ===
[WARNING] emily.langgraph.checkpointer: Checkpointer: Postgres unavailable
(No module named 'langgraph.checkpoint.postgres') — falling back to MemorySaver (断点将不跨进程持久化)
```

### 处理

1. 备份现网镜像：`docker tag emily-core:latest emily-core:pre-kernel-20260911`（可一键回滚）。
2. 启动镜像重建：`docker compose -f docker-compose-napcat.yml build emily-core`（已完成，产物 `emily-core:rebuilt-20260912`；该镜像存在新问题 N4，最终未采用）。
3. 为不阻塞验证，先在运行容器内补齐依赖：

```
docker exec emily-core pip install --no-cache-dir \
  'langgraph-checkpoint-postgres>=2.0' 'psycopg[binary]>=3.1' 'psycopg-pool>=3.1'
```

### 验证（实测）

启动日志：

```
[INFO] emily.langgraph.checkpointer: Checkpointer: LazyPostgresCheckpointer (mode=postgres)
[INFO] emily.langgraph.graph: Unified lifecycle graph built: ... checkpointer=postgres
[INFO] emily.langgraph.checkpointer: Checkpointer: AsyncPostgresSaver ready (pool=True)
[INFO] emily.langgraph.checkpointer: Checkpointer startup recovery: scanned=0 swept=0 degraded=False
```

跨进程续跑（`scripts/repro_checkpoint_persistence.py`，两个独立进程）：

```
=== PHASE A ===
Phase A: 已挂起于 interrupt，next=('tool_node',)，进程退出（模拟重启）。
=== PHASE B ===
PASS: 断点跨进程续跑成功，answer='用户回复'
```

数据库侧检查点表已建立：`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations`。

**结论**：PRD 约束 8（挂起必须支持跨进程恢复）与 AC-US-07.1（持久化全路径可用）的前置条件已满足，M5 具备准入条件。

---

## 二、B3 运行容器挂载漂移

### 根因

运行中的 emily-core 由更早版本的 compose 定义创建，实际挂载 19 个，仓库 `docker-compose-napcat.yml` 定义 20 个，缺少 `./scripts:/app/scripts:ro`。

### 处理与验证

执行 `docker compose -f docker-compose-napcat.yml up -d emily-core` 重建容器。

```
=== mount count after recreate ===
20
=== scripts mount now? ===
"D:\\app\\Emily\\scripts:/app/scripts:ro"
=== in-container check ===
wiring_scan.py present: True
```

**结论**：挂载已与仓库定义一致。附带说明：`scripts/wiring_scan.py` 依赖「脚本位于仓库根的子目录」来推导扫描根，容器内 `ROOT` 会推导为 `/app` 而与实际目录结构不符，因此该脚本仍应在宿主执行。

---

## 三、处理过程中暴露的新隐患

| # | 严重度 | 隐患 | 现象与证据 | 建议 |
|---|-------|------|-----------|------|
| N1 | 🔴高 | Postgres 数据目录缺空目录 `pg_logical/snapshots` | 重建容器后 Postgres 进入重启循环：`ERROR: could not open directory "pg_logical/snapshots": No such file or directory` → `FATAL: checkpoint request failed` → `shutting down due to startup process failure` | 空目录不被 git 跟踪，而该数据目录被纳入版本管理。已手工补齐该目录使服务恢复；建议在部署文档或前置脚本中固定"启动前确保该目录存在"，并评估停止用 git 跟踪生产数据目录 |
| N2 | 🟡中 | pip 解析副作用卸掉了基础包 `langgraph` | 在同一容器重复执行依赖安装后，`langgraph` 分发被移除，仅剩空命名空间目录：`import langgraph.graph` → `ModuleNotFoundError`；`pip list` 中 `langgraph` 消失而 `langgraph-checkpoint` 等仍在 | 依赖安装一律走镜像重建，不在运行容器内反复裸装；并按计划 M10 在 `requirements.txt` 钉住 `langgraph` 上界 |
| N3 | 🟡中（存量） | `emily-embed` 容器持续重启 | `docker inspect -f '{{.State.Status}}' emily-embed` → `restarting`；工具注册数由 45 降为 44、契约数由 55 降为 54（少一个依赖嵌入服务的条件工具） | 与本轮改动无关；查 `docker logs emily-embed`，核对 TEI 模型挂载与版本 pin |
| N4 | 🟡中 | 按当前 `requirements.txt` 重建的镜像启动失败 | `emily-core:rebuilt-20260912` 启动时报 `RuntimeError: Form data requires "python-multipart" to be installed`（触发点 `api/routes/console_resources.py` 的表单端点），而 `python-multipart` 其实已在 `requirements.txt` 中声明，成因未定 | 本次在镜像内显式安装该依赖解决；下一次镜像重建须重点验证该依赖是否真正随声明装入 |
| N5 | 🟢低（工艺） | `docker commit` 会连带提交临时容器的入口覆盖 | 用 `--entrypoint sleep` 的临时容器提交后，镜像入口变为 `sleep`，容器"运行中"但进程空转（内存 2MB、日志为空、端口未监听） | 镜像入口与命令改由 `docker-compose-napcat.yml` 显式声明，不再依赖镜像层入口配置 |
| N6 | 🟡中（已修） | 内核级开关被适配器窄化配置遮蔽，灰度高开却不生效 | 会话池持有的是适配器层 `SessionConfig.from_config(...)`（仅含会话运行参数），读 `session_graph_enabled` 恒为缺省 False；表现为"环境变量已置 true、路由日志仍显示旧路径" | `_use_graph()` 改为优先读内核配置（`core.config`），并保留 `SessionConfig` 兜底；修复后实测路由日志出现 `graph path` 与 `session graph built` |
| N7 | 🟡中（待处理） | 发送者无法解析为系统用户时，新路径构建会话失败、该消息无回复 | 以不存在的 UUID 作 `sender_id` 发送时，日志为 `SessionLoopPool build failed: user_id 不能为空`，消息静默无回复；旧链路可通过 IM 绑定/游客规则处理 | 灰度期间需明确该行为差异：建议在无法解析用户时回退旧链路，或按游客策略放行（属 M10 完整版范围） |
| N8 | 🟡中（已修，M5） | 状态结构未登记的运行期字段会被框架**静默丢弃**，导致路由条件恒不成立 | 新增 `_suspend_active` 只写进了运行期初始状态、未登记进状态结构（KernelState），框架按 schema 过滤后该键消失；表现为挂起节点已建但永不触发、请求空转到迭代上限 12 次。对照探针（有/无挂起节点两组）定位 | 字段登记进 `KernelState` 后恢复；结论：凡参与路由或节点判定的运行期字段，必须先登记进状态结构，否则失败方式是"静默不生效"而非报错 |

服务恢复后状态：Postgres `accepting connections`、重启策略已恢复为 `always`；emily-core 检查点走 Postgres 且 `degraded=False`；工具 44 个、契约 54 条、缺失 schema 为 0。

---

## 四、待办与风险

1. **持久修复已落地**：最终采用的镜像是「经验证的旧镜像 + 补齐依赖」的固化版本（标签 `emily-core:fixed-20260912`，同时为 `latest`），并在 `docker-compose-napcat.yml` 中为 emily-core 显式声明入口。依赖不再依赖容器内临时安装。
2. **镜像标签留痕**：`pre-kernel-20260911`（原始）、`rebuilt-20260912`（按当前声明重建、启动失败，待评估 N4）、`fixed-20260912`（当前在用）。
3. 恢复后实测：容器 `running`、重启次数 0；检查点走 Postgres 且 `degraded=False`；契约 54 条且缺失 schema 为 0；`GET /api/v1/health` 返回 401（服务在监听，需令牌）；Postgres `accepting connections`。
4. N1 仍属高危潜在故障：虽然本次已手工补齐目录且服务恢复，但任何一次容器重建都可能再次触发，建议优先给出固定化方案。

---

*本记录为环境阻断处理的过程与证据留痕，供测试报告 V1 的 B1、B3 结项与后续轮次参考。*
