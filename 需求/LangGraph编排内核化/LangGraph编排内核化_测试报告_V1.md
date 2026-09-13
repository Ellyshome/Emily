# LangGraph 编排内核化 — 验证测试报告 V1

> **测试日期**：2026-09-11
> **测试工程师**：AI 资深测试工程师（req-verify）
> **基于 PRD（规格）**：`LangGraph编排内核化_PRD_V1.md` ← 规格符合度的判定基准
> **基于计划**：`LangGraph编排内核化_计划_V1.md`
> **参考**：`LangGraph编排内核化_模块拆解清单_V1.md`、`Emily系统技术栈定版_V1.md`
> **宪法版本**：v1.1
> **测试对象**：第一轮编码落地成果（M1 状态与序列化边界、M2 能力契约）
> **测试环境**：Docker Compose（emily-core + emily-postgres + mitmproxy + napcat + astrbot + emily-embed）｜ LLM: deepseek 系列（本轮未调用）｜ Core: 本地工作树（未打 tag）
> **测试结论**：⚠️ **有条件通过**（本轮交付的 M1/M2 模块功能验证通过：本地 31 项 + 容器 14 项全通过，启动注册实证 55/55 且缺失 schema 为 0；但存在 5 处计划内待接线、1 处环境阻断、5 处存量问题）

---

## 一、测试环境

| 项目 | 说明 |
|------|------|
| Docker Compose | `docker-compose-napcat.yml`（Docker 28.3.2） |
| emily-core | FastAPI :18080，Up（23:27:46 重启后启动完成） |
| emily-postgres | pgvector/pgvector:pg16，`:5432 - accepting connections` |
| mitmproxy | Up，`127.0.0.1:8081` |
| napcat / astrbot | Up |
| emily-embed | **Restarting (1)**，见 Bug B2 |
| LLM | 本轮所有用例均不依赖真实 LLM 调用 |
| Python（容器） | 3.12（应用镜像） |
| Python（宿主） | 3.10.11（用于静态脚本与 AST 校验） |
| 预设数据 | 无预埋；本轮无数据库写操作 |

### 1.1 环境前置检查

| 检查项 | 状态 | 详情 |
|--------|------|------|
| Docker 守护进程 | ✅ | `docker version --format '{{.Server.Version}}'` → `28.3.2` |
| 容器运行 | ✅ | emily-core / emily-postgres / mitmproxy / napcat / astrbot 均 Up；emily-embed 处于重启循环 |
| Core 健康检查 | ⚠️ | `GET /api/v1/health` 返回 `{"detail":"unauthorized"}`（该端点需 `Authorization: Bearer $EMILY_API_TOKEN`）；容器内无 `curl`，改用启动日志作为健康证据（初始化完整、Hook 注册齐全、无 ERROR） |
| 数据库连通 | ✅ | `pg_isready` → `/var/run/postgresql:5432 - accepting connections` |
| LLM 可用性 | N/A | 本轮无用例依赖 LLM（新的会话编排链路 M3 尚未落地） |
| 代码可见性 | ✅ | `emily_core` 以只读方式挂载，宿主改动即时可见；重启后加载新代码 |

### 1.2 数据库基线快照

本轮无写入类用例（M1/M2 为纯逻辑模块，不落库），故未采集行数快照。写入类规格（US-02、US-03.3、US-06.1）的端到端验证属 M3 及之后轮次。

---

## 二、测试计划

### 2.1 测试目标与范围

**测试对象**：第一轮交付的两个模块（M1 状态与序列化边界、M2 能力契约）及其对既有代码的一处改动（能力层解耦）。

**覆盖范围**：模块可导入性与行为、状态序列化守卫、工具上下文端口、契约装配与注册、启动装配接线、能力层反向依赖切断、专家工具回归、运行时可观测、约束合规与接线可达性。

**不覆盖（及原因）**：新的会话编排链路端到端对话（M3 未落地）；挂起跨进程恢复（M5 未落地，且当前环境存在阻断，见 TC09）；门禁与评审（M6）；进度与归档统一（M7）；外壳端口化（M8）；作业手动化（M9）；灰度开关（M10）；语料回放（M11）。

**特别说明**：本轮为 11 模块计划的第 1 轮，规格符合度天然偏低（多数 US 的实现在后续轮次），因此**不应**把"US 未覆盖"等同于"实现失败"，判定须结合计划的分期与接线矩阵。

### 2.2 测试用例设计

