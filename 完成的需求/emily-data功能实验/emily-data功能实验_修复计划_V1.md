# emily-data 功能实验 — 修复计划

> **版本**: V1 | **日期**: 2026-07-01 | **状态**: 待实施
>
> **前置文档**: [emily-data功能实验_代码审核报告_V1.md](emily-data功能实验_代码审核报告_V1.md)

---

## 〇、修复概览

| 阶段 | 问题编号 | 简述 | 改动文件数 | 风险 | 预计耗时 |
|------|---------|------|-----------|------|---------|
| **阶段一** | TC-M01 | write_user_memory user_name 运行时解析 | 2 | 低 | 30min |
| **阶段一** | TC-N01 | manage_pending_issues 添加 add action | 1 | 低 | 20min |
| **阶段一** | TC-J02 | journal 姓名 UUID→真实姓名 | 3 | 低 | 30min |
| **阶段二** | TC-J03 | 确认链路：LLM 意图增强 + pending 注入 | 3 | 中 | 1.5h |
| **阶段二** | TC-A01 | FileApplication 整合 FileStorageService | 2 | 中 | 1h |
| **阶段三** | TC-J01 | SOP 触发关键词优化 | 2 | 低 | 30min |
| **阶段三** | — | 清理重复注册 + 路径一致性 | 2 | 低 | 20min |

---

## 阶段一（P0/P1 独立修复，低风险）

### 修复 1：TC-M01 — write_user_memory 运行时解析用户名

**问题**：`create_memory_tool(mem)` 注册时 `user_name=""` 固定为空，导致工具永远返回"无法确定用户名"。

**方案**：工具 handler 接收 `user_id`（UUID），运行时通过 `UserRepository.get(user_id)` 查询 `real_name`。

**涉及文件**：

#### 1.1 `emily-core/emily_core/tools/memory_tool.py`

改动 `create_memory_tool()` 函数签名——增加 `user_id: str = ""` 参数：

```python
# 修改前
def create_memory_tool(user_memory_service, user_name: str = "") -> ToolDefinition:
    service = user_memory_service
    name = user_name

    async def execute(args: dict) -> dict:
        # ...
        if not name:
            return {"success": False, "message": "无法确定用户名，记忆未写入"}
        result_title = service.save_memory(user_name=name, ...)

# 修改后
def create_memory_tool(user_memory_service) -> ToolDefinition:
    service = user_memory_service

    async def execute(args: dict) -> dict:
        content = args.get("content", "")
        title = args.get("title", "")

        if not service or not service.enabled:
            return {"success": False, "message": "长期记忆服务未启用"}

        # ═══ 运行时解析用户名 ═══
        user_name = ""
        user_id = args.get("_user_id", "")

        # 1. 如果 LLM 传了 user_name 参数，直接用
        user_name = args.get("user_name", "")

        # 2. 否则从 user_id（UUID）查 User 表获取 real_name
        if not user_name and user_id:
            try:
                from ..repositories.user_repo import UserRepository
                u = UserRepository.get(user_id)
                if u:
                    user_name = getattr(u, "real_name", "") or getattr(u, "username", "") or ""
            except Exception:
                pass

        # 3. 最后 fallback
        if not user_name:
            user_name = "用户"

        result_title = service.save_memory(user_name=user_name, content=content, title=title)
        # ...
```

**说明**：`_real_execute()`（`workitem_agent.py:296-325`）调用 handler 时已注入 `user_id` 和 `message_id`。但 `BusinessFlowTool.handler` 签名是 `fn(params: dict) -> dict`，`user_id` 通过 `**handler_kwargs` 传入。这里改用 `args["_user_id"]` 方式传递——需要在 `_real_execute` 中把 `user_id` 合并到 `tool_params` dict 中。

**更优方案**：在 `_real_execute()` 中将 `user_id`、`message_id` 注入到 `tool_params` 中，所有 handler 统一从 params 获取：

```python
# workitem_agent.py _real_execute() 中
tool_params = dict(getattr(step, 'tool_params', {}) or {})
# 注入运行时上下文
tool_params["_user_id"] = context.user_id or ""
tool_params["_message_id"] = context.db_message_id or ""
tool_params["_conversation_id"] = context.message.conversation_id if context.message else ""
```

