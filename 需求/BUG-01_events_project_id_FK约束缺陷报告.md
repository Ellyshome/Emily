# BUG-01: events 表 project_id 外键约束违反

| 属性 | 值 |
|------|-----|
| **缺陷编号** | BUG-01 |
| **严重级别** | P2 — 功能正确但录入失败（核心业务流程阻塞） |
| **发现日期** | 2026-07-29 |
| **发现方式** | emy-test 生产环境实战测试 |
| **影响范围** | 事件录入、任务录入、会议录入（三个功能均受影响） |
| **责任人** | 待指定 |

---

## 现象

用户通过 Emily 输入事件录入消息后，系统尝试写入 PostgreSQL 时抛出 `ForeignKeyViolation`，导致整个 WorkItem 的 SSE 回复超时（HTTP 500 → No SSE reply），用户看到"不接管，无回复"。

**Docker 日志关键片段**：

```
sqlalchemy.exc.IntegrityError: (psycopg2.errors.ForeignKeyViolation)
insert or update on table "events" violates foreign key constraint "events_project_id_fkey"
DETAIL: Key (project_id)=(翠湖庭院) is not present in table "projects".
```

复现命令：

```powershell
uv run python .claude/skills/emy-test/cli.py --managed ^
  --qq 123456001 --sender 王建国 ^
  --message "帮我记一下样板段放线完成，翠湖庭院项目"
```

---

## 根因分析

### 数据流追踪

LLM 从用户消息"翠湖庭院项目"中提取参数时，将项目名称 `"翠湖庭院"` 放入了 `project_id` 字段而非 `project_name` 字段，导致下游代码绕过了项目名称→UUID 的解析逻辑。

完整链路（6 层）：

#### 第 1 层：Tool Handler

