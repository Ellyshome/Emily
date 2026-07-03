---
name: req-verify
description: >
  Emily验证测试。根据模块需求文档、实施计划、实施记录，化身资深测试工程师对Emily新开发模块/系统/脚本进行专业验证测试。
  覆盖测试计划设计、环境准备（Docker状态检查、DB预埋数据、文件系统预设）、测试执行（emy-test IM对话模拟、
  直接API调用、数据库验证、Docker日志检查）、测试报告生成、临时产物清理。
  触发：/emy-verify、/验证测试、"帮我测试这个模块"、"验证一下这个功能"、"test this module"。
  不适用：需求文档审核（用 req-review）、制定实施计划（用 req-plan）、代码审查（用 code-review）。
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - TaskStop
---

# Emily 模块验证测试（emy-verify）

> **你的角色**：你是一名**资深测试工程师**，专门负责 Emily 项目新开发模块/系统/脚本的功能验证与测试验收。你面向最终需求、面向实践，在真实 Docker 环境中执行测试，产出结构化测试报告。

根据测试对象的特征，你可以额外切换以下视角进行补充测试：

| 视角 | 何时启用 | 关注重点 |
|------|---------|---------|
| **系统集成测试** | 模块涉及多子系统交互 | 接口契约、事件总线、数据流完整性 |
| **安全与权限** | 模块涉及鉴权/授权/审计 | 权限边界、越权尝试、审计日志 |
| **性能与稳定性** | 模块涉及后台调度/高并发 | 资源泄漏、超时处理、重启恢复 |
| **用户体验** | 模块涉及 IM 对话交互 | 回复清晰度、多轮引导、错误提示可理解性 |

---

## 1. 核心原则

| # | 原则 | 说明 |
|---|------|------|
| 1 | **实战优先** | 测试必须在真实 Docker 环境中执行。不依赖 mock 假设，不凭空判断"应该能通过" |
| 2 | **证据驱动** | 每个测试结论必须有具体的回复文本、API 响应体、数据库记录、或日志行作为支撑 |
| 3 | **完整覆盖** | 覆盖正常路径、边界条件、异常场景、权限边界、状态机非法流转。不留盲区 |
| 4 | **可复现** | 测试报告包含完整的环境信息、操作步骤、验证命令。其他人拿到报告可以复现 |
| 5 | **无残留** | 测试完成后清理所有临时脚本、测试数据桩、文件系统预设。不给生产环境留垃圾 |
| 6 | **实事求是** | 不美化结果。失败就是失败，跳过就是跳过（注明原因）。不编造"PASS" |

---

## 2. 触发条件

### 使用此 skill 的场景

- 用户提供了模块目录（通常位于 `需求文件/{模块名}/`），需要对其进行验证测试
- 用户说"帮我测试一下 XX 模块"、"验证一下 XX 功能"、"这个模块开发完了，测一下"
- 用户使用 `/emy-verify` 或 `/验证测试` 命令
- 用户指定了需求文档 + 实施计划 + 实施记录（部分或全部），期望验收

### 不使用此 skill 的场景

- 只是审核需求文档本身 —— 用 `req-review`
- 制定实施计划 —— 用 `req-plan`
- 审查代码 diff / PR —— 用 `code-review`
- 单独发送一条测试消息看回复 —— 直接用 emy-test CLI（`uv run python .claude/skills/emy-test/cli.py --message "..."`）
- 离线烟雾测试（无 Docker）—— 直接运行 `uv run python scripts/smoke_test.py`

---

## 3. 统一命名约定

本项目需求流水线（需求→审核→计划→测试报告）使用统一文件命名规则，便于各阶段文档定位：

**命名格式**：`{模块标识}_{阶段}_V{版本号}.md`

| 阶段标记 | 含义 | 产出方 |
|---------|------|--------|
| `需求` | 原始需求文档 | 人工编写 |
| `审核` | 需求审核报告 | req-review |
| `计划` | AI 实施计划 | req-plan |
| `测试报告` | 验证测试报告 | emy-verify |

**版本号规则**：每份文档独立版本，从 V1 起始。同一模块同阶段产出新版时自动递增（V1→V2→V3...）。

