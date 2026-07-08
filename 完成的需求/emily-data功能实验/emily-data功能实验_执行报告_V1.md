# emily-data 功能实验 — 执行报告

> **版本**: V1 | **日期**: 2026-07-01 | **状态**: 已执行

---

## 一、环境检查 + 基线快照

### 1.1 容器状态

| 容器 | 状态 |
|------|------|
| emily-core | ✅ 运行中 |
| emily-postgres | ✅ 运行中 |

### 1.2 前置配置

- **测试用户**：`彭工`（peng_gong），手动创建，permission_level=5
- **SOP 注册**：12 个 SOP 手动插入到 `sop_business_flows` 表，`is_public=true`，`min_permission_level=1`
- **SOP ID 修正**：将 `sop_id` 从完整文件名（如 `SOP-009-REC-plan_task`）截断为意图路由产出的短格式（如 `SOP-009-REC`），解决 AuthHook 权限不匹配问题
- **系统用户**：创建 `scheduler` 用户解决 plan_task_scheduler 错误

### 1.3 基线快照（执行前）

| 目录/表 | 状态 |
|---------|------|
| `journal/项目日志.md` | 1 条（TC12 连接测试） |
| `notebooks/待解决问题.md` | 1 条（PND-20260701-0001，TC14） |
| `user_memory/` | 仅 `.gitkeep` |
| `attachments/` | 仅 `.gitkeep` |
| `tasks` 表 | 0 条 |
| `events` 表 | 0 条 |
| `meetings` 表 | 0 条 |
| `files` 表 | 0 条 |

---

## 二、测试执行记录

### TC-J01：创建任务 → journal

**发送消息**：
> 帮我记个待办：安排彭工下周五之前完成5号楼外立面幕墙材料进场验收，优先级高

**实际结果**：
- 系统回复：创建了待办任务，生成事件记录 `EVT-20260701-0005`
- 实际路由到 SOP-002（事件记录）而非 SOP-003（任务管理）
- `events` 表：新增 1 条，status=pending，title="未命名事件"
- `tasks` 表：仍然为 0 条
- `journal/项目日志.md`：无新增（事件的 journal 写入需走确认路径）

**判断**：❌ **不通过** — 未创建 task，journal 无 TSK 条目

---

### TC-J02：创建会议 → journal

**发送消息**：
> 帮我记录会议纪要：今天上午10点5号楼幕墙进度讨论会，参加人彭工、张总、王监理，结论是材料进场时间需要提前到下周三，张总负责协调

**实际结果**：
- 系统回复：会议已记录但部分信息未正确保存
- Guardian 警告：标题和参会人与工具输入不一致，initiator_id 为空
- `meetings` 表：新增 `MTG-20260701-0001`，title="未命名会议"
- `journal/项目日志.md`：新增一行 `[2026-07-01] b1c0db35-... 录入会议纪要：未命名会议（MTG-20260701-0001）`

**判断**：⚠️ **部分通过** — 会议已创建，journal 有记录，但姓名为 UUID 而非"彭工"，标题为"未命名会议"

---

### TC-J03：创建事件 + 确认 → journal

**第1轮发送消息**：
> 帮我记个事件：今天上午5号楼一楼大厅铺装完成了45平米，验收通过

**第1轮实际结果**：
- 系统回复：已记录事件，编号 `EVT-20260701-0007`，请回复"确认"
- `events` 表：新增 1 条，status=pending，title="未命名事件"

**第2轮发送消息**：
> 确认

**第2轮实际结果**：
- 系统回复："好的，已收到您的确认，无需进一步操作"（被当作普通对话处理）
- `events` 表：EVT-20260701-0007 仍为 pending（未确认）
- `journal/项目日志.md`：无新增

**判断**：❌ **不通过** — 确认未被识别为事件确认流程，事件未确认，journal 未写入

---

### TC-N01：force录入 → notebooks

**发送消息**：
> 记一个待解决问题：S4地块CFG桩检测报告与设计图纸有偏差，桩位偏移约5cm，需要设计院复核