| 编号 | 回验的规格 | 靶向模块 | 分类 | 测试用例 | 输入/操作 | 预期行为 | 验证方式 |
|------|-----------|---------|------|---------|-----------|---------|---------|
| TC01 | US-08 | M1, M2 | 接线可达性 | 新增符号的消费方扫描 | 全仓检索新增公开符号 | 有消费方的标记 ✅，无消费方的进入未接线清单 | 静态检索 |
| TC02 | US-03, US-07, US-08 | M1, M2 | 规格符合度 | 本地模块单测（状态守卫、端口、契约映射） | 按文件路径加载模块并逐项断言 | 31 项全部通过 | `verify_m1_m2.py` |
| TC03 | US-03, US-07, US-08 | M1, M2 | 规格符合度 | 容器内模块探测（真实运行时环境） | 容器内导入并逐项断言 | 14 项全部通过 | `docker exec python /tmp/probe.py` |
| TC04 | US-03（AC-US-03.1） | M2 | 规格符合度 | 启动注册实证 | 重启 emily-core 并读启动日志 | 契约注册条数 > 0，缺失 schema 枚举为空 | `docker logs` |
| TC05 | US-08（AC-US-08.4） | M2 | 规格符合度 | 能力层无图状态引用 | 检索 `tools/` 下对图状态的引用 | 0 命中 | 静态检索 |
| TC06 | US-08 | M2 | 回归 | 专家工具导入与调用 | 容器内导入并调用两个取上下文函数 | 不抛错、返回空上下文、源码无图状态引用 | `docker exec` |
| TC07 | US-07（AC-US-07.1） | M1 | 规格符合度 | 领域对象进状态被拦截 | 传入非基础类型与嵌套违规 | 返回违规字段路径；`assert` 抛错 | 单元断言 |
| TC08 | US-08（AC-US-08.3） | M1 | 规格符合度 | 端口绑定、读取、清除与回退 | 绑定后读取、清除后回退 | 读取一致；未绑定时回退为空上下文 | 单元断言 |
| TC09 | US-07（AC-US-07.1）、PRD 约束 8 | M1, M5 前置 | 异常场景 | 检查点跨进程持久化前提 | 读取启动日志中的检查点初始化结果 | 检查点持久化可用 | `docker logs` |
| TC10 | 宪法 Q6 | 全仓 | 接线可达性 | 存量配置项死开关扫描 | 运行 `wiring_scan.py --markdown` | 无断线 | 静态扫描脚本 |
| TC11 | — | 全仓 | 运行时 | 启动日志 ERROR/WARNING 扫描 | 重启后读日志 | 无 ERROR；WARNING 均可归因 | `docker logs` |
| TC12 | US-01, US-02, US-04, US-05, US-06 | M3–M7 | 端到端 | 新链路对话闭环 | 发送业务消息 | 新编排产出回复 | emy-test | 
| TC13 | PRD 约束 5、10 | M10 | 约束合规 | 依赖清单与版本上界核对 | 比对 `requirements.txt` 与容器内可导入模块 | 无新增依赖；上界已锁定 | 清单比对 + 容器导入 |

### 2.3 测试覆盖矩阵

| 覆盖维度 | 覆盖情况 | 对应用例 |
|----------|---------|---------|
| 规格符合度（逐条 US） | ⚠️ 部分（3/11 部分满足，8/11 属后续轮次） | TC02, TC03, TC04, TC05, TC07, TC08 |
| 约束合规（PRD §4.4） | ⚠️ 部分（7 条已遵守/不适用，4 条待落实轮次或被环境阻断） | TC05, TC09, TC13 |
| 正常路径 | ✅（模块级） | TC02, TC03 |
| 边界条件 | ✅（模块级：空入参、非法类别、重名、嵌套违规） | TC02, TC03 |
| 异常/错误处理 | ⚠️ 部分（模块内 fail-closed 已验；链路级属后续轮次） | TC02, TC03, TC06 |
| 权限控制 | ⏭️ SKIP（裁剪与判定属 M3/M6） | — |
| 状态机完整性 | ⏭️ SKIP（M1 仅定状态结构，流转属 M3） | — |
| API 契约 | ⏭️ SKIP（本轮未改动 API） | — |
| 数据持久化 | ❌ 失败（检查点持久化环境阻断） | TC09 |
| Docker 运行时 | ✅（无 ERROR；WARNING 可归因） | TC11 |
| 回归（不破坏现有功能） | ✅（能力层解耦后专家工具可用） | TC06 |
| 接线可达性（静态） | ❌ 存在断线（5 处计划内待接线 + 1 处存量死开关） | TC01, TC10 |

### 2.4 追溯矩阵（US → 模块 → 用例）

| US-ID | 需求一句话 | 实现模块 | 回验用例 | 覆盖状态 |
|-------|-----------|---------|---------|---------|
| US-01 | 会话编排收敛为唯一执行形态 | M3, M10 | — | ❌ 未覆盖：M3/M10 未进入本轮 |
| US-02 | 任务级计划覆盖全部能力且不丢参 | M4, M2 | — | ❌ 未覆盖：M4 未落地（M2 契约已就绪） |
| US-03 | 能力契约显式化与可见范围裁剪 | M2, M6 | TC02, TC04, TC13 | 🟡 部分覆盖 |
| US-04 | 挂起可跨进程恢复且归属发起者 | M5 | — | ❌ 未覆盖：M5 未落地且环境阻断（TC09） |
| US-05 | 进度与结果统一可见且与实际一致 | M7, M3 | — | ❌ 未覆盖：M7 未落地 |
| US-06 | 门禁与评审在编排内判定，否决效力保留 | M6 | — | ❌ 未覆盖：M6 未落地 |
| US-07 | 中间状态可序列化、可恢复、可幂等重放 | M1, M5 | TC02, TC03, TC07, TC09 | 🟡 部分覆盖 |
| US-08 | 领域与外壳不进执行状态，只经端口访问 | M8, M1 | TC01, TC03, TC05, TC06, TC08 | 🟡 部分覆盖 |
| US-09 | 灰度双轨与可回退 | M10 | — | ❌ 未覆盖：M10 未落地 |
| US-10 | 治理与业务资产不减 | M6, M7, M10 | — | ❌ 未覆盖：按轮次推进 |
| US-11 | 无人值守作业与内核解耦 | M9 | — | ❌ 未覆盖：M9 未落地 |