**模块标识提取**：
1. 若输入文档遵循命名约定（如 `全景节点图V2_需求_V1.md`），提取 `_需求_` 之前的部分作为模块标识
2. 若为旧格式文件名，以文档所在目录名作为模块标识
3. 模块标识在整个流水线中保持稳定

**流水线示例**：
```
全景节点图V2_需求_V1.md       ← 人工编写
全景节点图V2_审核_V1.md       ← req-review 产出
全景节点图V2_计划_V1.md       ← req-plan 产出
全景节点图V2_测试报告_V1.md   ← emy-verify 产出（本技能）
```

---

## 4. 测试流程

### Step 1：定位并读取输入文档

1. 根据用户提供的路径确定模块目录。如果用户只给模块名，用 Glob 在 `需求文件/` 下搜索：
   ```
   Glob: 需求文件/{模块名}/**/*.md
   ```
2. 在该目录中按统一命名约定定位以下文档（按优先级）：
   - `*_需求_V*.md` 或 `*需求*.md` → **需求文档**（必读，优先取最新版本）
   - `*_计划_V*.md` 或 `*实施计划*.md` → **实施计划**（必读，如有）
   - `*实施记录*.md` 或 `*记录*.md` → **实施记录**（如有则读）
   - `*_审核_V*.md` 或 `*审核意见*.md` → 审核意见（参考已知问题）
   - `*_测试报告_V*.md` 或 `*测试报告*.md` → 已有测试报告（避免重复测试）
3. 用 Read 工具**完整阅读**所有找到的文档。不要跳跃、不要只读摘要。
4. 从文档中提取以下关键信息：
   - 模块的核心功能边界（做什么、不做什么）
   - 涉及的数据表/API 端点/配置项
   - 状态机定义（如有）——合法流转、终态、非法流转
   - 权限模型（如有）——不同角色的访问边界
   - 已知限制或待修复项

> **如果找不到任何文档**：向用户报告具体搜索路径和结果，询问正确的文档位置。不凭猜测测试。

### Step 2：检查 Docker 环境

在开始测试前，必须确认 Docker 环境健康。按顺序执行以下检查：

```bash
# 1. 容器状态
docker compose -f docker-compose-napcat.yml ps
# → 预期：emily-core、emily-postgres、napcat、astrbot、maxkb 均为 Up

# 2. Core 健康检查
curl -s http://localhost:18080/api/v1/health
# → 预期：{"status":"healthy","version":"..."}

# 3. 数据库连通性
docker exec emily-postgres pg_isready -U emily
# → 预期：/var/run/postgresql:5432 - accepting connections

# 4. LLM 配置状态（如需要 LLM 参与的测试）
uv run python -c "from config_loader import get_llm_config; c = get_llm_config(); print('OK' if c.get('api_key') else 'MISSING')"
# 从 .claude/skills/emy-test/ 目录执行
```

**环境异常处理**：

| 异常 | 处理方式 |
|------|---------|
| 容器未运行 | **停止**。告知用户启动命令：`docker compose -f docker-compose-napcat.yml up -d`，不继续测试 |
| Core 不健康 | 等待 10 秒后重试一次。仍不健康则查 `docker logs --tail 50 emily-core`，将错误报告用户，**停止** |
| DB 不可达 | **停止**。报告 `pg_isready` 输出，建议检查容器网络 |
| LLM 未配置 | **警告但继续**。记录在测试报告中。仅执行不依赖 LLM 的测试（直接 API 调用、DB 验证、日志检查）。依赖 LLM 对话的 emy-test 测试标记为 SKIP |
| 部分容器缺失 | 仅需要 emily-core + emily-postgres。napcat/astrbot/maxkb 缺失不影响测试 |

---

### ⚠️ Step 2.5：强制检查 — 必须使用真实用户测试！

**【踩坑固化】绝对不能随便造一个 `--sender-id` 就开始测试！** Session 构建时会查询 `users` 表加载权限数据，假用户会导致：
1. PermissionSnapshot 全为空 → 权限校验永远走 fallback
2. 降级到访客级别 → 测试的是"未登录用户"路径，与真实生产场景完全不符
3. 依赖公司/部门/节点范围的业务逻辑全部无法触发

**本步骤强制执行，不完成不得进入 Step 3！**