[event_tool.py](file:///d:/app/Emily/emily-core/emily_core/tools/event_tool.py) 的 `handle_record_event()` 直接从 LLM 提取的 `params` 中透传 `project_id` 和 `project_name`，不做任何解析或验证：

```python
route_result = RouteResult(
    intent="event_record",
    project_name=params.get("project_name"),
    project_id=params.get("project_id"),     # ← "翠湖庭院" 出现在这里
    ...
)
```

#### 第 2 层：Application

[event_app.py](file:///d:/app/Emily/emily-core/emily_core/application/event_app.py) 的 `_build_command()` 直接将 RouteResult 映射为 `EventCommand`，无校验。

#### 第 3 层：Service — 缺陷所在

[event_service.py](file:///d:/app/Emily/emily-core/emily_core/services/event_service.py#L38-L43) 的 `create_pending_event()`：

```python
# 解析项目 ID
project_id = cmd.project_id
if not project_id and cmd.project_name:
    project = self.repo.find_project_by_name(cmd.project_name)
    if project:
        project_id = project.id
```

**缺陷逻辑**：`if not project_id` 仅在 `project_id` 为 falsy（None / ""）时才触发 name→UUID 解析。当 LLM 错误地将 `"翠湖庭院"` 填入 `project_id` 时，该字符串是 truthy 的，条件分支被跳过。`"翠湖庭院"` 原样传入后续 INSERT。

#### 第 4 层：Repository

[event_repo.py](file:///d:/app/Emily/emily-core/emily_core/repositories/event_repo.py#L85) 直接插入：

```python
event = Event(
    ...
    project_id=project_id,  # ← "翠湖庭院"，不是 UUID
    ...
)
session.flush()  # ← ForeignKeyViolation
```

#### 第 5 层：ORM Model

[models.py](file:///d:/app/Emily/emily-core/emily_core/infrastructure/database/models.py#L202)：

```python
project_id = Column(String, ForeignKey("projects.id"), nullable=True)
```

#### 第 6 层：已有但未被调用的解析方法

[event_repo.py](file:///d:/app/Emily/emily-core/emily_core/repositories/event_repo.py#L234-L238) 已实现 `find_project_by_name()`，可正确返回 Project ORM 对象及其 UUID：

```python
@staticmethod
def find_project_by_name(name: str) -> Optional[Project]:
    """按名称查找项目（精确匹配）。"""
    with get_session() as session:
        return session.query(Project).filter(Project.name == name).first()
```

该方法仅在 `EventService` 中条件分支满足时才被调用，从未对已在 `project_id` 中的值生效。

---

## 同源缺陷：task_service.py & meeting_service.py

### TaskService — 无任何项目名称解析

[task_service.py](file:///d:/app/Emily/emily-core/emily_core/services/task_service.py#L17-L31)：

```python
def create_task(self, cmd: TaskCommand) -> Task:
    task_no = self.repo.generate_task_no()
    task = self.repo.create(
        task_no=task_no,
        title=cmd.title,
        project_id=cmd.project_id or None,  # ← 完全没做解析
        ...
    )
```

不存在任何 `project_name → UUID` 的解析逻辑。只要 LLM 将项目名放入 `project_id`，必然失败。

### MeetingService — 同样无解析

[meeting_service.py](file:///d:/app/Emily/emily-core/emily_core/services/meeting_service.py#L18-L30)：

```python
def create_meeting(self, cmd: MeetingCommand) -> Meeting:
    meeting_no = self.repo.generate_meeting_no()
    meeting = self.repo.create(
        meeting_no=meeting_no,
        title=cmd.title,
        project_id=cmd.project_id or None,  # ← 同样没做解析
        ...
    )
```

与 TaskService 一模一样的问题。

---

## 修复方案

### 推荐方案：Service 层增加 UUID 格式判断（修改量小，覆盖广）

在三个 Service 的创建方法中，增加 `project_id` 的安全网校验：如果 `project_id` 非空但不匹配 UUID 格式，尝试按名称解析。

以 `EventService.create_pending_event()` 为例：

```python
# 解析项目 ID（修正版）
import re
UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE
)

project_id = cmd.project_id
# 如果 project_id 存在但不是 UUID 格式，尝试作为项目名查找
if project_id and not UUID_PATTERN.match(project_id):
    project = self.repo.find_project_by_name(project_id)
    if project:
        project_id = project.id
    else:
        project_id = None  # 找不到则按无项目处理，避免 DB FK 报错

# 原有逻辑：project_id 为空且 project_name 存在时按名称查找
if not project_id and cmd.project_name:
    project = self.repo.find_project_by_name(cmd.project_name)
    if project:
        project_id = project.id
```

同样应用于 [task_service.py](file:///d:/app/Emily/emily-core/emily_core/services/task_service.py#L22) 和 [meeting_service.py](file:///d:/app/Emily/emily-core/emily_core/services/meeting_service.py#L23)。

### 备选方案：Tool Handler 层提前解析（更深层防御）

在 `event_tool.py` / `task_tool.py` / `meeting_tool.py` 的 handler 中，接收 LLM 参数后立即调用 project 查询，将 `project_id` 统一修正为 UUID。此方案防御更早，但需要传入 project_repo 依赖。

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `emily_core/services/event_service.py` L38-43 | 增加 project_id 非 UUID 格式时的名称解析分支 |
| `emily_core/services/task_service.py` L22 | 增加完整的 project_id 解析逻辑（从无到有） |
| `emily_core/services/meeting_service.py` L23 | 同上 |

---

## 验证方法

修复后执行以下 emy-test 用例：

```powershell
# 带项目名的事件录入
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "帮我记一下样板段放线完成，翠湖庭院项目"

# 带项目名的任务录入
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "创建任务：景观验收，翠湖庭院项目"

# 不带项目名（仅保留 project_name → UUID 解析回退）
uv run python .claude/skills/emy-test/cli.py --managed \
  --qq 123456001 --sender 王建国 \
  --message "记录事件：材料进场" --project "翠湖庭院"
```

预期：每个命令返回确认卡片或录入成功的回复，不再超时。