**未覆盖规格清单**：US-01、US-02、US-04、US-05、US-06、US-09、US-10、US-11 共 8 条，原因统一为"对应实现模块（M3–M11）尚未进入编码轮次"，非实现缺陷。

---

## 三、规格符合度（Spec Compliance）

### 3.1 逐条需求判定

| US-ID | 验收标准 | 判定 | 证据 | 关联用例 |
|-------|---------|------|------|---------|
| US-03 | AC-US-03.1 能力须声明参数约束，缺声明不进入可见集 | ✅ 满足（产出侧） | 启动日志 `capability_contract: ready (55 registered / 55 total, missing_schema=[])` | TC04 |
| US-03 | AC-US-03.2 可见集按操作者裁剪、越权不可调用 | ⏭️ 未验证 | 消费方为 M3 目录装配与 M6 门禁，均未落地 | — |
| US-03 | AC-US-03.3 / AC-US-03.4 写入门禁与群聊操作者基准 | ⏭️ 未验证 | 同属 M6/M3 | — |
| US-07 | AC-US-07.1 状态仅含可序列化内容、持久化全路径可用 | 🟡 部分满足 | 守卫实测通过（本地+容器）；但持久化全路径不可用，见 TC09 | TC07, TC09 |
| US-07 | AC-US-07.2 / AC-US-07.3 重放幂等与状态体积受控 | ⏭️ 未验证 | 属 M5/M11 | — |
| US-08 | AC-US-08.1 状态不含领域对象 | ✅ 满足 | 守卫拦截实测 + 静态检索 0 命中领域类型 | TC02, TC03 |
| US-08 | AC-US-08.4 能力层不反向依赖执行状态内部结构 | ✅ 满足 | `tools/` 下 `get_bus_context` 检索 0 命中；专家工具源码实测无该引用 | TC05, TC06 |
| US-08 | AC-US-08.2 / AC-US-08.3 领域按标识重建、外壳经端口 | 🟡 部分满足 | 端口与守卫已就位；图内绑定与外壳端口化属 M3/M8 | TC08 |
| 其余 8 条 US | — | ⏭️ 未覆盖 | 见 2.4 未覆盖清单 | — |

### 3.2 未覆盖规格清单

| US-ID | 为何未覆盖 | 影响 | 建议 |
|-------|-----------|------|------|
| US-01, US-02, US-04, US-05, US-06, US-09, US-10, US-11 | 对应模块（M3–M11）尚未进入编码轮次 | 规格符合度无法在本轮闭合 | 按计划顺序推进，逐轮补测 |

**规格符合度总评**：0/11 条 US 完全满足；3/11 条部分满足（US-03、US-07、US-08）；8/11 条未覆盖（属分期未到，非缺陷）。满足率按"已进入编码轮次的规格"计为 **3/3 部分达成**（US-03 产出侧完全达成）。

---

## 四、测试结果

### 4.1 结果汇总

| 指标 | 数值 |
|------|------|
| 总测试用例数 | 13 |
| 通过 | 8 |
| 失败 | 2（TC09、TC10，均为存量/环境问题，非本轮代码缺陷） |
| 跳过（注明原因） | 3（TC12 依赖 M3；权限与状态机维度 SKIP） |
| 模块内断言合计 | 本地 31 项 + 容器 14 项，全部通过 |
| 通过率 | 8/13 = 61.5%（按可执行用例计 8/10 = 80%） |

### 4.2 逐项测试结果

#### TC01：新增符号的消费方扫描

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-08（AC-US-08.4）、宪法 Q6 |
| **靶向模块** | M1、M2 |
| **输入** | 全仓检索 `bind_tool_context`、`assert_state_serializable`、`summarize_state`、`make_initial_state(`、`current_tool_user_id`、`current_tool_perm_dict`、`register_contracts`、`full_specs_from_core`、`build_specs(` |
| **预期行为** | 已交付且应在启动或既有路径被消费的符号有消费方；尚无消费方的进入未接线清单 |
| **实际行为** | `register_contracts`、`get_contract_registry` 在 `__init__.py:267-268` 被消费；`current_tool_user_id`、`current_tool_perm_dict` 在 `tools/expert_manage_tool.py:165、171` 被消费；`bind_tool_context`、`assert_state_serializable`、`summarize_state`、M1 的 `make_initial_state`、`build_specs` **仅有定义、零外部消费方** |
| **验证方式** | 静态检索（ripgrep） |
| **结果** | ⚠️ PASS_WITH_NOTES（5 处未接线已登记，见第九章） |