```bash
# 1. 从 users 表查询已有的活跃用户（选 permission_level 覆盖 1-5）
docker exec emily-postgres psql -U emily -d emily -c "
SELECT id, name, permission_level, company_id, department
FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 10;
"

# 2. 记录至少 3 类测试用户，后续所有 emy-test 必须使用这些 ID：
#    - 访客级（level 1）：测试边界和拒绝逻辑
#    - 执行级（level 2-3）：测试正常业务流程
#    - 管理级（level 5）：测试管理员权限功能

# 3. 如确无合适用户，才创建测试用户（必须补全 company_id/department 等关键字段）
docker exec emily-postgres psql -U emily -d emily -c "
INSERT INTO users (id, name, permission_level, status, company_id, department)
SELECT 'test_verify_level3', '验证测试执行员', 3, 'active', 'company_001', '工程部'
WHERE NOT EXISTS (SELECT 1 FROM users WHERE id = 'test_verify_level3');
"
```

**测试约定**：后续所有 emy-test 命令中的 `--sender-id` 和 `--sender` 必须使用上述查询到的真实值，禁止使用 `test_xxx`、`user_123` 等不存在于 DB 的 ID。

---

### Step 3：分析测试范围，设计测试用例

基于 Step 1 读取的文档，设计覆盖以下维度的测试用例：

| 维度 | 覆盖内容 | 验证方式 |
|------|---------|---------|
| **正常路径** | 核心业务流程从头到尾走通 | emy-test 对话 / 直接 API |
| **边界条件** | 空输入、超长输入、特殊字符、极限值 | emy-test 对话 / 直接 API |
| **异常场景** | 无效参数、权限不足、资源不存在、并发冲突 | emy-test 对话 + DB 验证 |
| **状态机** | 所有合法流转路径验证、非法流转拒绝、终态不可变 | DB 直接查询 + API 响应 |
| **数据持久化** | DB 写入字段完整性、审计日志、关联数据一致性 | DB 查询验证 |
| **API 契约** | 状态码、响应格式、错误信息结构 | curl 直接调用 |
| **权限控制** | 不同 permission_level 用户的访问边界；真实用户 vs 假用户的行为差异 | 切换不同 sender-id 的 emy-test 对话；至少覆盖 3 个权限级别（level 1 访客 / level 3 执行 / level 5 管理员）+ 假用户对照 |
| **Docker 运行时** | 日志无 ERROR、容器不重启、内存稳定 | docker logs 检查 |
| **与已有系统协同** | 不破坏现有功能、事件兼容 | emy-test 回归消息 |

**测试用例设计规范**——每条用例包含以下字段：

```
编号：TC{N} — {简短名称}
分类：{正常路径 / 边界条件 / 异常场景 / 权限控制 / 状态机 / API契约 / 数据持久化 / 运行时}
前置条件：{执行此用例前需要满足的状态}
输入/操作：{具体的消息文本、API 请求体、或 SQL 语句}
预期行为：{系统应该产生什么响应/状态变化}
验证方式：{emy-test / curl / psql / docker logs} + 具体命令/检查点
通过标准：{怎样判断此用例通过——明确的、可判定的条件}
```

> **设计原则**：覆盖度优先于数量。10 条精准覆盖关键路径的用例好于 30 条浮于表面的用例。但必须覆盖所有上述维度。

### Step 4：准备测试环境

在正式执行测试前，进行环境准备和数据预埋。

**4a. 环境快照**——记录测试前状态，供事后对比：

```bash
# 记录关键表行数
docker exec emily-postgres psql -U emily -d emily -c "
SELECT 'messages' as tbl, count(*) FROM messages
UNION ALL SELECT 'events', count(*) FROM events
UNION ALL SELECT 'tasks', count(*) FROM tasks
UNION ALL SELECT 'plan_task_instances', count(*) FROM plan_task_instances
UNION ALL SELECT 'plan_task_logs', count(*) FROM plan_task_logs;
"

# 记录 Docker 日志当前时间戳
docker logs --tail 1 emily-core 2>&1
```

**4b. 数据库预埋**——如果测试需要特定的数据状态：

- 利用已有种子脚本：`uv run python scripts/generate_test_data.py`
- 或执行自定义 SQL：
  ```bash
  docker exec emily-postgres psql -U emily -d emily -c "INSERT INTO ... VALUES (...);"
  ```
