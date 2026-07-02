# emily-data 功能实验 — 执行任务书

> **版本**: V1 | **日期**: 2026-07-01 | **状态**: 待执行

---

## 一、目的

本任务书设计一系列 IM 对话，通过 emy-test 工具发送，**触发 Emily 系统写入数据到 `emily-data/` 下的 4 个读写目录**（journal / notebooks / user_memory / attachments），形成可被人类查阅的文档，验证四目录在生产环境中的实际功能运转。

---

## 二、前置条件

### 2.1 环境要求

| 项目 | 要求 | 确认命令 |
|------|------|---------|
| Docker 全部容器运行 | emily-core + emily-postgres + maxkb + napcat + astrbot | `docker compose ps` |
| LLM API Key 已配置 | DeepSeek 或其他兼容 API | curl health endpoint |
| 测试用户存在 | emily-postgres users 表 | `SELECT id, name FROM users LIMIT 5;` |
| emily-core 已清除 pycache | 确保本次代码变更生效 | `docker exec emily-core find /app -name '__pycache__' -type d -exec rm -rf {} +` |

### 2.2 测试前基线快照

执行前记录 4 个目录的当前状态：

```bash
# 1. journal 目录
ls -la emily-data/journal/

# 2. notebooks 目录
ls -la emily-data/notebooks/

# 3. user_memory 目录
ls -la emily-data/user_memory/

# 4. attachments 目录
ls -la emily-data/attachments/

# 5. DB 用户检查
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, name, permission_level FROM users LIMIT 5;"
```

### 2.3 测试后验证

每个目录的实验完成后，**立即检查目录中是否产生了新文件**：

```bash
ls -la emily-data/{目录名}/            # 查看文件列表及时间戳
cat emily-data/{目录名}/{新产生的文件}   # 查看文件内容
```

---

## 三、实验设计

### 总体设计说明

每个实验分两阶段：

**阶段 A — 触发写入**：使用 emy-test 发送 IM 对话消息，触发 Emily 写入对应目录
**阶段 B — 观察证实**：检查目录中产生的新文件，验证内容符合预期

所有实验使用同一测试用户、同一会话，模拟真实员工日常工作交互。

### 实验参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 测试用户名 | 彭工 | `--sender "彭工"` |
| 测试用户ID | `peng_gong` | `--sender-id "peng_gong"` |
| 测试项目 | 生态城26#地 | 作为事件/任务/会议的 project_name |
| 会话ID | 不指定 | 使用默认会话（同一用户自动复用） |

---

## 四、实施方案

### 实验一：journal/ — 项目日记写入

**触发路径**：IM 对话 → SessionAgent → 意图路由 → SOP-001/002/003 → WorkItemAgent node3 → record_event/task/meeting handler → Application → `self._journal.append()` → `emily-data/journal/项目日志.md`

**设计思路**：journal 通过 **4 种 Application** 写入（event confirm / task create / meeting create / file create）。选用 **任务创建** 做第一触发（最简单，无需确认流程），**事件+确认** 做第二触发（覆盖 confirm 路径）。

#### TC-J01：创建任务 → 写入 journal

```bash
# 发送消息：创建任务
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建任务：5号楼外立面幕墙材料进场验收，负责人是彭工，截止下周五" \
  --sender "彭工" --sender-id "peng_gong"
```

| 字段 | 值 |
|------|-----|
| **触发写入函数** | `TaskApplication.handle_task()` — `self._journal.append()` |
| **预期 journal 内容** | 一行 `[YYYY-MM-DD] {user_id} 创建任务：5号楼外立面幕墙材料进场验收（TSK-YYYYMMDD-NNNN），负责人彭工` |
| **预期目录文件** | `项目日志.md` 末尾新增一行 |
| **通过标准** | `项目日志.md` 中存在含 "外立面幕墙" + "TSK" 的行 |

#### TC-J02：创建会议 → 写入 journal

```bash
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "记录会议：生态城26#地景观方案评审会，参会人员有设计部王工、工程部李工，会议确定铺装选用花岗岩材质" \
  --sender "彭工" --sender-id "peng_gong"
```