#### TC02：本地模块单测

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-03、US-07、US-08 |
| **靶向模块** | M1、M2 |
| **输入** | 31 条断言，覆盖状态构造与校验、违规路径定位、`DomainRef` 拦截、容器类型白名单、断言抛错、端口绑定/清除/回退、契约映射（含 resolver 跳过）、三类业务能力、控制类单列、权限归属派生、注册表幂等与重名、缺 schema 枚举、非法类别拒绝、三种工具注册表访问器形态、空入参安全降级 |
| **预期行为** | 全部通过 |
| **实际行为** | `合计 31 项，通过 31，失败 0` |
| **验证方式** | `python verify_m1_m2.py` |
| **结果** | ✅ PASS |

#### TC03：容器内模块探测

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-03、US-07、US-08 |
| **靶向模块** | M1、M2 |
| **输入** | 14 条断言（真实运行时环境，Python 3.12） |
| **预期行为** | 全部通过 |
| **实际行为** | `容器内探测：共 14 项，通过 14，失败 0` |
| **验证方式** | `docker exec -e PYTHONPATH=/app -w /app emily-core python /tmp/probe.py` |
| **结果** | ✅ PASS |

#### TC04：启动注册实证

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-03（AC-US-03.1） |
| **靶向模块** | M2 |
| **输入** | 重启 emily-core 后读取启动日志 |
| **预期行为** | 契约注册条数 > 0；缺失 schema 枚举为空 |
| **实际行为** | `2026-09-11 23:27:50 [INFO] emily.session.capability_contract: CapabilityContract: 注册 55 条契约（表内共 55 条）` / `emily.core: capability_contract: ready (55 registered / 55 total, missing_schema=[])` |
| **验证方式** | `docker logs --since 2m emily-core` |
| **结果** | ✅ PASS（条数可交叉校验：45 个业务工具 + 8 个 SOP 能力 + 2 个控制工具 = 55，与注册结果完全一致，证明工具枚举走了正确路径） |

#### TC05：能力层无图状态引用

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-08（AC-US-08.4） |
| **靶向模块** | M2 |
| **输入** | 检索 `emily-core/emily_core/tools` 下 `get_bus_context` |
| **预期行为** | 0 命中 |
| **实际行为** | `No matches found` |
| **验证方式** | 静态检索 |
| **结果** | ✅ PASS |

#### TC06：专家工具回归

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-08；回归"不破坏现有功能" |
| **靶向模块** | M2 |
| **输入** | 容器内导入并调用取上下文函数，检查源码引用 |
| **预期行为** | 不抛错；无运行上下文时返回空上下文；源码不再引用图状态 |
| **实际行为** | `user_id='' perm=None`；源码断言 `get_bus_context not in src` 通过 |
| **验证方式** | `docker exec` 内探针 |
| **结果** | ✅ PASS |

#### TC07：领域对象进状态被拦截

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-07（AC-US-07.1）、US-08（AC-US-08.1） |
| **靶向模块** | M1 |
| **输入** | `{"bad": object()}`、`{"a": [{"b": object()}]}`、`{"ref": DomainRef(...)}` |
| **预期行为** | 均被拦截并给出字段路径；`DomainRef` 实例要求转 dict |
| **实际行为** | `['bad']`、`['a[0].b']`、`['ref']`；`assert` 抛 `ValueError` |
| **验证方式** | 本地 + 容器断言 |
| **结果** | ✅ PASS |

#### TC08：工具上下文端口绑定与回退

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-08（AC-US-08.3） |
| **靶向模块** | M1 |
| **输入** | 未绑定读取；绑定 `ToolContext(user_id="u-probe", perm_dict={"level":3})` 后读取；清除后再读 |
| **预期行为** | 未绑定与清除后返回空上下文；绑定后读取一致 |
| **实际行为** | 三项断言全部通过 |
| **验证方式** | 本地 + 容器断言 |
| **结果** | ✅ PASS |

#### TC09：检查点跨进程持久化前提

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-07（AC-US-07.1）、PRD 约束 8（挂起必须支持跨进程恢复） |
| **靶向模块** | M1、M5 前置环境 |
| **输入** | 重启后读启动日志中的检查点初始化结果 |
| **预期行为** | 检查点持久化可用（Postgres） |
| **实际行为** | `2026-09-11 23:27:51 [WARNING] emily.langgraph.checkpointer: Checkpointer: Postgres unavailable (No module named 'langgraph.checkpoint.postgres') — falling back to MemorySaver (断点将不跨进程持久化)` |
| **验证方式** | `docker logs --since 2m emily-core` |
| **结果** | ❌ FAIL（环境阻断，见 Bug B1） |

#### TC10：存量配置项死开关扫描