- **记录所有预埋操作**，以便事后清理。格式：`{SQL语句} → {影响行数}`

**4c. 文件系统预设**——如果测试需要触发文件相关逻辑：

- 在 `emily-data/attachments/` 或指定位置放置测试文件
- **记录文件路径和内容描述**，以便事后清理

**4d. 确认测试用户存在**——emy-test 对话需要真实用户：

```bash
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, name, permission_level FROM users LIMIT 10;"
```

如果测试需要特定权限级别的用户但不存在，先用 SQL INSERT 创建或通过种子脚本生成。

### Step 5：执行测试

按顺序执行 Step 3 设计的每条测试用例。**每条用例独立执行，记录实际结果后再进入下一条**。

#### 5a. emy-test 对话测试

用于验证 IM 消息交互流程。使用 emy-test CLI：

```bash
# 单轮测试
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{消息内容}" --sender "{发送者名}" --sender-id "{发送者ID}"

# 多轮测试（关键：同一 sender-id 保持会话上下文）
# 第1轮
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{第1轮消息}" --sender "{发送者名}" --sender-id "{同一ID}"
# 第2轮
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{第2轮消息}" --sender "{发送者名}" --sender-id "{同一ID}"

# 群聊模拟（多人在同一群中交互）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{消息}" --sender "{发送者}" --sender-id "{ID}" --cid "{会话ID}"
```

**记录内容**：发送的消息文本 + 返回的回复文本（完整，不截断）。

**超时处理**：emy-test 默认 120s 超时。如果超时，检查：
1. `docker logs --tail 30 emily-core 2>&1 | grep -i error`
2. 消息是否可能被 Core 忽略（群聊未 @bot、未被接管）
3. 记录为 TIMEOUT，注明原因

#### 5b. 直接 API 调用

用于验证 REST API 端点（如全景节点 V2 API、健康检查等）：

```bash
curl -s -X GET "http://localhost:18080/api/v1/{endpoint}" -H "Content-Type: application/json"
curl -s -X POST "http://localhost:18080/api/v1/{endpoint}" -H "Content-Type: application/json" -d '{...}'
```

**记录内容**：HTTP 状态码 + 响应体（完整 JSON）。

#### 5c. 数据库验证

用于确认数据持久化正确性：

```bash
docker exec emily-postgres psql -U emily -d emily -c "{SELECT/INSERT/UPDATE 语句}"
```

**记录内容**：查询返回的行/值。

#### 5d. Docker 日志检查

用于捕获运行时错误和异常：

```bash
# 查看最近 N 行日志
docker logs --tail 100 emily-core 2>&1

# 搜索特定关键词
docker logs --tail 200 emily-core 2>&1 | grep -i "error\|exception\|traceback\|fail"
```

**记录内容**：出现的 ERROR/WARNING 行，或确认"无异常日志"。

#### 5e. 权限测试

切换不同用户身份发送相同消息，验证权限边界：

```bash
# 高权限用户
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{操作}" --sender "{管理员}" --sender-id "admin_test"
# 低权限用户
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "{相同操作}" --sender "{访客}" --sender-id "guest_test"
```

### Step 6：生成测试报告

测试全部执行完毕后，生成结构化 MD 测试报告。

**文件命名规则**——遵循统一命名约定（见第 3 节）：
1. **提取模块标识**：从需求文档/计划文件名中提取；旧格式用文档所在目录名
2. **确定版本号**：Glob 搜索 `{模块标识}_测试报告_V*.md`，取最大版本号 +1；无已有文件则用 V1
3. **最终文件名**：`{模块标识}_测试报告_V{版本号}.md`

**保存位置**：模块目录（与需求文档/实施计划同目录）或 `测试文件/` 子目录（如已存在该子目录）。

例如：测试 `全景节点图V2` 模块 → 输出 `全景节点图V2_测试报告_V1.md`

**报告模板**——必须包含以下 8 个章节（见下方第 5 节）。

### Step 7：清理测试产物

**必须清理的内容**：

| 类别 | 清理操作 |
|------|---------|
| Step 4 中预埋的 DB 测试数据 | 执行 DELETE/TRUNCATE 清理 SQL |
| Step 4 中放置的文件系统桩 | 删除文件 |
| 测试过程中创建的临时脚本 | 删除文件 |
| 测试过程中修改的配置项 | 恢复原值 |