| 字段 | 值 |
|------|-----|
| **触发写入函数** | `MeetingApplication.handle_meeting()` — `self._journal.append()` |
| **预期 journal 内容** | 一行 `[YYYY-MM-DD] {user_id} 录入会议纪要：生态城26#地景观方案评审会（MTG-YYYYMMDD-NNNN）` |
| **通过标准** | `项目日志.md` 中存在含 "景观方案评审会" + "MTG" 的行 |

#### TC-J03：创建事件 + 确认 → 写入 journal（两轮对话）

第1轮 — 创建事件（进入 pending 状态）：
```bash
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "记录事件：3号楼外墙保温样板验收完成，验收合格" \
  --sender "彭工" --sender-id "peng_gong"
```
→ 预期回复含 "确认"/"好的" 引导，pending_confirmation=True

第2轮 — 确认事件（触发 journal 写入）：
```bash
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "确认" \
  --sender "彭工" --sender-id "peng_gong"
```

| 字段 | 值 |
|------|-----|
| **触发写入函数** | `EventApplication.handle_confirmation()` — `self._journal.append()` |
| **预期 journal 内容** | 一行 `[YYYY-MM-DD] {user_name} 确认录入事件：3号楼外墙保温样板验收完成（EVT-YYYYMMDD-NNNN）` |
| **通过标准** | `项目日志.md` 中存在含 "保温样板" + "EVT" + "确认录入" 的行 |

---

### 实验二：notebooks/ — 待解决问题销项

**触发路径**：IM 对话 → SessionAgent → 意图路由 → SOP-008-SYS-pending_issue → WorkItemAgent node3 → `manage_pending_issues` 工具 → `PendingIssuesService.add()` → `emily-data/notebooks/待解决问题.md`

**设计思路**：pending_issue 通过 `manage_pending_issues` 工具的 `action="add"` 写入。当前工具支持 `list_pending`/`list_resolved`/`resolve` 但缺少 `add` action。

**⚠️ 前置需要**：`pending_issue_tool.py` 需添加 `action="add"` 支持，或构造一个触发守护核验 `force=true` 的场景让 `event_tool.py` 内部调用 `pending_issues.add()`。

**最快路径**：使用守护核验场景。录入一个可疑事件并坚持 force 录入 —— 系统会将核验发现写入待解决问题清单。

#### TC-N01：force 录入触发 pending_issue

```bash
# 第1轮：录入可疑事件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "记录事件：全项目所有楼栋已竣工验收完成" \
  --sender "彭工" --sender-id "peng_gong"
```

第2轮 — 坚持录入（force=true，触发 pending issue 写入）：
```bash
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "坚持录入" \
  --sender "彭工" --sender-id "peng_gong"
```

| 字段 | 值 |
|------|-----|
| **触发写入函数** | `EventApplication` force 路径 → `pending_issues.add()` |
| **预期目录文件** | `待解决问题.md` 中新增 `### PND-YYYYMMDD-NNNN` 条目 |
| **通过标准** | `待解决问题.md` 中包含 "竣工" 或 "验收" 相关的问题描述 |

**后备方案**：如果守护核验未触发（LLM 无核验输出），改为直接检查 `待解决问题.md` 是否被 `PendingIssuesService._ensure_file()` 创建了模板文件。

---

### 实验三：user_memory/ — 长期记忆写入

**触发路径**：IM 对话 → SessionAgent → 意图路由 → SOP-007-REC-user_memory → WorkItemAgent node3 → `write_user_memory` 工具 → `UserMemoryService.save_memory()` → `emily-data/user_memory/{用户名}-长期记忆.md`

**设计思路**：当用户表达 "以后/每周/定期/随时" 等长期意图时，Agent 路由到 SOP-007（长期记忆 SOP），调用 `write_user_memory` 工具。

#### TC-M01：表达长期需求 → 写入 user_memory

```bash
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "以后每周五帮我自动检查生态城26#地所有在建楼栋的进度，卡滞超过7天的节点要通知我" \
  --sender "彭工" --sender-id "peng_gong"
```