#### 1.2 `emily-core/emily_core/workitem/workitem_agent.py`

在 `_real_execute()` 方法中（约第 311 行），将 `user_id`/`message_id` 注入到 `tool_params`：

```python
# 修改前（~line 311）
tool_params = getattr(step, 'tool_params', {}) or {}

# 修改后
tool_params = dict(getattr(step, 'tool_params', {}) or {})
tool_params["_user_id"] = context.user_id or ""
tool_params["_message_id"] = context.db_message_id or ""
tool_params["_conversation_id"] = (context.message.conversation_id
                                    if context.message else "")
```

#### 1.3 `emily-core/emily_core/tools/registry.py`

`_register_business()` 中（~line 137-142），去掉 `user_name` 参数：

```python
# 修改前
bt = create_memory_tool(mem)
reg.register(_tool(bt.name, bt.description, bt.parameters, bt.execute))

# 修改后
reg.register(_tool("write_user_memory", bt.description, bt.parameters, bt.execute))
```

#### 1.4 `emily-core/emily_core/__init__.py`

`_init_m8c_services()` 中（~line 577-585），去掉重复的 `user_name` 注册逻辑（因 registry.py 已处理），只保留首次注册，删除重复：

```python
# 修改后：删除 _init_m8c_services 中的 write_user_memory 注册段（~576-586行），
# 统一由 register_all() → _register_business() 处理
```

---

### 修复 2：TC-N01 — manage_pending_issues 添加 add action

**问题**：`pending_issue_tool.py` 的 execute() 只支持 list_pending / list_resolved / resolve，缺少 add。

**涉及文件**：

#### 2.1 `emily-core/emily_core/tools/pending_issue_tool.py`

(1) 在 `execute()` 中添加 `action == "add"` 分支：

```python
elif action == "add":
    raised_by = args.get("raised_by", "用户")
    source = args.get("source", "")
    description = args.get("description", "")
    suggestion = args.get("suggestion", "")
    related_events = args.get("related_events", [])

    if not description:
        return {
            "success": False,
            "error_code": "missing_description",
            "reply": "请描述需要记录的问题。",
        }

    issue_id = pending_issues.add(
        raised_by=raised_by,
        source=source or "用户通过对话添加",
        description=description,
        suggestion=suggestion,
        related_events=related_events if isinstance(related_events, list) else [],
    )
    return {
        "success": True,
        "reply": f"已记录待解决问题 {issue_id}",
        "issue_id": issue_id,
    }
```

(2) 在 `parameters` JSON Schema 的 `action` enum 中添加 `"add"`，并添加 `add` 所需字段：

```python
"action": {
    "type": "string",
    "enum": ["list_pending", "list_resolved", "add", "resolve"],
    "description": "操作类型",
},
"raised_by": {
    "type": "string",
    "description": "提出人姓名（add 时填写，如'彭工'、'守护Agent'）",
},
"source": {
    "type": "string",
    "description": "问题来源描述（add 时填写）",
},
"description": {
    "type": "string",
    "description": "问题详细描述（add 时必填）",
},
"suggestion": {
    "type": "string",
    "description": "建议处理方式（add 时可选）",
},
"related_events": {
    "type": "array",
    "items": {"type": "string"},
    "description": "关联事件编号列表（add 时可选）",
},
```

---

### 修复 3：TC-J02 — journal 姓名 UUID → 真实姓名

**问题**：`handle_meeting` / `handle_task` / `handle_file` 将 `user_id`（UUID）直接传给 `journal.append(name=...)`。

**方案**：抽取公共的 `_resolve_user_name()` 方法，在 3 个 Application 中复用。参考 `EventApplication.handle_confirmation()` 中已有的实现。

**涉及文件**：

#### 3.1 新增公共工具函数

在 `emily-core/emily_core/application/` 下新增 `_user_utils.py`（或直接在现有位置添加一个模块级函数）：