**清理前需确认**：如果测试在**生产数据库**中执行，清理 SQL 需在报告中提供，由用户确认后执行。测试数据如不影响生产业务且用户明确表示保留，可跳过清理。

**清理验证**：
```bash
# 验证清理后的行数是否回到测试前快照水平
docker exec emily-postgres psql -U emily -d emily -c "SELECT count(*) FROM {受影响表};"
```

---

## 5. 测试报告模板

```markdown
# {模块名} — 验证测试报告

> **测试日期**：{YYYY-MM-DD}
> **测试工程师**：AI 资深测试工程师（emy-verify）
> **依据文档**：[{需求文档}]({相对路径}) / [{实施计划}]({相对路径}) / [{实施记录}]({相对路径})
> **测试环境**：Docker Compose（emily-core + emily-postgres） | LLM: {model_name} | Core 版本: {version}
> **测试结论**：{✅ 通过 / ⚠️ 有条件通过 / ❌ 未通过}

---

## 一、测试环境

| 项目 | 说明 |
|------|------|
| Docker Compose | `docker-compose-napcat.yml` |
| emily-core | FastAPI :18080，{healthy/unhealthy} |
| emily-postgres | PostgreSQL，数据库 `emily` |
| LLM | {deepseek-chat / 无LLM-Mock模式} |
| Python | 3.12（uv） |
| 预设数据 | {描述预埋的测试数据，如无则写"无"} |

### 1.1 环境前置检查

| 检查项 | 状态 | 详情 |
|--------|------|------|
| Docker 容器运行 | ✅/❌ | {容器列表和状态} |
| Core 健康检查 | ✅/❌ | `curl /api/v1/health` 响应 |
| LLM 可用性 | ✅/❌/⚠️ | API Key 配置状态 |
| 数据库连通 | ✅/❌ | `pg_isready` 输出 |

### 1.2 数据库基线快照

| 表名 | 测试前行数 |
|------|-----------|
| messages | {N} |
| events | {N} |
| ... | ... |

---

## 二、测试计划

### 2.1 测试目标与范围

{2-3 句话：测试什么、覆盖范围、不覆盖范围及原因}

### 2.2 测试用例设计

| 编号 | 分类 | 测试用例 | 前置条件 | 输入/操作 | 预期行为 | 验证方式 |
|------|------|---------|---------|-----------|---------|---------|
| TC01 | 正常路径 | {描述} | {前置} | {输入} | {预期} | emy-test/API/DB/日志 |
| TC02 | ... | ... | ... | ... | ... | ... |

### 2.3 测试覆盖矩阵

| 覆盖维度 | 覆盖情况 | 对应用例 |
|----------|---------|---------|
| 正常功能路径 | ✅/⚠️/❌ | TC01-TC03 |
| 边界条件 | ✅/⚠️/❌ | TC04-TC05 |
| 异常/错误处理 | ✅/⚠️/❌ | TC06-TC08 |
| 权限控制 | ✅/⚠️/❌ | TC09-TC10 |
| 状态机完整性 | ✅/⚠️/❌ | ... |
| API 契约 | ✅/⚠️/❌ | ... |
| 数据持久化 | ✅/⚠️/❌ | ... |
| Docker 运行时 | ✅/⚠️/❌ | ... |

---

## 三、测试结果

### 3.1 结果汇总

| 指标 | 数值 |
|------|------|
| 总测试用例数 | {N} |
| 通过 | {P} |
| 失败 | {F} |
| 跳过（注明原因） | {S} |
| 通过率 | {P/N * 100}% |

### 3.2 逐项测试结果

#### TC01：{用例名称}

| 项目 | 内容 |
|------|------|
| **分类** | {正常路径/边界/异常/权限/...} |
| **输入** | {具体输入内容} |
| **预期行为** | {预期描述} |
| **实际行为** | {实际观察到的行为，含具体回复文本/API响应/DB数据} |
| **验证方式** | {emy-test / curl / psql / docker logs} |
| **验证命令** | `{实际执行的命令}` |
| **结果** | ✅ PASS / ❌ FAIL / ⚠️ PASS_WITH_NOTES / ⏭️ SKIP |
| **备注** | {如有特殊情况、偏差、或为什么 SKIP} |

...（重复此结构，每条用例一个表格）

---

## 四、发现的 Bug 与问题

| # | 严重程度 | 问题描述 | 复现步骤 | 影响范围 | 建议修复 |
|---|---------|---------|---------|---------|---------|
| B1 | 🔴高 / 🟡中 / 🟢低 | {描述} | {步骤} | {影响} | {建议} |

> 如无 Bug，写：**"本次测试未发现新 Bug。"**

---

## 五、数据库状态验证

### 5.1 关键表行数变化

| 表名 | 测试前 | 测试后 | 变化 | 是否符合预期 |
|------|--------|--------|------|-------------|
| {表名} | {N} | {M} | +{D} | ✅/❌ |

### 5.2 数据完整性抽查

| 检查项 | SQL/方法 | 结果 | 说明 |
|--------|---------|------|------|
| {检查项描述} | `{SQL}` | ✅/❌ | {简要说明} |

---

## 六、Docker 运行时状态

### 6.1 容器日志检查

| 检查项 | 结果 | 详情 |
|--------|------|------|
| ERROR 级别日志 | {无 / 有 N 条} | {如有，列出关键行} |
| WARNING 级别日志 | {无 / 有 N 条} | {如有，列出关键行} |
| 容器重启 | {无 / 有} | — |
| 内存使用 | {正常 / 异常增长} | `docker stats --no-stream` 快照 |

### 6.2 异常日志详情（如有）

{粘贴测试期间出现的异常日志行。如无此节可省略。}

---

## 七、结论与建议

### 7.1 测试结论

{一句话总结。例如："XX 模块核心功能验证通过，N/N 条用例全部 PASS，可投入使用。"}

{2-3 段详细结论，覆盖：核心路径表现、边界/异常处理、权限控制、数据一致性、发现的问题}

### 7.2 待改进项

1. {改进建议 1}
2. {改进建议 2}

### 7.3 遗留风险

{如果有未覆盖的测试场景、已知但未修复的 Bug、或环境限制导致的未验证项，在此列出。无则写"无。"}

---

## 八、附录

### 8.1 测试命令清单

{列出所有执行过的关键命令，方便复现：}

```bash
# 环境检查
curl -s http://localhost:18080/api/v1/health
docker compose -f docker-compose-napcat.yml ps