| 项目 | 内容 |
|------|------|
| **回验的规格** | 宪法 Q6 |
| **靶向模块** | 全仓 |
| **输入** | `python scripts/wiring_scan.py --markdown` |
| **预期行为** | 无断线 |
| **实际行为** | 扫描 72 个 Config 字段，发现 1 处：`langgraph_max_replan` 仅定义无使用（DEAD_SWITCH）；脚本退出码 1 |
| **验证方式** | 宿主静态扫描（容器内不可用，见 B3） |
| **结果** | ❌ FAIL（存量为题，非本轮引入，见 Bug B6） |

#### TC11：启动日志 ERROR/WARNING 扫描

| 项目 | 内容 |
|------|------|
| **回验的规格** | 运行时健康 |
| **输入** | 重启后日志全量扫描 |
| **预期行为** | 无 ERROR；WARNING 可归因 |
| **实际行为** | 无 ERROR、无 Traceback。2 条 WARNING 为存量数据问题（`参与单位登记不全`、`单位标识不规范：25 个节点 related_company_id 非合法单位 ID`）；1 条为检查点回退（B1）；1 条为会话池 TTL 相关 INFO |
| **验证方式** | `docker logs --since 2m emily-core` |
| **结果** | ⚠️ PASS_WITH_NOTES |

#### TC12：新链路端到端对话

| 项目 | 内容 |
|------|------|
| **回验的规格** | US-01、US-02、US-04、US-05、US-06 |
| **靶向模块** | M3–M7 |
| **输入** | 业务消息（emy-test） |
| **预期行为** | 新编排产出回复并归档 |
| **实际行为** | 未执行 |
| **验证方式** | — |
| **结果** | ⏭️ SKIP：新编排链路（M3）尚未落地，当前仅有旧链路；执行端到端只会测到旧路径，对本轮无判定价值 |

#### TC13：依赖清单与版本上界核对

| 项目 | 内容 |
|------|------|
| **回验的规格** | PRD 约束 5（不引入 LangChain 主包）、约束 10（版本上界与回归语料） |
| **靶向模块** | 全仓 / M10 |
| **输入** | 比对 `requirements.txt` 与容器内可导入模块 |
| **预期行为** | 无新增依赖；`langgraph` 上界已锁定；升级回归语料存在 |
| **实际行为** | 本轮未新增任何第三方依赖；`requirements.txt` 仍为 `langgraph>=0.2.0`，**未锁定上界**；容器内缺 `langgraph.checkpoint.postgres`；升级回归语料属 M11 未落地 |
| **验证方式** | `git diff requirements.txt` + 容器内导入检查 |
| **结果** | ⚠️ 部分（无新增依赖 ✅；上界锁定与语料 ❌ 待 M10/M11） |

---

## 五、发现的 Bug 与问题

| # | 严重程度 | 问题描述 | 复现步骤 | 影响范围 | 建议修复 |
|---|---------|---------|---------|---------|---------|
| B1 | 🔴高 | 容器内缺 `langgraph.checkpoint.postgres`，检查点回退 MemorySaver，「断点将不跨进程持久化」 | 重启 emily-core，读启动日志 | PRD 约束 8、US-04、US-07.1、计划 M5/M1 | 在镜像内安装 `requirements.txt` 已声明的 `langgraph-checkpoint-postgres>=2.0` 与 `psycopg[binary]` 并重建镜像；这是 M5 的前置条件 |
| B2 | 🟡中（存量） | `emily-embed` 容器处于 Restarting (1) | `docker compose ps` | RAG 嵌入不可用（本轮未涉及） | 查 `docker logs emily-embed`，核对 TEI 模型挂载与版本 pin |
| B3 | 🟡中（环境漂移） | 运行中的 emily-core 未挂载 `scripts/`，`/app/scripts` 不存在，与仓库 compose 定义不符 | `docker exec emily-core ls /app/scripts` | 技能规定的 `wiring_scan.py` 无法在容器内执行，只能在宿主执行 | 核对实际使用的 compose 文件与服务重建来源 |
| B4 | 🟢低（文档） | `req-verify` 技能 Step 2.5 示例 SQL 使用 `name` / `permission_level` / `department`，与真实 `users` 表不符 | 照抄技能 SQL 执行 | 报 `column "name" does not exist`；且部门维度已于 2026-09-11 移除 | 更新技能文档：`username` / `level`，去掉 `department` |
| B5 | 🟡中（存量，已登记待决策） | 专家工具权限快照读取路径失效：原读 `BusContext.session_ctx`，而该类实际暴露 `get_session_context()` 与 `_session_context`，故权限快照恒为 None，判定长期依赖数据库兜底 | 原代码 `getattr(ctx, "session_ctx", None)` 恒为 None | 专家工具的权限判定口径 | 本轮按"迁移期行为完全不变"迁移，未修；是否修复需业务决策（修会改变权限判定口径） |
| B6 | 🟢低（存量） | 死开关 `langgraph_max_replan`：Config 中定义，全仓零读取方 | `python scripts/wiring_scan.py --markdown` | 宪法 Q6 | 接线或删除 |

---

## 六、数据库状态验证