| 字段 | 值 |
|------|-----|
| **触发写入函数** | `UserMemoryService.save_memory(user_name="彭工", content=...)` |
| **预期目录文件** | `彭工-长期记忆.md` |
| **预期内容** | 含 "每周五"、"卡滞"、"进度" 等关键词 |
| **通过标准** | `彭工-长期记忆.md` 文件存在，内容包含用户表达的长期需求描述 |

**后备方案**：如果 LLM 路由未命中 SOP-007（置信度低），改为直接验证 `UserMemoryService` 在 Core 初始化时是否正确创建了 `memory_dir`。

---

### 实验四：attachments/ — 文件存档

**触发路径**：这个目录由 `FileStorageService.store_attachment()` 写入，该函数从 IM URL 下载文件到 `{storage_root}/{platform}/{YYYY-MM}/FIL-YYYYMMDD-NNNN.ext`。

**⚠️ 限制说明**：`FileStorageService` 需要真实的 IM 附件 URL 才能触发下载。在 emy-test 模拟环境中，无法提供真实附件 URL。但在 emy-test CLI 中可通过 `--message` 附带图片 URL（如果 napcat/astrbot 有缓存的附件）。

**后备验证方案**：不通过 IM 对话触发，改为直接验证 `FileStorageService` 在 Core 初始化后的实例状态。

#### TC-A01：文件存储服务可用性验证（后备方案）

```bash
# 在容器内直接调用 Python 验证 FileStorageService
docker exec emily-core python -c "
from emily_core.services.file_storage_service import FileStorageService
fs = FileStorageService(storage_root='/app/attachments', platform='test')
d = fs.ensure_dir()
print(f'Storage dir: {d}')
print(f'Dir exists: {d.exists()}')
# 写一个测试文件
test_file = d / 'TEST_emily_data_connection.md'
test_file.write_text('# Emily Data 连接测试\n\n测试时间: 2026-07-01\n')
print(f'Test file written: {test_file}')
print(f'File size: {test_file.stat().st_size} bytes')
"
```

| 字段 | 值 |
|------|-----|
| **验证对象** | `FileStorageService` 能否在 `/app/attachments/` 下创建目录和文件 |
| **预期文件** | `attachments/test/{YYYY-MM}/TEST_emily_data_connection.md` |
| **通过标准** | 文件写入成功，磁盘可读，内容完整 |

**清理**：测试完成后删除测试文件。

---

## 五、执行顺序

| 步骤 | 实验 | 预计耗时 | 依赖 |
|------|------|---------|------|
| 1 | 环境检查 + 基线快照 | 1 min | 无 |
| 2 | TC-J01：创建任务 → journal | 1 min | 步骤 1 |
| 3 | TC-J02：创建会议 → journal | 1 min | 步骤 1 |
| 4 | TC-J03：创建事件+确认 → journal | 2 min | 步骤 1 |
| 5 | TC-N01：force录入 → notebooks | 2 min | 步骤 1 |
| 6 | TC-M01：长期需求 → user_memory | 1 min | 步骤 1 |
| 7 | TC-A01：文件存储 → attachments | 1 min | 步骤 1（后备方案） |
| 8 | 检查 + 汇总 + 清理 | 1 min | 全部 |

**总预计耗时**：约 10 分钟。

---

## 六、EMY-TEST 超时处理

emytest 默认 120s 超时。遇到超时时：

1. 观察日志错误：`docker logs --tail 30 emily-core 2>&1 | grep -i error`
2. 如果消息被忽略（群聊未 @bot），检查接管模式
3. 记录为 ⚠️ TIMEOUT，注明原因，进入下一实验

---

## 七、清理计划

测试完成后执行：

```bash
# 1. 删除测试写入的 user_memory 文件
rm emily-data/user_memory/彭工-长期记忆.md   # 如果 TC-M01 成功写入

# 2. 删除 attachments 测试文件
rm -rf emily-data/attachments/test/

# 3. 清理 notebooks 测试 PND 条目（通过 resolve 工具或直接编辑文件）
#    如果 TC-N01 触发了 force 录入，在 待解决问题.md 中手动移除测试条目

# 4. journal 保留（事件流水日志允许测试条目作为历史记录）
```

---

## 八、输出

测试执行后生成报告保存到：`需求文件/emily-data功能实验/emily-data功能实验_执行报告_V1.md`
