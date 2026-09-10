# Pi Agent 与 Emily 对比分析报告

> **对比对象**：开源项目 Pi Agent Harness（`D:\app\PI_agent\pi`） vs 自研项目 Emily（`D:\app\Emily`）
> **分析日期**：2026-09-10
> **分析问题**：Pi 是否比 Emily 更"系统"、更具功能完整性（指已有功能的**自完备**，而非功能数量）
> **结论**：**判断基本成立，但需分层理解**。两者不是同一品类，不能按"功能多少"简单比较；差异的实质是**工程闭环程度**，而非功能覆盖面。

---

## 一、核心结论

1. **品类不同**：Pi 是**通用 Agent 运行时/框架**（基础设施），Emily 是**垂直领域应用**（地产项目管理）。Pi 不做 Emily 的业务，Emily 也不提供 Pi 的可复用内核。
2. **"功能数量"上 Emily 更多**：IM 多渠道、RAG、权限分级、全景节点状态机、自进化、调度等，业务功能面更宽。
3. **"功能自完备"上 Pi 明显更高一档**：Pi 的每个功能是闭环——**类型契约 → 实现 → 测试 → CI → 发布**；Emily 的许多功能是开环——有规格/脚本/提示词，但**缺契约、缺测试、缺迁移、缺 CI**。
4. **一句话**：差距不在"功能多少"，而在"已有功能是否被闭合与验证"。这正是提问所称的"自完备"。

---

## 二、规模与形态实测

| 维度 | Pi | Emily |
|---|---|---|
| 语言/形态 | TypeScript monorepo，npm workspaces，Node ≥22 | Python asyncio + FastAPI，单包 + 周边脚本 |
| 代码量 | TS 约 **31.7 万行**（源码约 17.5 万 + 测试约 14 万），1381 个文件 | Python 约 **4.9 万行**（emily_core 约 4.7 万 + scripts 约 1 万），274 个文件 |
| 模块化 | **12 个 workspace 包**，依赖单向分层 | 单包内 15+ 子域，无包边界 |
| 测试 | **540 个测试文件**，vitest / node:test，含契约一致性测试 | **0 个 Python 单测**，仅 2 个 YAML 冒烟用例 |
| CI / 静态检查 | GitHub Actions：build + biome + tsgo + 依赖/入口图/锁文件校验 | **无 CI、无 ruff/mypy、无 pyproject.toml** |
| 发布工程 | semver 标签至 v0.85.1，npm 发布 + 独立二进制 + shrinkwrap 固化 | 无发布管道，docker compose 部署 |
| 提交历史 | 6326 commits | 163 commits（含将 postgres 数据文件、napcat 日志提交入仓） |

### Pi 各包代码量（TS 行数，实测）

| 包 | 行数 | 职责 |
|---|---|---|
| `packages/ai` | 64,979 | 多 provider 统一 LLM API |
| `packages/agent` | 50,136 | Agent 运行时 + 持久化 harness |
| `packages/tui` | 36,724 | 终端 UI 库（差分渲染） |
| `packages/coding-agent` | 142,572 | 交互式编码 Agent CLI |
| `packages/chord` | 9,375 | 应用组合运行时（服务/RPC/插件） |
| `packages/session-backends/sqlite-node` | 4,126 | SQLite 会话后端 |
| `packages/server` | 3,051 | RPC 服务端 |
| `packages/client` | 1,951 | RPC 客户端 |
| `packages/protocol` | 1,447 | 类型化线协议（CBOR 帧） |
| `packages/telemetry` | 1,178 | 厂商中立遥测契约 |
| `packages/evals` | 1,848 | 评测（私有） |

---

## 三、Pi"自完备"的证据

1. **分层清晰、无环**
   `telemetry ← ai ← agent ← session-backends`；`protocol ← client/server` 是与业务正交的网络栈；`coding-agent` 在最上层。构建顺序在 `pi/package.json` 的 `build` 脚本里显式编码。跨接缝全部走类型化契约，而非口头约定。

2. **每个接缝都有契约测试**
   会话存储抽象 `Storage`/`SessionRepo` 定义在 agent 包（`packages/agent/src/harness/session/types.ts`），JSONL / 内存 / SQLite 三个后端都必须通过**同一套导出的一致性套件**（`packages/agent/src/harness/session/testing/conformance/`）。telemetry 包同样导出 conformance（`packages/telemetry/src/testing/conformance.ts`）。这意味着"换后端"是被证明过的，不是文档承诺。

3. **插件/扩展是稳定的公开面**
   `ExtensionAPI` 提供约 35 个可订阅事件、`registerTool`/`registerCommand`/`registerProvider`，用 jiti 运行时热加载 TypeScript，带 70+ 示例，约 2900 行文档（`packages/coding-agent/docs/extensions.md`）。这是被导出、被测试、被示例覆盖的公开 API。