**实际结果**：
- 消息发送成功，系统返回正常
- `notebooks/待解决问题.md`：无新增条目
- 数据库中不存在 `pending_issues` 表

**判断**：❌ **不通过** — notebook 未更新，pending_issues 模块表结构缺失

---

### TC-M01：长期需求 → user_memory

**发送消息**：
> 以后每周五上午提醒我检查项目进度，另外叫我老彭就行

**实际结果**：
- 系统回复："好的老彭，我记住了！以后每周五上午我会提醒你检查项目进度"
- Guardian 警告："回复声称记住了并会提醒，但执行步骤显示创建记忆失败，存在矛盾"
- 数据库中不存在 `user_memories` 表
- `user_memory/` 目录仅含 `.gitkeep`

**判断**：❌ **不通过** — user_memory 模块表结构缺失，未写入文件

---

### TC-A01：文件存储 → attachments

**发送消息**（附带文件 `_test_file.txt`）：
> 这份材料验收清单帮我归档到5号楼项目

**实际结果**：
- 系统回复：已归档，文件编号 `FIL-20260701-0001`，状态"待处理"
- `files` 表：新增 1 条（FIL-20260701-0001）
- `journal/项目日志.md`：新增一行 `[2026-07-01] 14d979a2-... 归档文件：未命名文件（FIL-20260701-0001）`
- 但 `attachments/` 目录仅含 `.gitkeep`，无物理文件

**判断**：⚠️ **部分通过** — 数据库元数据已写入，journal 已记录，但无物理文件存储

---

## 三、执行后数据状态

| 数据目录/表 | 记录数 | 状态 |
|------------|--------|------|
| `journal/项目日志.md` | 3 条 | TC12 + MTG + FIL |
| `notebooks/待解决问题.md` | 1 条 | 仅 TC14 旧条目 |
| `user_memory/` | 空 | 仅 `.gitkeep` |
| `attachments/` | 空 | 仅 `.gitkeep` |
| `tasks` 表 | 0 | 空 |
| `events` 表 | 7 | 全部 pending |
| `meetings` 表 | 1 | MTG-20260701-0001，title="未命名会议" |
| `files` 表 | 1 | FIL-20260701-0001 |

---

## 四、结果汇总

| 用例 | 描述 | 通过 | 备注 |
|------|------|------|------|
| TC-J01 | 创建任务 → journal | ❌ | 路由到事件而非任务，tasks 表空 |
| TC-J02 | 创建会议 → journal | ⚠️ | 会议创建成功，journal 有记录但姓名为 UUID |
| TC-J03 | 创建事件+确认 → journal | ❌ | 确认流程未触发，事件仍 pending |
| TC-N01 | force录入 → notebooks | ❌ | pending_issues 表不存在 |
| TC-M01 | 长期需求 → user_memory | ❌ | user_memories 表不存在 |
| TC-A01 | 文件存储 → attachments | ⚠️ | 元数据写入成功，物理文件未存储 |

**通过率：0/6 完全通过，2/6 部分通过**

---

## 五、核心发现

1. **意图路由偏差**："创建任务"类消息被 LLM 路由到 SOP-002（事件记录）或 SOP-009（计划任务），而非 SOP-003（任务管理），导致 `tasks` 表始终为空
2. **确认机制失效**：pending 事件的确认对话被当作新对话处理，无法完成确认流程
3. **数据提取不准**：事件/会议标题均为"未命名事件/会议"，LLM 结构化输出未正确提取字段
4. **Journal 姓名**：写入 journal 时用户姓名显示为 UUID 而非真实姓名
5. **user_memory 模块**：`user_memories` 表缺失，`UserMemoryService` 可能未初始化
6. **pending_issues 模块**：`pending_issues` 表缺失
7. **文件物理存储**：文件元数据写入 DB，但物理文件未存入 attachments 目录

---

## 六、清理

- 已删除测试临时文件 `_test_send.py`、`_test_file.txt`
- journal 保留测试条目作为历史记录
- DB 测试数据（7 条 pending events、1 条 meeting、1 条 file）保留，未清理
