# emily-data 功能实验 — 代码审核报告

> **角色**: 资深架构师 / 程序员 | **日期**: 2026-07-01 | **版本**: V1
>
> **审核范围**: `emily-data/` 下 4 个读写目录（journal / notebooks / user_memory / attachments）对应的全链路代码

---

## 一、总体结论

本次审核确认：**上一轮修复未能解决核心问题**。原因并非个别代码行有 bug，而是存在 **5 个系统性设计缺口 + 2 个初始化缺陷**，导致 emily-data 文件级持久化链路从意图路由、工具注册、用户信息传递到物理文件存储各环节均存在断点。

---

## 二、测试结果与根因对照

| 用例 | 测试结果 | 根本原因 | 严重度 |
|------|---------|---------|--------|
| TC-J01 创建任务→journal | ❌ | LLM 意图路由将"任务"误判为"事件" | 中 |
| TC-J02 创建会议→journal | ⚠️ | Journal name 传入 UUID 而非姓名 | 高 |
| TC-J03 事件确认→journal | ❌ | **确认对话无拦截机制**，被当作新消息走 fallback | 严重 |
| TC-N01 force录入→notebooks | ❌ | `manage_pending_issues` 工具缺少 `add` action | 严重 |
| TC-M01 长期需求→user_memory | ❌ | `write_user_memory` 工具注册时 **user_name=空字符串** | 严重 |
| TC-A01 文件存储→attachments | ⚠️ | `FileApplication` 只写 DB 元数据，未调用 `FileStorageService` | 高 |

---

## 三、逐项详细分析

### 3.1 TC-J03：事件确认链路断裂（最严重）

**问题现象**：用户回复"确认"后，系统当作新对话处理，事件保持 pending，journal 未写入。

**根因**：`SessionAgent.handle()` (`session_agent.py:115`) 对所有消息一律走 LLM 意图路由管道。系统完全没有检查"当前 session 是否有 pending 确认项"的拦截逻辑。

**代码证据**（`session_agent.py:115-161`）：

```python
async def handle(self, message):
    content = (message.content or "").strip()

    # ① 短路：仅处理闲聊/问候，不处理确认
    fast = self._try_fast_reply(content, message.sender_name)
    if fast is not None:
        return self._reply(message, fast)

    # ② LLM 意图识别（"确认"被 LLM 解释为 fallback）
    work_items = await self._split_into_workitems(message)
    # ...
```

`_try_fast_reply()` 只匹配"你好/谢谢/再见/你是谁"，不匹配"确认"/"取消"/"好的"等确认类回复。

而 `ConfirmQueue`（`confirm_queue.py`）已经实现了完整的优先级队列，`EventService.find_pending_by_conversation()` 也能查到 pending 事件，`EventApplication.handle_confirmation()` 已经实现了确认逻辑 + journal 写入。但这些功能之间的**桥梁代码缺失**——没有在 `SessionAgent.handle()` 的入口处检查 pending 状态。

**修复方向**：在 `SessionAgent.handle()` 的 ① 和 ② 之间插入 pending confirmation 检查逻辑：若当前 conversation 存在 pending 事件，将"确认/取消"等关键词匹配到 `EventApplication.handle_confirmation()`。

---

### 3.2 TC-M01：write_user_memory 工具 user_name 为空（严重）

**问题现象**：系统回复"好的老彭，我记住了！"但实际未写入任何文件。Guardian 检测到矛盾。

**根因**：`write_user_memory` 工具在注册时 **未传入当前用户名**，导致 `create_memory_tool()` 的 `user_name` 参数保持默认值 `""`。

**代码证据**（`tools/registry.py:136-142`）：

```python
# _register_business() 中
mem = getattr(core, "_user_memory_service", None)
if mem is not None and not reg.has("write_user_memory"):
    from .memory_tool import create_memory_tool
    bt = create_memory_tool(mem)  # ← user_name=""  （默认值）
    reg.register(...)
```

`create_memory_tool()` 的定义（`tools/memory_tool.py:15`）：

```python
def create_memory_tool(user_memory_service, user_name: str = "") -> ToolDefinition:
```

在 tool handler 中（`memory_tool.py:46-50`）：

```python
if not name:
    return {"success": False, "message": "无法确定用户名，记忆未写入"}
```

因此 `write_user_memory` 工具**无论 LLM 如何调用都会失败**。而 LLM 并不知道工具会失败——它调用了工具，收到了 `success: false`，但在生成最终回复时仍然对用户说"我记住了"，产生了 Guardian 检测到的矛盾。

**此外**，`_init_m8c_services()` 中还有一次重复注册（`__init__.py:576-586`），同样是 `user_name=""`：