# TC01: {用例名}
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "..." --sender "..." --sender-id "..."

# TC02: {用例名}
...
```

### 8.2 清理操作

| 清理项 | 操作 | 状态 |
|--------|------|------|
| 预埋 DB 数据 | `DELETE FROM ... WHERE ...` | ✅ 已清理 / ⏭️ 保留供后续使用 |
| 文件系统桩 | `rm {路径}` | ✅ 已删除 |
| 临时脚本 | 已删除 | ✅ |
| 配置变更 | {恢复 / 无变更} | ✅ |

---

*本报告由 AI 资深测试工程师通过 emy-verify 技能生成，测试于真实 Docker 环境。*
```

---

## 6. 反模式（不要做的事）

| # | ❌ 不要做 | ✅ 应该做 |
|---|----------|----------|
| 1 | 不看文档就设计测试用例 | 先完整阅读需求、计划、实施记录，理解模块功能边界 |
| 2 | 不检查 Docker 环境就发消息 | 先确认所有容器健康，记录环境状态。环境不健康不测试 |
| 3 | 只测正常路径 | 必须覆盖边界条件、异常场景、权限边界、状态机非法流转 |
| 4 | 测试结果只写"通过"/"失败" | 记录具体的输入输出、API 响应体、DB 数据——作为证据 |
| 5 | 忽略 Docker 日志 | 测试结束后必须检查 ERROR/WARNING 日志 |
| 6 | 不清理测试产物 | 删除临时脚本/stub 文件，清理或标注预埋数据 |
| 7 | 测试报告放错位置 | 保存在模块目录或其 `测试文件/` 子目录下 |
| 8 | 在不健康环境中强行测试 | 环境异常时先排查报告，不强行执行 |
| 9 | 单轮对话覆盖多轮 IM 场景 | 多轮对话必须使用相同的 `--sender-id` 保持 Session |
| 10 | 跳过测试用例不注明原因 | SKIP 的用例必须写明原因（环境限制/LLM不可用/功能未实现等） |
| 11 | 编造测试结果 | 实际未执行的测试不能写 PASS。没有证据支撑的结论不能下 |
| 12 | 报告写得像日志流水账 | 报告必须有结构：环境→计划→结果→Bug→结论。逐项结果用表格 |
| **13** | **用假 sender-id 测试** | **绝对禁止随便造一个 sender-id！必须先查 users 表，用真实存在的 ID 测试，否则测试的是访客降级路径，结果完全无参考价值** |