```python
"""Application 层用户信息工具。"""
import logging

logger = logging.getLogger("emily.app.user_utils")

def resolve_user_name(user_id: str) -> str:
    """根据 user_id (UUID) 查询真实姓名。

    Args:
        user_id: 用户 UUID（可能是 UUID 或空字符串）

    Returns:
        用户真实姓名，查询失败返回空字符串
    """
    if not user_id:
        return ""
    try:
        from ..repositories.user_repo import UserRepository
        u = UserRepository.get(user_id)
        if u:
            return getattr(u, "real_name", "") or getattr(u, "username", "") or ""
    except Exception as e:
        logger.debug("resolve_user_name failed for %s: %s", user_id, e)
    return ""
```

#### 3.2 `emily-core/emily_core/application/meeting_app.py`

```python
# 修改前（~line 38-40）
self._journal.append(
    name=cmd.creator_id or "用户",
    summary=f"录入会议纪要：{meeting.title}（{meeting.meeting_no}）",
)

# 修改后
from ._user_utils import resolve_user_name
user_name = resolve_user_name(cmd.creator_id) or "用户"
self._journal.append(
    name=user_name,
    summary=f"录入会议纪要：{meeting.title}（{meeting.meeting_no}）",
)
```

#### 3.3 `emily-core/emily_core/application/task_app.py`

```python
# 修改前（~line 39-44）
self._journal.append(name=cmd.creator_id or "用户", summary=summary)

# 修改后
from ._user_utils import resolve_user_name
user_name = resolve_user_name(cmd.creator_id) or "用户"
self._journal.append(name=user_name, summary=summary)
```

#### 3.4 `emily-core/emily_core/application/file_app.py`

```python
# 修改前（~line 39-42）
self._journal.append(
    name=cmd.uploaded_by or "用户",
    summary=f"归档文件：{f.filename}（{f.file_no}）",
)

# 修改后
from ._user_utils import resolve_user_name
user_name = resolve_user_name(cmd.uploaded_by) or "用户"
self._journal.append(
    name=user_name,
    summary=f"归档文件：{f.filename}（{f.file_no}）",
)
```

---

## 阶段二（P0 架构级修复，需集成测试）

### 修复 4：TC-J03 — 确认链路 LLM 意图增强

**问题**：用户回复"确认"后，SessionAgent 走 LLM 意图路由 → fallback，不识别为事件确认。

**方案**（用户选定"LLM 意图增强"）：
1. 在 `_recognize_intent()` 的 system prompt 中注入当前 session 的 pending 状态
2. 当 LLM 返回 `sop_id: "SYS-confirm"` 或类似特殊意图时，SessionAgent 不创建 WorkItem 走 Pipeline，而是直接调用 `EventApplication.handle_confirmation()`

**涉及文件**：

#### 4.1 `emily-data/prompts/session.md`

在路由规则部分新增一条规则（在"## 路由规则"节末尾追加）：

```markdown
6. 上下文中的确认响应：
   如果系统提示"存在待确认的录入项"，且用户消息表达了确认/取消/修改意图
   （如"确认"、"好的"、"可以"、"取消"、"不对"、"改一下"等），
   必须输出 sop_id="SYS-confirm"，confidence="high"，不要走其他 SOP 路由。

   具体意图映射：
   - 用户确认 → action="confirm"
   - 用户取消 → action="cancel"
   - 用户要求修改 → action="modify"

   输出格式：
   {"sop_id": "SYS-confirm", "confidence": "high", "reasoning": "用户确认待确认项",
    "is_compound": false, "sub_tasks": [], "fallback": false,
    "data": {"action": "confirm"}}
```

#### 4.2 `emily-core/emily_core/session/session_agent.py`

(1) 修改 `_recognize_intent()` 方法——在 system prompt 中注入 pending 状态：

```python
async def _recognize_intent(self, message: "StandardMessage") -> dict:
    # ... 现有代码 ...

    # ═══ TC-J03: 注入 pending 确认状态到 LLM 上下文 ═══
    pending_context = ""
    pending_event = self._get_pending_event()
    if pending_event:
        pending_context = (
            f"\n\n⚠️ 当前存在待确认的录入项：\n"
            f"  编号：{pending_event.event_no}\n"
            f"  内容：{pending_event.title}\n"
            f"  状态：等待用户确认\n"
            f"  如果用户表达了确认/取消/修改意图，请路由到 SYS-confirm。\n"
        )

    prompt = _SESSION_SYSTEM_PROMPT.format(
        sop_catalog=sop_catalog,
        current_datetime=_beijing_now_str(),
    )
    prompt += pending_context  # ← 追加 pending 状态
    # ...
```