```python
mem_tool = create_memory_tool(self._user_memory_service)
# ← user_name 也未传入
self._business_flow_tools.register(...)
```

两个注册点都有同样的问题。

**修复方向**：`write_user_memory` 工具的 user_name 必须在运行时从 message sender_name 或 user binding 动态获取，而非注册时固定。需要改为 handler 内部从调用上下文获取用户名。

---

### 3.3 TC-N01：manage_pending_issues 缺少 add action（严重）

**问题现象**：无法通过 IM 对话新增待解决问题到 `notebooks/待解决问题.md`。

**根因**：`PendingIssuesService.add()` 方法实现了完整的写入逻辑（写入 `### PND-YYYYMMDD-NNNN` 条目到 markdown 文件），但 `pending_issue_tool.py` 中 `create_pending_issue_tool()` 的 `execute()` 函数只支持三种 action：`list_pending`、`list_resolved`、`resolve`。**没有 `add` action**。

**代码证据**（`tools/pending_issue_tool.py:26-91`）：

```python
async def execute(args: dict) -> dict:
    action = args.get("action", "list_pending")
    if action == "list_pending": ...
    elif action == "list_resolved": ...
    elif action == "resolve": ...
    else:
        return {"success": False, "error_code": "unknown_action",
                "reply": f"不支持的操作: {action}"}
```

而 `PendingIssuesService.add()`（`services/pending_issues.py:87-141`）已经完整实现了所有写入逻辑——只需在 tool 层的 JSON Schema 和 execute 函数中添加 `add` action 的分支即可。

**修复方向**：
1. 在 `pending_issue_tool.py` 的 `execute()` 中添加 `action == "add"` 分支
2. 在 `parameters` JSON Schema 的 `action` enum 中添加 `"add"`
3. 在 `parameters` 中添加 `raised_by`、`source`、`description`、`suggestion` 等字段

---

### 3.4 TC-J02：Journal 姓名显示 UUID 而非真实姓名（高）

**问题现象**：`项目日志.md` 中会议条目显示 `[2026-07-01] b1c0db35-... 录入会议纪要...` 而非 `[2026-07-01] 彭工 录入会议纪要...`

**根因**：`MeetingApplication.handle_meeting()` 和 `TaskApplication.handle_task()` 直接将 `cmd.creator_id`（数据库 UUID）传给 `journal.append(name=...)` 而不做用户名解析。

**代码证据对比**：

`EventApplication.handle_confirmation()`（**做对了**——有 UserRepository 查询）：
```python
# event_app.py:99-108
if self._journal is not None:
    user_name = getattr(event, "user_id", "") or ""
    try:
        from ..repositories.user_repo import UserRepository
        u = UserRepository.get(event.user_id) if event.user_id else None
        if u:
            user_name = getattr(u, "real_name", "") or getattr(u, "username", "") or ""
    except Exception:
        pass
    self._journal.append(name=user_name or "用户", ...)
```

`MeetingApplication.handle_meeting()`（**做错了**——直接传 UUID）：
```python
# meeting_app.py:38-40
self._journal.append(
    name=cmd.creator_id or "用户",   # ← UUID，不是姓名
    summary=f"录入会议纪要：{meeting.title}（{meeting.meeting_no}）",
)
```

`TaskApplication.handle_task()` 同样问题（`task_app.py:44`）：
```python
self._journal.append(name=cmd.creator_id or "用户", ...)
```

`FileApplication.handle_file()` 同样问题（`file_app.py:41`）：
```python
self._journal.append(name=cmd.uploaded_by or "用户", ...)
```

4 个 Application 中只有 `EventApplication.handle_confirmation()` 做了 UserRepository 查询，其余 3 个都直接传 UUID。

**修复方向**：将 EventApplication 中的 user_name 解析模式抽取为公共方法，在 Task/Meeting/File Application 的 journal.append 调用前统一使用。

---

### 3.5 TC-A01：FileApplication 未触发物理文件存储（高）

**问题现象**：`files` 表有记录、journal 有记录，但 `attachments/` 目录无物理文件。

**根因**：`FileApplication.handle_file()` 只调用 `FileService.create_file_record()` 写 DB 元数据，完全不涉及 `FileStorageService`。

**代码证据**（`application/file_app.py:24-51`）：

```python
async def handle_file(self, route_result, user_id, message_id):
    cmd = FileCommand(filename=..., uploaded_by=...)
    f = self.file_service.create_file_record(cmd)  # ← 仅 DB insert
    # journal...
    reply = FileService.format_reply(f)
    return HandlerResult(success=True, ...)  # ← 不下载、不写磁盘
```