### 6.1 关键表行数变化

本轮未执行任何数据库写操作（M1/M2 为纯逻辑模块；探针脚本只读），故无行数变化。测试前未采集快照，原因见 1.2。

### 6.2 数据完整性抽查

| 检查项 | SQL/方法 | 结果 | 说明 |
|--------|---------|------|------|
| 数据库连通 | `pg_isready -U emily` | ✅ | `accepting connections` |
| 真实用户可查（供后续轮次使用） | `SELECT id, username, level FROM users WHERE status='active'` | ✅ | 取得 level 1/2/3 等真实用户 UUID，如 `173a9d35-…`（周文斌, L1）、`4cd7aae1-…`（周国栋, L3）、`4d4c…`（陈建华, L3） |
| 本轮引入的写操作 | — | 无 | 无 |

---

## 七、运行时可观测性

### 7.1 容器日志检查

| 检查项 | 结果 | 详情 |
|--------|------|------|
| ERROR 级别日志 | 无 | 重启窗口内 0 条 ERROR、0 条 Traceback |
| WARNING 级别日志 | 4 条 | ① 参与单位登记不全（存量数据）② 单位标识不规范 25 节点（存量数据）③ 检查点回退 MemorySaver（B1）④ 会话池相关提示 |
| 容器重启 | 有 1 次（测试主动执行） | `docker compose restart emily-core`，用于加载新代码 |
| 服务初始化 | 完整 | 依赖注入、Hook 注册（before/after/on_error 全挂载点）、能力注册 8 条、契约注册 55 条、路径路由与监控模块均就绪 |

### 7.2 LLM 调用链分析

本轮无用例触发 LLM 调用，`emily-data/logs/llm_trace.jsonl` 无新增记录（该结论由"无用例执行对话类操作"直接保证，非推断）。LLM 相关维度（prompt 渲染、模型分层、cache 命中）留待 M3 落地后回验。

### 7.3 Session 归档验证

本轮无对话类用例，`emily-data/session_archives/` 无新增归档文件。权限快照与意图识别维度留待 M3 落地后回验。

### 7.4 异常详情

```
2026-09-11 23:27:51 [WARNING] emily.langgraph.checkpointer: Checkpointer: Postgres unavailable
(No module named 'langgraph.checkpoint.postgres') — falling back to MemorySaver (断点将不跨进程持久化)
```

---

## 八、约束与合规

### 8.1 架构铁律合规（C0~C11）

| 铁律 | 是否涉及 | 判定 | 证据 |
|------|---------|------|------|
| C0 根治而非迁就 | 是 | ✅ 合规 | M1/M2 直接建立边界与契约，未采用补丁式缓解 |
| C2 分层不可跳 | 是 | ✅ 合规 | M2 只做装配，权限判定仍走既有通道；未直连数据访问层 |
| C6 Sync repo + to_thread | 否 | — | 本轮未新增数据访问 |
| C8 唯一执行引擎 | 是 | ✅ 合规（未新增引擎） | 本轮未新增任何执行引擎或图 |
| C10 工具必须带参数 schema | 是 | ✅ 合规 | 契约只收录带 schema 的能力，缺 schema 单独列出（实测为空） |
| C11 功能注册接入 | 是 | ✅ 合规 | 契约注册经启动装配通道接入，未裸调用 |
| Q4 无残留 | 是 | ✅ 合规 | 容器内探针已删除；宿主临时脚本位于临时工作目录，未入仓库 |
| Q6 接线闭合 | 是 | ❌ 存在断线（已登记） | 5 处计划内待接线 + 1 处存量死开关，见第九章 |

### 8.2 质量红线合规（Q1~Q6）

| 红线 | 判定 | 证据 |
|------|------|------|
| Q1 验收可执行 | ✅ | 所有验收均为可执行命令，含预期输出 |
| Q2 证据驱动 | ✅ | 每条结论附日志行 / 命令输出 / 断言结果 |
| Q3 真实用户测试 | N/A | 本轮无用例需发送消息；已预留真实用户 UUID 供后续轮次 |
| Q4 无残留 | ✅ | 见 8.1 |
| Q5 无越界 | ✅ | 未新增依赖，未顺带重构无关代码 |
| Q6 接线闭合 | ❌ | 见 9.1 与 9.3，已登记为计划内待接线与存量死开关 |

### 8.3 PRD 约束合规（§4.4 约束型技术决策）