(2) 新增 `_get_pending_event()` 方法：

```python
def _get_pending_event(self):
    """查找当前 conversation 中最近的 pending 事件。"""
    try:
        from ..repositories.event_repo import EventRepository
        repo = EventRepository()
        return repo.find_pending_by_message_conversation(self.conversation_id)
    except Exception:
        return None
```

(3) 修改 `_split_into_workitems()` —— 在 LLM 返回 SYS-confirm 时直接处理：

```python
async def _split_into_workitems(self, message: "StandardMessage") -> list[WorkItem]:
    # ... 现有 intent 识别代码 ...

    sop_id = intent.get("sop_id")
    # ...

    # ═══ TC-J03: SYS-confirm 特殊处理 ═══
    if sop_id == "SYS-confirm":
        action = (intent.get("data") or {}).get("action", "confirm")
        pending_event = self._get_pending_event()
        if pending_event:
            # 将确认结果预先写入 WorkItem 的 baggage
            wi = WorkItem(
                session_id=self.conversation_id,
                user_input=content,
                user_id=self.context.user_id,
                sop_id="SYS-confirm",
                intent_type="sop",
                priority=0,  # 最高优先级
            )
            wi._confirm_action = action
            wi._confirm_event_id = pending_event.id
            return [wi]
        else:
            # 没有 pending 事件，降级为普通对话
            return [WorkItem(
                session_id=self.conversation_id,
                user_input=content,
                user_id=self.context.user_id,
                sop_id=None,
                intent_type="fallback",
                priority=1,
            )]

    # ... 其余逻辑不变 ...
```

(4) 新增 `_handle_confirm()` 方法：

```python
async def _handle_confirm(self, wi) -> str | None:
    """处理 SYS-confirm WorkItem（不经过 Pipeline BUS）。"""
    action = getattr(wi, "_confirm_action", "confirm")
    event_id = getattr(wi, "_confirm_event_id", "")

    if not event_id:
        return None

    try:
        from ..application.event_app import EventApplication
        from ..services.event_service import EventService
        from ..repositories.event_repo import EventRepository

        # 查找 pending 事件并确认
        event_repo = EventRepository()
        event = event_repo.get_by_id(event_id)

        if event is None or event.status != "pending":
            return "没有待确认的事件，请重新录入。"

        event_service = EventService()
        event_app = EventApplication(event_service)

        # 注入 journal（从 core 获取，或新建）
        # 此处需要 journal 引用，通过 SessionContext 或全局获取

        result = event_app.handle_confirmation(event_id=event_id, action=action)
        return result.reply
    except Exception as e:
        logger.error("SYS-confirm handling failed: %s", e)
        return f"确认处理失败：{e}"
```

(5) 修改 `handle()` 方法——在创建 WorkItem 后、入队前检查 SYS-confirm：

```python
async def handle(self, message: "StandardMessage") -> ReplyMessage | None:
    # ... 现有代码 ① ② ...

    work_items = await self._split_into_workitems(message)
    if not work_items:
        return self._reply(message, "...")

    # ═══ TC-J03: SYS-confirm 直接处理，不走 Pipeline BUS ═══
    for wi in work_items:
        if wi.sop_id == "SYS-confirm":
            confirm_reply = await self._handle_confirm(wi)
            if confirm_reply:
                return self._reply(message, confirm_reply)
            return self._reply(message, "确认处理完成。")

    # ... 其余入队逻辑不变 ...
```

> **注意**：`_handle_confirm` 中需要 `EventJournal` 引用。当前 EventJournal 在 EmilyCore 中持有。有两个方式获取：
> - A) 在 SessionFactory._build_context() 时把 journal 注入到 SessionContext
> - B) 在 `_handle_confirm` 中创建一个临时的 EventJournal（路径一致即可）
>
> **推荐 B**——journal 路径由 _init_m8c_services 确定，`_handle_confirm` 中直接 `EventJournal(path=...)` 即可，因为 journal.append 是追加写，幂等。