而 `FileStorageService.store_attachment()` / `store_attachment_async()`（`services/file_storage_service.py:86-186`）完整实现了 URL 下载 → 磁盘写入 → DB 联动（files 表 + message_attachments 表），但它**从 FileApplication 的调用链中完全断开了**。

这两条链路之间的关系：
- **FileApplication** → `FileService.create_file_record()` → 只写 DB 元数据（当前路径）
- **FileStorageService** → `store_attachment()` → 下载 + 磁盘 + DB（独立存在，无人调用）

**修复方向**：
1. 在 `FileApplication.handle_file()` 中，如果消息包含附件 URL，调用 `FileStorageService.store_attachment()` 下载文件
2. 或者将 `FileApplication` 的 handle 逻辑与 `FileStorageService` 合并

---

### 3.6 TC-J01：LLM 意图路由偏差（中）

**问题现象**："帮我记个待办"类消息被路由到 SOP-002（事件记录）而非 SOP-003（任务管理）。

**根因**：这是 LLM 意图识别的问题。`_SESSION_SYSTEM_PROMPT` 中的 SOP 目录描述可能未能清晰区分"事件"和"任务"。具体取决于 SOP markdown 文件中 `trigger_keywords` 和 `deny_conditions` 的配置是否足够精确。

**修复方向**：检查 SOP-002 和 SOP-003 的触发关键词是否有重叠，在 SOP 文件中明确"待办/提醒/截止日期"类的描述属于任务，添加 deny_conditions 防止事件误匹配。

---

## 四、额外发现的系统性问题

### 4.1 `_init_m8c_services()` 中的重复注册

`EmilyCore._init_m8c_services()`（`__init__.py:576-586`）中对 `write_user_memory` 和 `manage_pending_issues` 进行注册，而 `register_all()` → `_register_business()` / `_register_project()` 中也有相同的注册逻辑。虽然 `not reg.has(...)` 检查防止了重复，但**两处的 user_name 参数均为空**，意味着无论哪个先注册，工具都是坏的。

### 4.2 文档注释 vs 代码实际路径不一致

`EventJournal` 的文档注释（`event_journal.py:7`）写：
```python
journal = EventJournal(path="tem_log/项目日志.md")
```

实际使用时，`_init_m8c_services()` 传入了显式路径（`/app/journal/` 或 `emily-data/journal/`），但如果因某种原因回退到 EventJournal 的默认路径，会写入错误位置 `tem_log/` 而非 `emily-data/journal/`。

同样的，`UserMemoryService` 默认路径是 `memory/`，`PendingIssuesService` 默认路径是 `tem_log/待解决问题.md`——都与 `emily-data/` 下的预期子目录不一致。

---

## 五、修复优先级建议

| 优先级 | 问题 | 修复工作量 | 影响范围 |
|--------|------|-----------|---------|
| **P0** | TC-M01: write_user_memory user_name 为空 | 小（handler 从上下文获取用户名） | user_memory 完全不可用 |
| **P0** | TC-J03: 确认链路缺失 | 中（SessionAgent 增加 pending 检查） | 事件确认/取消全部失效 |
| **P0** | TC-N01: manage_pending_issues 缺 add action | 小（tool 层增加 action 分支） | notebooks 写链路不可用 |
| **P1** | TC-J02: journal 显示 UUID | 小（3 个 Application 各加 user 查询） | 日志可读性 |
| **P1** | TC-A01: FileApplication 不触发物理存储 | 中（整合 FileStorageService 调用） | attachments 物理文件缺失 |
| **P2** | TC-J01: 意图路由偏差 | 小（调整 SOP 关键词） | 用户体验 |
| **P2** | 默认路径与 emily-data 不一致 | 小（修正代码默认路径注释） | 开发环境一致性 |

---

## 六、架构层面的反思

当前 emily-data 功能实验暴露的核心问题是**文件级持久化链路缺少端到端集成测试**。每个模块（EventJournal、UserMemoryService、PendingIssuesService、FileStorageService）单独来看实现正确，但串联到 IM→SessionAgent→LLM意图→工具→文件写入的完整路径时，多处断点：

1. **工具注册时上下文缺失**（user_name 在注册时而非运行时绑定）
2. **确认类交互无状态记忆**（Session 不记住 pending 状态）
3. **两层路径回退机制复杂**（容器路径 → 宿主机路径 → 代码默认路径，任一环节断裂就写错位置）
4. **Application 层职责不完整**（FileApplication 只管 DB 元数据，不管物理文件）

建议：
- 在 `emy-test` 或 `smoke_test` 中增加 emily-data 文件级写入的端到端验证
- 将工具 handler 的参数绑定从"注册时固定"改为"运行时从上下文动态获取"
- 统一 emily-data 路径管理为单一配置项 `EMILY_DATA_DIR`，各服务相对此路径定位