4. **跨切面统一**
   - `AbortSignal` 贯穿全链路（客户端 abort → cancel 信封 → 服务端中断请求）。
   - 重试策略带分类、指数退避与上限（`packages/ai/src/utils/retry.ts`、`provider-retry.ts`）。
   - TypeBox 在**工具参数 / 线协议 / 存储**三处统一校验。
   - 错误作为值（`Result` 联合类型，`packages/agent/src/harness/result.ts`），而非随处抛异常。

5. **LLM 层可替换**
   约 40 家 provider、10 种 API 适配器（anthropic-messages / openai-completions / google-generative-ai / bedrock-converse-stream 等），SDK 懒加载，模型目录为**生成物**（`models.generated.ts` 禁止手改，有 `check:model-data` 校验）。

6. **供应链与质量门禁**
   直接依赖精确锁定、`npm-shrinkwrap.json` 固化传递依赖、CI 跑 `build → check → test`，`check` 内含 pinned-deps / runtime-deps / ts-imports / entry-graphs / shrinkwrap 多重校验。

---

## 四、Emily"开环"的证据（部分为其 `CLAUDE.md` 自述）

1. **"三层 Agent"是愿景而非实现**
   - `SessionAgent` 是真实类（`emily-core/emily_core/session/session_agent.py`，1191 行）。
   - `WorkItemAgent` **已删除**，被 LangGraph 节点替代（`workitem/langgraph_engine/`）。
   - `ProjectAgent` **完全不存在**，仅剩提示词占位（`infrastructure/llm/prompt_loader.py` 标注"预埋骨架，代码未实现"），但 `tools/project/` 下仍挂着"仅 ProjectAgent"的工具。
   - 三者**无共享基类、无统一循环**。

2. **迁移机制形同虚设**
   60 张 ORM 表（`infrastructure/database/models.py`，60 处 `__tablename__`），Alembic 仅 **4 个 revision**；真实 schema 靠运行时 `create_all()` + 硬编码 `_ensure_columns()` 补丁。`CLAUDE.md` §9 亦承认"`create_all()` 不 ALTER 已有表"。

3. **工具系统两套并存，schema 不强制**
   `BusinessFlowTool` 与遗留 `ToolDefinition` 并行；注册缺 schema 时**静默**变为 `{"type":"object","properties":{}}`。项目为此专门写了 `scripts/check_tools_consistency.py`，并在 `CLAUDE.md` 约束 #11 复盘"16 个工具空 schema"的系统性事故——问题是**靠事后检查**而非**靠类型系统**防住的。

4. **文档与代码漂移**
   `README.md` 与 `docs/Manual/` 仍在描述已被移除的"三层 Agent + 全局状态机"（`sm_nodes`/`sm_stages` 在代码中 0 命中），只有 `CLAUDE.md` 是当前真相。渠道逻辑在 AstrBot 插件、`wechat-gateway/`、核心 DTO **三处各写一遍**，无 Channel 抽象（`CLAUDE.md` §6.12 自述为"历史遗留"）。

5. **能力大量停留在 scripts / 需求**
   `scripts/` 51 个文件（进化、RAG 治理、三书导出等），`需求/` 100 份 md（其中 71 份"已完成需求文件"）。**规格与脚本体量超过被测试、被集成的代码**，即"文档说完成了、代码只存在于脚本或提示词里"。

6. **工程基建缺失**
   `emily-core/tests/` 仅 2 个 YAML 冒烟用例、0 个 `.py` 测试；无 pytest、无 CI、无 ruff/mypy、无 pyproject；`Taskfile.yml` 纯部署（up/down/logs/backup），无 test/lint 任务；`tenacity` 在 `requirements.txt` 声明却**全项目 0 处 import**。

---

## 五、公平视角：Pi 的短板与 Emily 的资产

### Pi 的短板（并非无懈可击）

- 远程传输**仅实现 UNIX domain socket**，CBOR 协议虽传输中立但未接 HTTP/WebSocket。
- 存在**两套 RPC**：类型化 CBOR 协议与 CLI 的 JSONL stdio 模式，校验严格度差异大，易混淆。
- `coding-agent` 过度集中：`modes/interactive/interactive-mode.ts` 约 6620 行，`core/agent-session.ts` 约 3550 行。
- agent 包并存两套核心：简单 `Agent` 循环与 13 状态持久化 `AgentHarness`。

### Emily 的资产（不可忽视）

- 已是**可部署的生产系统**：6 容器 compose（napcat / astrbot / emily-core / maxkb / emily-postgres / mitmproxy）。
- 有 **mitmproxy LLM 流量代理**，全量记录请求/响应与 `reasoning_content`，工程可观测性接地气，优于 Pi 的纯中立 telemetry。
- 有 **CodeGraph** 代码知识图谱索引（169 文件 / 2110 符号 / 4051 边）。
- 领域建模厚实：60 表、5 级权限、节点依赖与三态流转、自进化机制。
- `CLAUDE.md` 的自我批判（约束 #12"提醒义务"、#11 schema 事故复盘）表明团队**已识别开环问题**，具备补齐前提。