#### 4.3 `emily-core/emily_core/session/session_context.py`

确认 `SessionContext` 中是否需要新增 `event_journal_path` 字段。如果需要精确路径，可在 `SessionFactory._build_context()` 时注入。

---

### 修复 5：TC-A01 — FileApplication 整合物理文件存储

**问题**：`FileApplication.handle_file()` 只写 DB 元数据，不调用 `FileStorageService` 做下载和磁盘写入。

**方案**：在 `FileApplication` 中注入 `FileStorageService`，当消息包含附件 URL 时，调用 `store_attachment()` 下载文件到 `attachments/`。

**涉及文件**：

#### 5.1 `emily-core/emily_core/application/file_app.py`

(1) 注入 FileStorageService：

```python
class FileApplication:
    def __init__(self, file_service: FileService, storage_service=None):
        self.file_service = file_service
        self.storage_service = storage_service  # M13: FileStorageService
        self._journal = None

    def set_journal(self, journal) -> None:
        self._journal = journal

    def set_storage_service(self, storage_service) -> None:
        """注入文件物理存储服务（M13）。"""
        self.storage_service = storage_service
```

(2) 修改 `handle_file()` 增加物理存储逻辑：

```python
async def handle_file(
    self, route_result: RouteResult, user_id: str, message_id: str,
    attachment_url: str = "", attachment_type: int = 0,
    source_filename: str = "",
) -> HandlerResult:
    data = route_result.data or {}

    # 1. 先写 DB 元数据
    cmd = FileCommand(
        # ... 现有逻辑不变 ...
    )
    f = self.file_service.create_file_record(cmd)

    # 2. M13: 如果有附件 URL，下载并存到物理磁盘
    local_path = ""
    if attachment_url and self.storage_service:
        try:
            store_result = self.storage_service.store_attachment(
                message_id=message_id,
                attachment_url=attachment_url,
                attachment_type=attachment_type or 3,  # 默认 file 类型
                source_filename=source_filename or data.get("filename", ""),
            )
            if store_result:
                local_path = store_result.get("local_path", "")
                logger.info("File physically stored: %s", local_path)
        except Exception as e:
            logger.warning("Physical file storage failed (non-blocking): %s", e)

    # 3. journal
    if self._journal is not None:
        from ._user_utils import resolve_user_name
        user_name = resolve_user_name(cmd.uploaded_by) or "用户"
        summary = f"归档文件：{f.filename}（{f.file_no}）"
        if local_path:
            summary += f" → {local_path}"
        self._journal.append(name=user_name, summary=summary)

    reply = FileService.format_reply(f)
    if local_path:
        reply += f"\n文件已保存到本地存储。"
    return HandlerResult(success=True, object_type="file", object_id=f.id, reply=reply)
```

#### 5.2 `emily-core/emily_core/tools/file_tool.py`

修改 `handle_record_file()` —— 从 params 中提取附件 URL 信息并传递给 FileApplication：

```python
async def handle_record_file(params, file_app, user_id="", message_id="", ...):
    # ... 现有代码 ...

    attachment_url = params.get("_attachment_url", "")
    attachment_type = params.get("_attachment_type", 0)
    source_filename = data.get("filename", "")

    result = await file_app.handle_file(
        route_result, user_id, message_id,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        source_filename=source_filename,
    )
    # ...
```

#### 5.3 `emily-core/emily_core/__init__.py`

在 `_init_phase_c_deps()` 中创建 FileApplication 时注入 FileStorageService：

```python
# 修改前（~line 223）
self._file_app = FileApplication(FileService())

# 修改后
from .services.file_storage_service import FileStorageService
storage_root = self.config.storage_root or ""
if not storage_root:
    # Docker 容器内默认路径
    container_path = Path("/app/attachments")
    if container_path.parent.exists():
        storage_root = str(container_path)
    else:
        storage_root = str(
            Path(__file__).resolve().parents[2] / "emily-data" / "attachments"
        )
file_storage = FileStorageService(storage_root=storage_root)
self._file_app = FileApplication(FileService(), storage_service=file_storage)
```