| # | PRD 约束（摘要） | 计划声称如何遵守 | 实测判定 | 证据 |
|---|-----------------|----------------|---------|------|
| 1 | 复用现有图式执行引擎作为唯一编排形态 | 复用既有引擎，旧链路 M10 退役 | ⏭️ 未到落实轮次 | 本轮未新增引擎（grep 无新建图） |
| 2 | 复用现有业务执行引擎作为能力内部实现 | 走既有执行路径 | ⏭️ 未到落实轮次 | M2 未引入执行逻辑 |
| 3 | 状态只含可序列化内容，领域对象按标识重建 | 新状态结构仅基础类型 + ID/摘要 | ✅ 遵守 | TC07、TC03；静态检索无领域类型 |
| 4 | 复用权限判定，不新造，不引策略引擎 | 门禁只做调用编排 | ✅ 遵守 | `capability_contract` 仅装配；未引入策略引擎；依赖清单无新增 |
| 5 | 不引入 LangChain 主包；子包须登记上界 | 依赖清单无新增 | ⚠️ 部分 | 无新增依赖 ✅；`langgraph` 上界未锁定 ❌（待 M10） |
| 6 | 新能力与作业入口经既有注册通道接入 | 契约注册表 + 脚本注册表 | ✅ 部分遵守 | 契约注册已接入启动通道；作业手动入口属 M9 |
| 7 | 保留灰度双轨与可回退 | 沿用既有开关并扩为两级 | ⏭️ 未到落实轮次 | M10 |
| 8 | 挂起必须支持跨进程恢复 | 中断 + 检查点承载 | ❌ 前置不满足 | TC09：容器内检查点不可用，回退内存态（B1） |
| 9 | 无人值守作业不得构成内核必要条件 | 自检改手动、解耦日志依赖 | ⏭️ 未到落实轮次 | M9 |
| 10 | 组件锁版本上界并登记，配套升级语料 | 钉上界 + 语料 | ❌ 未落实 | `requirements.txt` 仍为 `>=0.2.0`；语料属 M11 |
| 11 | 领域与外壳不进执行状态，经端口访问 | 端口协议 + 守卫 | 🟡 部分遵守 | 守卫与端口就位；图内绑定与外壳端口化属 M3/M8 |

**合规总评**：存在 2 项未落实（约束 8 被环境阻断、约束 10 待 M10），4 项未到落实轮次，其余 5 项合规或部分合规。

---

## 九、接线与可达性（Wiring）

### 9.1 静态可达性扫描结果

| 对象 | 定义处 | 使用处计数 | 判定 | 证据 |
|------|-------|-----------|------|------|
| `register_contracts` | `session/capability_contract.py:409` | 1（`__init__.py:268`） | ✅ | 启动装配调用 |
| `get_contract_registry` | `session/capability_contract.py:181` | 1（`__init__.py:267`） | ✅ | 启动装配调用 |
| `current_tool_user_id` | `session/kernel_state.py:193` | 1（`tools/expert_manage_tool.py:165`） | ✅ | 能力层消费 |
| `current_tool_perm_dict` | `session/kernel_state.py:198` | 1（`tools/expert_manage_tool.py:171`） | ✅ | 能力层消费 |
| `full_specs_from_core` | `session/capability_contract.py:365` | 1（同模块 `register_contracts`） | ✅ | 经注册入口消费 |
| `bind_tool_context` | `session/kernel_state.py:175` | **0** | ❌ 仅定义 | 计划消费方 M3（图内绑定） |
| `assert_state_serializable` | `session/kernel_state.py:132` | **0** | ❌ 仅定义 | 计划消费方 M3、M11 |
| `summarize_state` | `session/kernel_state.py:139` | **0** | ❌ 仅定义 | 计划消费方 M7、M11 |
| `make_initial_state`（M1） | `session/kernel_state.py:79` | **0** | ❌ 仅定义 | 计划消费方 M3（注意：`scheduler.py:349` 调用的是既有图状态模块的同名函数） |
| `build_specs` | `session/capability_contract.py:417` | **0** | ❌ 仅定义 | 计划消费方 M3、M4、M6 |
| `langgraph_max_replan` | `config.py` | 0 | ❌ DEAD_SWITCH | **存量**，非本轮引入（TC10） |

**扫描命令**：`python scripts/wiring_scan.py --markdown`（宿主执行；容器内 `/app/scripts` 未挂载，见 B3）
**扫描结论**：存量断线 1 处（`langgraph_max_replan`）；本轮新增符号中 5 处尚无消费方，全部为计划内待接线。

### 9.2 端到端闭环用例结果

| TC | 写入内容 | 消费方 | 是否可见 | 判定 |
|----|---------|--------|---------|------|
| — | 本轮无写入类用例 | — | — | ⏭️ 待 M3 落地后补测 |

### 9.3 跨进程 / 跨重启用例结果

| TC | 场景 | 重启前 | 重启后 | 判定 |
|----|------|--------|--------|------|
| TC09 | 检查点持久化 | — | `Postgres unavailable … falling back to MemorySaver` | ❌ |

**未接线清单**：`bind_tool_context`、`assert_state_serializable`、`summarize_state`、M1 `make_initial_state`、`build_specs` 共 5 项。消费方在计划中已指定（M3/M4/M6/M7/M11），属分期交付的预期状态，**须在对应模块完成后复验归零**。

**接线合规总评**：本轮新增产出物的"启动期"接线已闭合（契约注册、能力层消费）；"运行期"接线待 M3 落地。按 Q6 判据，存在半截交付但**已登记**，故判定为有条件通过而非未通过。

---

## 十、结论与建议

### 10.1 测试结论