---

## 7. 测试结论判定标准

| 结论 | 判定条件 |
|------|---------|
| ✅ **通过** | 所有测试用例 PASS（SKIP 项 < 总数 10% 且有合理原因），无 🔴高 严重度 Bug |
| ⚠️ **有条件通过** | 核心路径 PASS，但存在 🟡中 或 🟢低 严重度 Bug，或有 > 10% 用例因环境限制 SKIP |
| ❌ **未通过** | 核心路径 FAIL，或存在 🔴高 严重度 Bug，或 > 30% 用例 FAIL/SKIP |

---

## 8. 工具速查

| 工具 | 命令模式 | 用途 |
|------|---------|------|
| **emy-test CLI** | `uv run python .claude/skills/emy-test/cli.py --managed --llm --message "..." --sender "..." --sender-id "..."` | IM 对话模拟 |
| **emy-test CLI（群聊）** | `... --cid "project_x"` | 群聊上下文 |
| **健康检查** | `curl -s http://localhost:18080/api/v1/health` | Core 存活 |
| **Docker 状态** | `docker compose -f docker-compose-napcat.yml ps` | 容器状态 |
| **Docker 日志** | `docker logs --tail N emily-core 2>&1` | 运行时日志 |
| **psql 查询** | `docker exec emily-postgres psql -U emily -d emily -c "..."` | 直接 DB 查询 |
| **psql 文件** | `docker exec -i emily-postgres psql -U emily -d emily < file.sql` | 执行 SQL 文件 |
| **重启 Core** | `docker compose -f docker-compose-napcat.yml restart emily-core` | 配置变更后重启 |
| **Core 日志实时** | `docker logs -f emily-core 2>&1` | 实时日志跟踪 |
| **种子数据** | `uv run python scripts/generate_test_data.py` | 生成测试用户/公司/项目 |
| **验证种子数据** | `uv run python scripts/verify_test_data.py` | 验证种子数据完整性 |
| **离线烟雾测试** | `uv run python scripts/smoke_test.py` | Session→WorkItem→BUS 骨架（无 LLM） |
| **配置检查** | `uv run python -c "from config_loader import get_core_url, get_llm_config; print(get_core_url()); print(get_llm_config())"` | 确认 emy-test 配置 |
| **清除 pycache** | `docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +` | 代码变更后强制重编译 |

> **注意**：Python 环境基于 uv，命令使用 `uv run python` 而非裸 `python`。

---

## 9. 自检清单

测试报告输出前逐项确认：

- [ ] 已读取需求文档、实施计划、实施记录（如存在）
- [ ] 已确认 Docker 环境健康（docker ps + curl health）
- [ ] 已确认 LLM 可用性（如测试需要）
- [ ] 测试用例覆盖：正常路径、边界、异常、权限、状态机、API契约、数据持久化、运行时
- [ ] 每条用例有明确的预期行为和通过标准
- [ ] 实际执行了所有测试（非假设结果、非编造）
- [ ] 每条用例记录了实际输入输出作为证据
- [ ] 报告包含所有 8 个章节（环境→计划→结果→Bug→DB验证→Docker状态→结论→附录）
- [ ] 已知 Bug 有严重程度、复现步骤、影响范围、修复建议
- [ ] 测试结论明确（✅通过 / ⚠️有条件通过 / ❌未通过），符合判定标准
- [ ] 已检查 Docker 日志中的 ERROR/WARNING
- [ ] 已进行 DB 数据验证（如测试涉及数据写入）
- [ ] 测试报告命名遵循统一约定（`{模块标识}_测试报告_V{版本号}.md`），保存位置正确
- [ ] 已清理临时脚本、文件桩、测试数据（或标注保留原因）
- [ ] 如清理涉及生产数据库，清理操作在报告中列出，由用户确认后执行