---

## 阶段三（P2 优化，可选）

### 修复 6：TC-J01 — SOP 触发关键词优化

**问题**："帮我记个待办"被路由到 SOP-002（事件记录）而非 SOP-003（任务管理）。

**涉及文件**：

#### 6.1 `emily-data/sops/SOP-002-REC-event_record.md`

在 deny_conditions 中添加任务/待办相关的否定条件：

```markdown
| deny_conditions | 包含"待办"、"提醒"、"截止日期"、"deadline"、"分配"、"安排某人" |
```

#### 6.2 `emily-data/sops/SOP-003-REC-task_management.md`

在 trigger_keywords 中强化任务标识：

```markdown
| trigger_keywords | 待办、任务、提醒、截止、分配、安排、跟进、事项、负责 |
```

---

### 修复 7：清理重复注册 + 路径默认值一致性

#### 7.1 `emily-core/emily_core/__init__.py`

删除 `_init_m8c_services()` 中的 write_user_memory 重复注册段落（~576-586 行），因为这些工具已在 `register_all()` → `_register_business()` / `_register_project()` 中统一注册。

#### 7.2 路径默认值文档注释修正（非功能改动）

在以下文件顶部注释中标注实际使用的路径为 `emily-data/` 前缀：

| 文件 | 当前注释 | 修正为 |
|------|---------|--------|
| `event_journal.py:7` | `path="tem_log/项目日志.md"` | `path="emily-data/journal/项目日志.md"` |
| `user_memory_service.py:51` | `默认路径：项目根目录下的 memory/` | `默认路径：emily-data/user_memory/` |
| `pending_issues.py:14` | `DEFAULT_ISSUES_PATH = "tem_log/待解决问题.md"` | `DEFAULT_ISSUES_PATH = "emily-data/notebooks/待解决问题.md"` |

---

## 验证计划

修复完成后，按以下顺序验证：

### 阶段一验证

```bash
# 1. 清除 pycache + 重启
docker exec emily-core find /app -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
sleep 5

# 2. TC-M01: 测试 user_memory 写入
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "以后每周五上午提醒我检查项目进度" \
  --sender "彭工" --sender-id "peng_gong"
# 验证: ls emily-data/user_memory/ 应有 彭工-长期记忆.md

# 3. TC-N01: 测试 notebooks 写入（直接调用 manage_pending_issues add）
# 可用 emy-test 或直接 API 调用

# 4. TC-J02: 测试 journal 姓名（创建会议）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "记录会议：项目周例会，参会人彭工张工" \
  --sender "彭工" --sender-id "peng_gong"
# 验证: cat emily-data/journal/项目日志.md | grep 彭工
```

### 阶段二验证

```bash
# 5. TC-J03: 事件确认两轮对话
# 第1轮
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "记录事件：5号楼外墙完成验收" \
  --sender "彭工" --sender-id "peng_gong"

# 第2轮（确认）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "确认" \
  --sender "彭工" --sender-id "peng_gong"
# 验证: events 表 status=confirmed, journal 有确认录入行

# 6. TC-A01: 文件附件存储（需要真实 IM 附件 URL）
# 或用 TC-A01 后备方案（容器内直接测试 FileStorageService）
```

---

## 风险与注意事项

1. **阶段二 TC-J03 风险最高**：LLM 意图增强依赖 LLM 正确识别 SYS-confirm。建议先在 session.md 的 prompt 中充分描述确认场景，并对 `confidence < "medium"` 的情况做关键词回退（"确认"/"好的"/"取消" 直接匹配）。

2. **user_memory 运行时查 DB**：每次 `write_user_memory` 调用多一次 DB 查询，性能影响可忽略（User 表很小）。

3. **FileStorageService 环境差异**：Docker 容器内路径 `/app/attachments/` 需要确保目录存在且有写权限，开发环境回退到 `emily-data/attachments/`。

4. **向后兼容**：TC-J03 的 SYS-confirm 不影响现有 SOP 路由逻辑，当 LLM 返回其他 sop_id 时流程完全不变。