本轮交付的 M1（状态与序列化边界）与 M2（能力契约）两个模块功能验证通过。本地 31 项与容器内 14 项断言全部通过；启动注册在真实容器内实证为 55 条契约、缺失 schema 为 0，且条数与"45 工具 + 8 SOP 能力 + 2 控制工具"完全吻合，说明工具枚举路径正确（本轮修复的访问器兼容问题得到实证）；能力层对图内部状态的反向依赖已归零；专家工具的迁移保持了运行行为不变。

规格符合度方面，3 条已进入本轮范围的 US（US-03、US-07、US-08）部分达成，其中 US-03 的产出侧验收标准完全满足，US-08 的两条关键标准（状态无领域对象、能力层不反向依赖）完全满足；其余 8 条 US 因对应模块未进入编码轮次而未覆盖，属分期未到。

发现 6 个问题，其中 1 个高严重度为环境阻断：容器内缺少 `langgraph-checkpoint-postgres`，检查点回退为内存态，导致"挂起跨进程恢复"这一核心规格在**当前环境下无法实现**。该问题不影响本轮模块功能，但**必须在 M5 之前解决**。另有 5 处计划内待接线与 1 处存量死开关已登记。

综合判定：⚠️ **有条件通过**（依据第 7 节判定标准：核心交付通过、存在已登记的半截交付与环境阻断项、无高严重度代码缺陷）。

### 10.2 待改进项

1. 修复 B1（安装 `langgraph-checkpoint-postgres` 并重建镜像），并将其列为 M5 的准入条件。
2. 在 M3 落地时同步完成 5 处待接线，并在 M3 轮的测试中复验接线清单归零。
3. 将 `langgraph` 版本上界与升级回归语料纳入 M10/M11 的验收项，避免版本漂移再次引发故障。
4. 更新 `req-verify` 技能的 Step 2.5 示例 SQL（B4），并补充"容器内无 `curl`、需用 `PYTHONPATH=/app -w /app` 运行探针"等实操要点。
5. 决策 B5（专家工具权限快照路径失效）的修复口径。

### 10.3 遗留风险

1. 检查点回退内存态期间，任何依赖断点恢复的能力（挂起续接、跨进程恢复）都不可用，若在此期间推进 M5 会产生错误结论。
2. 运行中容器的挂载与仓库 compose 定义不一致（B3），可能导致"看起来已部署但实际未生效"的判断偏差，建议在后续轮次测试前先做一次挂载核对。
3. 本轮未执行依赖会话编排链路的端到端用例，M3 落地后需补一轮完整回归。
4. 专家评审（多专家 Agent）体系已按需求基线 D10 转休眠（2026-09-11 决定，见计划 v1.1 修订记录）。本报告基于 PRD V1，属该次运行的历史快照，不追溯修改；报告第九章中与专家工具相关的接线与回归结论在启封后需单独复验。
5. B1、B3 已于 2026-09-12 处置并实测复验通过（检查点跨进程续跑 PASS，挂载数恢复为 20），处置过程与新增隐患见 `LangGraph编排内核化_阻断处理记录_V1.md`。

---

## 十一、附录

### 11.1 测试命令清单

```bash
# 环境检查
docker version --format '{{.Server.Version}}'
docker compose -f docker-compose-napcat.yml ps
docker exec emily-postgres pg_isready -U emily

# 真实用户（列名以实际表结构为准）
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, level, position FROM users WHERE status='active' ORDER BY level LIMIT 8;"

# TC02：本地模块单测
python <temp>/verify_m1_m2.py

# TC03/TC06：容器内探测
docker cp <temp>/probe_container.py emily-core:/tmp/probe.py
docker exec -e PYTHONPATH=/app -w /app emily-core python /tmp/probe.py

# TC04/TC09/TC11：重启并读启动日志
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --since 2m emily-core | grep -E "capability_contract|checkpointer|ERROR|WARNING"

# TC05：能力层反向依赖
grep -rn "get_bus_context" emily-core/emily_core/tools/

# TC10：存量接线扫描（宿主执行）
python scripts/wiring_scan.py --markdown

# 语法校验（AST，不写字节码）
python <temp>/syntax_check.py
```

### 11.2 清理操作

| 清理项 | 操作 | 状态 |
|--------|------|------|
| 容器内探针脚本 | `rm -f /tmp/probe.py /tmp/wiring_scan.py` | ✅ 已删除 |
| 宿主临时脚本（单测、探针、语法校验） | 位于临时工作目录，未写入仓库 | ✅ 未入库 |
| 数据库预埋数据 | 无 | ✅ 不适用 |
| 文件系统桩 | 无 | ✅ 不适用 |
| 配置变更 | 无（仅重启服务加载代码） | ✅ 无变更 |
| 未执行项 | `wiring_scan.py --with-models`（DB 列扫描）未执行：属低置信度项，且本轮未触碰数据模型 | ⏭️ 记录在案 |

---

*本报告由 AI 资深测试工程师通过 req-verify 技能生成，测试于真实 Docker 环境（emily-core 23:27 重启后版本），遵循项目宪法 v1.1。*