---

## 六、差距根因

| 维度 | Pi | Emily |
|---|---|---|
| 契约获取方式 | 类型系统（编译期强制） | 文档 + 事后校验脚本 |
| 正确性保证 | 自动化测试 + CI 门禁 | 人工冒烟 + Docker 实战测试 |
| 变更安全网 | 一致性套件 / 类型检查 / 锁文件 | 依赖开发者自觉与 CodeGraph 检索 |
| 架构收敛 | 包边界强制依赖方向 | 约定 + 评审，易漂移 |
| Schema 演进 | 迁移文件 + 生成物校验 | 运行时 `create_all` + 硬编码补丁 |
| 结果 | **功能可组合、可替换、可回归** | **功能可运行，但难以安全演进** |

根因可概括为：**Pi 用"机制"约束正确性，Emily 用"纪律"维持正确性**。后者在规模扩大与人员流动下必然退化。

---

## 七、改进优先级建议（不新增功能，只闭合已有功能）

1. **建立自动化质量底座**（最高优先）
   引入 `pytest` + `ruff` + `mypy`，加最小 CI；优先为**工具注册表、参数 schema、节点状态机**三类高风险模块补单测。这是所有后续改进的前提。

2. **正式化数据库迁移**
   用 Alembic 接管 schema 演进，废掉 `create_all()` + `_ensure_columns()` 的补丁模式，将现有 60 表基线固化为初始 revision。

3. **ProjectAgent 二选一**
   要么实现为真实类并接入统一循环，要么从架构文档中降级为"规划中"，同时处理 `tools/project/` 下的孤儿工具，避免"文档有、代码无"。

4. **抽象 Channel 接口**
   收敛 AstrBot 插件 / `wechat-gateway/` / 核心 DTO 三处重复的渠道代码为一个可注册的 Channel 抽象，符合 `CLAUDE.md` §6.12 的元原则。

---

## 八、附：关键证据路径

### Pi

- 项目说明：`pi/README.md`、`pi/AGENTS.md`、`pi/package.json`
- 分层与构建顺序：`pi/package.json`（`build` 脚本）
- Agent 运行时：`pi/packages/agent/src/agent-loop.ts`、`types.ts`、`src/harness/`
- 存储契约：`pi/packages/agent/src/harness/session/types.ts`、`.../testing/conformance/`
- LLM 抽象：`pi/packages/ai/src/models.ts`、`src/providers/all.ts`、`src/utils/retry.ts`
- 插件契约：`pi/packages/coding-agent/src/core/extensions/types.ts`、`docs/extensions.md`
- 线协议：`pi/packages/protocol/src/protocol.ts`、`framing.ts`、`codec.ts`
- 测试与 CI：`pi/vitest.base.ts`、`pi/test.sh`、`pi/.github/workflows/ci.yml`

### Emily

- 当前架构自述：`Emily/CLAUDE.md`（§1 定位、§6 开发约束、§9 踩坑）
- 会话层：`Emily/emily-core/emily_core/session/session_agent.py`
- LangGraph 引擎：`Emily/emily-core/emily_core/workitem/langgraph_engine/`
- ProjectAgent 占位：`Emily/emily-core/emily_core/infrastructure/llm/prompt_loader.py`
- 数据模型与迁移：`Emily/emily-core/emily_core/infrastructure/database/models.py`、`emily-core/alembic/versions/`
- 工具注册：`Emily/emily-core/emily_core/tools/registry.py`、`scripts/check_tools_consistency.py`
- 渠道代码：`Emily/wechat-gateway/`、`Emily/emily-data/plugins/emily_agent/`
- 部署：`Emily/docker-compose-*.yml`、`Emily/Taskfile.yml`

### 复现命令（统计口径）

```bash
# Pi 代码量（单流统计，排除 node_modules/dist）
find packages -type f \( -name '*.ts' -o -name '*.tsx' \) \
  -not -path '*/node_modules/*' -not -path '*/dist/*' -not -path '*/build/*' \
  -print0 | xargs -0 cat | wc -l

# Pi 测试文件数
find packages -type f \( -name '*.test.ts' -o -name '*.test.tsx' \) \
  -not -path '*/node_modules/*' | wc -l

# Emily 代码量（排除 venv 与缓存）
find emily-core -type f -name '*.py' \
  -not -path '*/.venv/*' -not -path '*/__pycache__/*' \
  -print0 | xargs -0 cat | wc -l

# Emily ORM 表数 / Alembic revision 数
grep -c '__tablename__' emily-core/emily_core/infrastructure/database/models.py
ls emily-core/alembic/versions/*.py | wc -l
```
