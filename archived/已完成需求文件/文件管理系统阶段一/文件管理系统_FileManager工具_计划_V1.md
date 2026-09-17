# 文件管理系统 — FileManager 工具实施计划

> **定位**：B（收敛服务层为 FileManager，对外仍拆细粒度工具）
> **计划版本**：v1.0
> **目标**：补全文件对象生命周期闭环（发文件 / 自动下载 / 关联版本 / 权限统一），收敛 4 个割裂服务为统一 FileManager，保持原子工具原则不破坏现有 LLM 工具契约。
> **基线梳理**：见 [需求/文件管理系统.md](../需求/文件管理系统.md)（本计划同源）

---

## Context

**为什么做这个改动**：当前 Emily 文件管理有四个割裂痛点（详见基线梳理）：

1. **能力不闭环**：能录（`record_file`）能查（`query_files`/`search_files`）能改分类（`update_file_category`），但不能发、不能删、不能关联、不能版本化。用户拿到 `file_no` 后无路可走。
2. **权限双标**：`query_files` 走 `project_ids` 过滤（无密级），`search_files` 走 `session_accessible_files`（含密级）——同项目低权限用户可能查到机密文件。
3. **职责割裂**：`FileService`（元数据）/ `FileStorageService`（物理）/ `FileApplication`（编排）/ `SessionAccessibleFileRepo`（可见性）四份职责，无统一 facade，权限/可见性逻辑分散。
4. **附件链路断裂**：入站附件不自动下载（[message_repo.py:62-82](../emily-core/emily_core/repositories/message_repo.py#L62-L82) 只存 URL 到 `messages.attachments` JSON），依赖 LLM 规划出 `record_file` 才下载。IM 平台 URL 有时效，用户随手发图不归档则丢失。

**本计划范围（定位 B）**：
- 含：M1 FileManager 服务收敛 + 权限统一；M2 send_file 工具；M3 附件自动下载；M4 文件关联与版本
- 不含：用户面下载 API（`/api/v1/files/{id}/download`，后续独立任务）、NAS/OSS 远程存储迁移（`bucket`/`object_key` 字段保留但不启用）、文件内容解析（OCR/解析调度已有 `parse_document` 工具，不在本计划）

**预期收益**：
- 文件对象生命周期闭环：上传→归档→关联→版本→分发→删除
- 权限单出口：所有文件查询统一走 `session_accessible_files`，密级过滤一致
- 附件不丢失：入站即落盘，URL 过期不影响
- Emily 可主动发文件：用户说"把昨天的图发我"可执行

---

## 探查发现的关键约束（影响实施）

1. **出站通道完全就绪**：`OutboundEventBus.publish("file_send", data)`（[outbound_bus.py:39-46](../emily-core/emily_core/outbound_bus.py#L39-L46)）→ SSE `/api/v1/events/outbound`（[api/sse/outbound.py:30](../emily-core/api/sse/outbound.py#L30)）→ 插件 `_handle_file_send`（[sse_listener.py:128-137](../data/plugins/emily_agent/adapters/sse_listener.py#L128-L137)）→ `outbound_sender.send_files(file_paths)`（[outbound_sender.py:59](../data/plugins/emily_agent/adapters/astrbot/outbound_sender.py#L59)）。**send_file 工具只需 publish 事件，无需改通道**。
2. **ReplyMessage 已有 file_paths 字段**（[reply.py:27](../emily-core/emily_core/adapters/standard/reply.py#L27)）：`file_paths: list[dict] = [{"path": "/abs/path", "name": "图纸.dwg"}]`。同步回复路径也可携带文件，不必走 SSE。
3. **handle_message 已有 on_send_file 回调参数**（[__init__.py:903](../emily-core/emily_core/__init__.py#L903)）：当前未接线，M2 需启用或在工具 handler 内直接 publish outbound_bus。
4. **FileStorageService.get_local_path 已实现**（[file_storage_service.py:268-277](../emily-core/emily_core/services/file_storage_service.py#L268-L277)）：按 `file_no` 解析本地绝对路径。M2 直接复用。
5. **session_accessible_files 三层授权模型已就绪**（[session_accessible_file_repo.py:23-131](../emily-core/emily_core/repositories/session_accessible_file_repo.py#L23-L131)）：`sync_for_user` 按 `project_ids + confidentiality <= info_level + authorized_node_ids + explicit` 四维度同步。M1 权限统一只需让 `query_files` 改走此表。
6. **附件 URL 注入机制已就绪**（[workitem_agent.py:532-544](../emily-core/emily_core/workitem/workitem_agent.py#L532-L544)）：`_attachment_url`/`_attachment_type` 已注入 `tool_params`。M3 自动下载后，`record_file` 复用已下载文件（避免二次下载），此注入机制不变。
7. **File 模型版本/关联字段已就位**（[models.py:354-376](../emily-core/emily_core/infrastructure/database/models.py#L354-L376)）：`version`/`is_latest`/`parent_file_id`/`change_log`/`source_module_id`/`source_module_type`/`confidentiality` 全部存在。M4 只需加工具，无需改表。
8. **M14 约束**（CLAUDE.md 约束 5）：新工具不暴露为 LLM function-calling，走 `BusinessFlowTool.handler(params)` 直调模式。注册到 `BusinessFlowToolRegistry` + `tool_registry` 表 + `REGISTERED_TOOLS` 集合 + `TOOL_META_MAP` + `TOOL_SCHEMA_MAP`（[tools_consistency.py:33-106](../emily-core/emily_core/infrastructure/tools_consistency.py#L33-L106)）五处同步。
9. **`__pycache__` 不自动刷新**（CLAUDE.md 踩坑）：每次代码变更后必须 `docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按 M1→M2→M3→M4 顺序执行，逐模块验证，验证不通过不进入下一个模块。

## 硬约束（违反即失败）

1. **不改现有 4 个工具的外部 schema**：`record_file`/`query_files`/`update_file_category`/`search_files` 的 params schema 不变，只改内部实现（走 FileManager）
2. **业务内核独立**（CLAUDE.md 约束 1）：`emily_core` 不 import 任何 `astrbot.*` 包
3. **分层不可跳**（CLAUDE.md 约束 2）：`API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB`。工具 handler → Application → FileManager(Service) → Repository
4. **Sync repo + asyncio.to_thread**（CLAUDE.md 约束 6）：Repository 全 sync，FileManager 若被 async 调用需 `asyncio.to_thread()` 包裹
5. **M14 约束**：新工具走 `BusinessFlowTool.handler` 直调，不暴露 function-calling
6. **五处注册同步**：新工具必须同步更新 `tools/registry.py` + `tools_consistency.py`（`REGISTERED_TOOLS`/`TOOL_META_MAP`/`TOOL_SCHEMA_MAP`）+ `seed_tool_registry.sql` + Skill YAML（如有）
7. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
8. **改完代码必须清 `__pycache__`**：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`
9. **权限 fail-closed**：`query_files` 改走 `session_accessible_files` 后，若用户无可见文件则返回空列表（不回退到 project_ids）
10. **附件自动下载不阻塞主线**：M3 下载必须异步，失败不阻断消息处理，仅记日志

## 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|------|-------------|
| 服务收敛 Facade | [query_service.py](../emily-core/emily_core/services/query_service.py) | 聚合多 Repo + 业务逻辑 + `asyncio.to_thread` 包裹 sync 调用 |
| 工具 handler 注册 | [tools/registry.py:225-235](../emily-core/emily_core/tools/registry.py#L225-L235) | `_reg_biz(reg, name, desc, partial(handler, app=core._file_app), category, perm_flag)` |
| 工具 schema 常量 | [file_tool.py:21-62](../emily-core/emily_core/tools/file_tool.py#L21-L62) | 模块级 `_XXX_SCHEMA` + `_XXX_DESCRIPTION` 常量 + `handle_xxx(params, **kwargs)` |
| 出站事件 publish | [node_event_bus.py:135](../emily-core/emily_core/node_event_bus.py#L135) | `self._outbound_bus.publish("file_send", {conversation_id, file_paths, caption})` |
| 附件存储路径 | [file_storage_service.py:33-45](../emily-core/emily_core/services/file_storage_service.py#L33-L45) | `data/files/{platform}/{YYYY-MM}/FIL-YYYYMMDD-NNNN.ext` |
| 可见性同步 | [session_accessible_file_repo.py:23-131](../emily-core/emily_core/repositories/session_accessible_file_repo.py#L23-L131) | `sync_for_user(user_id, project_ids, info_level, authorized_node_ids)` |
| 五处注册同步 | [tools_consistency.py:33-106](../emily-core/emily_core/infrastructure/tools_consistency.py#L33-L106) | `REGISTERED_TOOLS` set + `TOOL_META_MAP` + `TOOL_SCHEMA_MAP` |
| 消息入库附件处理 | [message_repo.py:62-82](../emily-core/emily_core/repositories/message_repo.py#L62-L82) | `create_from_standard` 序列化 attachments JSON |

## 模块依赖图

```
M1(FileManager 收敛 + 权限统一) ──→ M2(send_file 工具)
        │                                  │
        ↓                                  ↓
M3(附件自动下载) ──────────────→ M4(关联与版本)
```

- **M1 独立奠基**：收敛服务 + 修权限双标。其他模块都依赖 FileManager 存在
- **M2 依赖 M1**：send_file 工具调 FileManager.resolve_local_path + 权限校验
- **M3 依赖 M1**：自动下载后调 FileManager.register_attachment 入库
- **M4 依赖 M1**：link/version/delete 工具调 FileManager 对应方法
- **M3 与 M4 可并行**：互不依赖，但建议 M3 先（数据保全优先于能力扩展）

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/services/file_manager.py` | **新增** | FileManager 统一服务（聚合 FileService + FileStorageService + SessionAccessibleFileRepo 能力） |
| M1 | `emily-core/emily_core/application/file_app.py` | 修改 | `FileApplication` 改注入 FileManager，`handle_list_by_category` 走 session_accessible_files |
| M1 | `emily-core/emily_core/tools/file_tool.py` | 修改 | `handle_query_files` 内部改走 FileManager（权限统一） |
| M1 | `emily-core/emily_core/__init__.py` | 修改 | EmilyCore 初始化 FileManager，注入 FileApplication |
| M2 | `emily-core/emily_core/tools/file_tool.py` | 修改 | 新增 `handle_send_file` + `_SEND_FILE_SCHEMA` |
| M2 | `emily-core/emily_core/tools/registry.py` | 修改 | 注册 `send_file` |
| M2 | `emily-core/emily_core/infrastructure/tools_consistency.py` | 修改 | 三处同步 `send_file` |
| M2 | `emily-core/emily_core/infrastructure/database/scripts/seed_tool_registry.sql` | 修改 | 种子 `send_file` |
| M2 | `emily-data/sops/` 下相关 Skill YAML | 修改 | 若有发文件场景的 Skill，补 send_file 步骤 |
| M3 | `emily-core/emily_core/services/attachment_downloader.py` | **新增** | 异步附件下载服务（去重 + 落盘 + 入库） |
| M3 | `emily-core/emily_core/session/session_agent.py` | 修改 | 消息处理触发异步下载（不阻塞） |
| M3 | `emily-core/emily_core/application/file_app.py` | 修改 | `handle_file` 复用已下载文件（避免二次下载） |
| M4 | `emily-core/emily_core/tools/file_tool.py` | 修改 | 新增 `handle_link_file` + `handle_new_file_version` + `handle_delete_file` + `handle_list_file_versions` |
| M4 | `emily-core/emily_core/services/file_manager.py` | 修改 | 补 link/version/delete/list_versions 方法 |
| M4 | `emily-core/emily_core/tools/registry.py` + `tools_consistency.py` + `seed_tool_registry.sql` | 修改 | 同步注册 4 个新工具 |
| 全模块 | `docs/业务模块与运转全景.md` + `docs/接口协议与调用约定.md` + `docs/代码文件目录.md` | 修改 | 同步文档（CLAUDE.md 维护约定 1） |

---

## M1：FileManager 服务收敛 + 权限统一

### 目标

把 `FileService`（元数据）+ `FileStorageService`（物理）+ `SessionAccessibleFileRepo`（可见性）的对外能力收敛到 `FileManager` 统一服务，作为 `FileApplication` 和工具 handler 的唯一入口。同时修复 `query_files` 权限双标——统一走 `session_accessible_files`。

### 新建 `services/file_manager.py`

```python
"""FileManager —— 文件对象统一服务（定位 B 收敛层）。

聚合 FileService（元数据）+ FileStorageService（物理存储）+ 
SessionAccessibleFileRepo（可见性）的对外能力，作为 Application 层唯一入口。

不替代底层 Repo，只在 Service 层做 Facade：权限校验 + 编排 + 统一出口。
"""

class FileManager:
    def __init__(self, file_service, storage_service, accessible_repo):
        self._file_svc = file_service          # FileService
        self._storage = storage_service        # FileStorageService
        self._accessible = accessible_repo     # SessionAccessibleFileRepo

    # ── 检索（权限统一出口）──
    def query_visible_files(
        self, user_id: str, *,
        file_category: str | None = None, keyword: str = "",
        limit: int = 50,
    ) -> list[File]:
        """按分类/关键词查询用户可见文件。
        
        统一走 session_accessible_files：project_ids + confidentiality + 节点授权 + 显式。
        取代 FileRepository.query_files / query_by_category 的 project_ids 单层过滤。
        """
        # 1. 取 user 的 session_accessible_files file_id 集合
        # 2. 在 files 表按 file_id 集合 + is_deleted + 可选 file_category 过滤
        # 3. keyword 在 filename/file_type ILIKE
        ...

    def search_files(self, user_id: str, keyword: str, top_k: int = 5) -> list[dict]:
        """自然语言搜索（委托 SessionAccessibleFileRepo.search）。"""
        return self._accessible.search(user_id, keyword, top_k)

    def get_visible_summary(self, user_id: str) -> dict:
        """可见文件摘要（委托 SessionAccessibleFileRepo.get_file_summary）。"""
        return self._accessible.get_file_summary(user_id)

    # ── 物理存储 ──
    async def store_attachment(self, message_id, url, attachment_type, **kw) -> dict | None:
        """下载附件并落盘（委托 FileStorageService.store_attachment_async）。"""
        return await self._storage.store_attachment_async(message_id, url, attachment_type, **kw)

    def resolve_local_path(self, file_no: str) -> str | None:
        """file_no → 本地绝对路径（M2 send_file 用）。"""
        return self._storage.get_local_path(file_no)

    # ── 归档 ──
    def create_record(self, cmd: FileCommand) -> File:
        """元数据录入（委托 FileService.create_file_record）。"""
        return self._file_svc.create_file_record(cmd)

    def update_category(self, file_id, category, operator_id="") -> File | None:
        return self._file_svc.update_file_category(file_id, category, operator_id)

    # ── 权限校验（M2/M4 复用）──
    def can_access(self, user_id: str, file_id: str) -> bool:
        """校验用户是否可访问某文件（在 session_accessible_files 中）。"""
        # 委托 SessionAccessibleFileRepo 查询
        ...

    # ── M4 关联/版本/删除（M4 阶段补实现）──
    def link_to_module(self, file_id, module_id, module_type, operator_id="") -> File | None: ...
    def create_version(self, parent_file_id, new_file_id, version_label, operator_id="") -> File | None: ...
    def soft_delete(self, file_id, operator_id="") -> bool: ...
    def list_versions(self, parent_file_id) -> list[File]: ...
```

### 改 `application/file_app.py`

- `FileApplication.__init__` 增加 `file_manager: FileManager` 注入参数（保留 `file_service`/`storage_service` 向后兼容，但优先用 `file_manager`）
- `handle_list_by_category` 改调 `file_manager.query_visible_files(user_id, file_category, keyword, limit)`，**传入 user_id 走权限统一出口**
- `handle_file` 内部 `storage_service.store_attachment` 改调 `file_manager.store_attachment`（统一入口）

### 改 `tools/file_tool.py`

- `handle_query_files` 增加 `_user_id` 参数提取（已在 tool_params 注入，见 [workitem_agent.py:527](../emily-core/emily_core/workitem_agent.py#L527)），传给 `file_app.handle_list_by_category`
- `handle_list_by_category` 内部走 `file_manager.query_visible_files(user_id=...)`

### 改 `__init__.py` EmilyCore 初始化

- 在 `_init_services` 阶段新建 `FileManager(file_service, storage_service, SessionAccessibleFileRepo)`
- 注入到 `FileApplication.set_file_manager(file_manager)`

### M1 验收检测

```bash
# 1. 启动后清 pycache
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 工具一致性检查（V13 验证 tool_registry 同步）
uv run python scripts/check_tools_consistency.py

# 3. 权限统一验证（关键）
# 3a. 查一个 level=1 的访客用户，确认 query_files 只返回 session_accessible_files 中的文件
docker exec emily-postgres psql -U emily -d emily -c "
SELECT u.id, u.username, u.permission_level FROM users u WHERE u.status='active' ORDER BY u.permission_level LIMIT 3;"

# 3b. 对比 query_files 结果与 session_accessible_files 表
docker exec emily-postgres psql -U emily -d emily -c "
SELECT COUNT(*) FROM session_accessible_files WHERE user_id='<低权限用户UUID>';"

# 3c. emy-test 实战：低权限用户调 query_files，确认不返回机密文件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "查一下项目有哪些文件" --sender "<低权限用户名>"

# 4. 现有 4 个工具回归（确保未破坏）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我查 CONTRACT 类文件" --sender "<正常用户名>"
```

**通过标准**：
- 工具一致性检查 0 fatal
- 低权限用户 query_files 结果数 ≤ session_accessible_files 中该用户的记录数
- 现有 record_file/query_files/update_file_category/search_files 功能正常

---

## M2：send_file 工具（Emily 主动发文件）

### 目标

补 M13 遗留的 `send_file` 工具。LLM 调用 → 权限校验 → 解析本地路径 → publish `file_send` 出站事件 → 插件发送到 IM。

### 在 `tools/file_tool.py` 新增

```python
_SEND_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001），必填",
        },
        "caption": {
            "type": "string",
            "description": "附带的文字说明（可选）",
        },
    },
    "required": ["file_no"],
}

_SEND_FILE_DESCRIPTION = (
    "向当前会话用户发送一个 Emily 已有的文件。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号（必须是用户有权访问的文件）\n"
    "可选字段：\n"
    "  caption — 附带文字说明"
)

async def handle_send_file(
    params: dict,
    file_manager,           # FileManager 注入
    outbound_bus,           # OutboundEventBus 注入
    user_id: str = "",
    message_id: str = "",
    conversation_id: str = "",
    **kwargs,
) -> dict:
    """处理 send_file 工具调用。"""
    file_no = params.get("file_no", "")
    caption = params.get("caption", "")
    
    # 1. 解析 file_no → file_id
    file_record = FileManager.get_by_file_no(file_no)  # 委托 FileRepository
    if file_record is None:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}
    
    # 2. 权限校验（fail-closed）
    if not file_manager.can_access(user_id, file_record.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}
    
    # 3. 解析本地路径
    local_path = file_manager.resolve_local_path(file_no)
    if not local_path:
        return {"success": False, "reply": f"文件 {file_no} 未在本地存储", "error_code": "file_not_stored"}
    
    # 4. publish file_send 出站事件
    outbound_bus.publish("file_send", {
        "conversation_id": conversation_id,
        "file_paths": [{"path": local_path, "name": file_record.filename}],
        "caption": caption,
    })
    
    return {
        "success": True,
        "reply": f"✅ 已发送文件：{file_record.filename}（{file_no}）",
    }
```

### 在 `tools/registry.py` 注册

```python
# 在 _register_business 中追加
_buc += _reg_biz(reg, "send_file", "向用户发送已有文件",
                 partial(_h("file_tool", "handle_send_file"),
                         file_manager=core._file_manager,
                         outbound_bus=core.outbound_bus),
                 "business", "all")
```

### 五处同步

1. `tools/registry.py` `_register_business`：注册 `send_file`（见上）
2. `tools_consistency.py` `REGISTERED_TOOLS`：加 `"send_file"`
3. `tools_consistency.py` `TOOL_META_MAP`：`"send_file": ("向用户发送已有文件", "business", "all")`
4. `tools_consistency.py` `TOOL_SCHEMA_MAP`：`"send_file": ("emily_core.tools.file_tool", "_SEND_FILE_SCHEMA")`
5. `seed_tool_registry.sql`：`('send_file', '{}', '向用户发送已有文件', 'business', 'all', '', true, ...)`

### Skill YAML 补充

检查 `emily-data/sops/` 下是否有"发文件"场景的 Skill（如 SOP-005-QRY 查询后发文件）。若有，补 `send_file` 步骤；若无，跳过（LLM 可在规划阶段自主调用）。

### M2 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 工具一致性检查
uv run python scripts/check_tools_consistency.py
# 确认 send_file 在 REGISTERED_TOOLS + tool_registry 表 + 内存注册三方一致

# 3. 实战测试：先归档一个文件，再让 Emily 发回来
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把刚才那份合同文件发给我" --sender "<用户名>"

# 4. 权限验证：让低权限用户请求无权访问的文件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把 FIL-XXXXXXXX-XXXX 文件发给我" --sender "<低权限用户名>"
# 预期：reply "您无权访问该文件"

# 5. SSE 通道验证：查看日志确认 file_send 事件已 publish
docker logs --tail 50 emily-core 2>&1 | grep -i "file_send"
```

**通过标准**：
- 工具一致性 0 fatal，send_file 三方同步
- 正常用户请求可达文件 → IM 收到文件
- 低权限用户请求机密文件 → permission_denied
- file_send 事件日志可见

---

## M3：附件自动下载

### 目标

消息入站时自动下载附件到本地，不依赖 `record_file` 触发。避免 IM URL 过期丢失。去重（SHA256）+ 异步 + 不阻塞主线。

### 新建 `services/attachment_downloader.py`

```python
"""AttachmentDownloader —— 入站附件异步下载服务。

消息入库后异步触发：遍历 attachments → 去重（SHA256）→ 下载 → 
写 message_attachments 表（回填 local_path + file_id）。

不阻塞 Session 主线；失败仅记日志。
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

logger = logging.getLogger("emily.service.attachment_downloader")


class AttachmentDownloader:
    def __init__(self, file_manager, chat_archive_repo):
        self._fm = file_manager
        self._archive_repo = chat_archive_repo

    async def download_for_message(self, message_id: str, attachments: list[dict]) -> None:
        """异步下载一条消息的所有附件。
        
        每个附件独立 try/except，单个失败不影响其他。
        """
        if not attachments:
            return
        for att in attachments:
            try:
                await self._download_one(message_id, att)
            except Exception as e:
                logger.warning("Attachment download failed msg=%s url=%s: %s",
                               message_id, str(att.get("url", ""))[:80], e)

    async def _download_one(self, message_id: str, att: dict) -> None:
        url = att.get("url", "")
        if not url:
            return
        att_type = att.get("type", 0)
        source_filename = att.get("file_name", "") or att.get("summary", "")
        
        # 1. 委托 FileManager.store_attachment 下载落盘 + 写 files/message_attachments
        result = await self._fm.store_attachment(
            message_id=message_id,
            attachment_url=url,
            attachment_type=att_type,
            source_filename=source_filename,
        )
        if result:
            logger.info("Attachment auto-downloaded: msg=%s file_no=%s",
                        message_id, result.get("file_no"))

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()
```

### 去重策略（在 `FileStorageService._finalize_store` 中补）

下载前先按 URL + file_size 查 `message_attachments` 表，若已存在则跳过（同一消息重复入站去重）。后续可扩展为 SHA256 全局去重（需先下载算 hash，成本较高，V1 先做 URL 级去重）。

### 改 `session/session_agent.py`

在 `handle_message` 消息入库后（`message_repo.create_from_standard` 之后），异步触发下载：

```python
# 在 message 持久化后、Session 主流程继续前
if msg.attachments and self._attachment_downloader:
    asyncio.create_task(
        self._attachment_downloader.download_for_message(
            message_id=db_msg.id, attachments=msg.attachments,
        )
    )
    logger.debug("Scheduled attachment download: msg=%s, %d item(s)",
                 db_msg.id, len(msg.attachments))
```

**关键**：`asyncio.create_task` 不 await，不阻塞主线。下载失败不影响消息处理。

### 改 `application/file_app.py` `handle_file`

`record_file` 工具调用时，先检查 `message_attachments` 表是否已有该消息附件的本地路径。若有则复用（避免二次下载），仅在缺失时调 `store_attachment`。

### 改 `__init__.py` EmilyCore 初始化

- 新建 `AttachmentDownloader(file_manager, chat_archive_repo)`
- 注入到 `SessionAgent`

### M3 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 实战测试：发一张图片，不给"归档"指令
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "[图片消息]" --sender "<用户名>"
# 注意：emy-test 当前可能不支持模拟图片附件，需确认 cli.py 是否支持 --attachment 参数
# 若不支持，需先用真实 QQ 发图，或扩展 emy-test 支持 attachments

# 3. 验证附件已落盘
docker exec emily-postgres psql -U emily -d emily -c "
SELECT ma.id, ma.attachment_type, ma.file_url, ma.local_path, ma.file_id 
FROM message_attachments ma 
ORDER BY ma.created_at DESC LIMIT 5;"

# 4. 验证本地文件存在
docker exec emily-core ls -la /app/data/files/napcat/$(date +%Y-%m)/ | head -20

# 5. 验证不阻塞主线：消息响应时间正常
docker logs --tail 50 emily-core 2>&1 | grep -i "Scheduled attachment download"

# 6. 回归：record_file 仍正常工作（且复用已下载文件，不二次下载）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "归档刚才那张图" --sender "<用户名>"
# 预期：日志显示复用已下载文件，不触发二次下载
```

**通过标准**：
- 消息入库后 message_attachments 表有记录，local_path 非空
- 本地磁盘 data/files/napcat/YYYY-MM/ 有对应文件
- 消息响应时间无明显增加（异步不阻塞）
- record_file 复用已下载文件，不二次下载

---

## M4：文件关联与版本

### 目标

补全文件对象生命周期：关联（到节点/事件/会议）+ 版本迭代 + 软删除 + 版本列表。让 file_no 之后有路可走。

### 在 `services/file_manager.py` 补方法

```python
def link_to_module(
    self, file_id: str, module_id: str, module_type: str, operator_id: str = "",
) -> File | None:
    """关联文件到业务对象（节点/事件/会议等）。
    
    module_type: NODE_STARTUP_DOC / NODE_WORKLOAD_DOC / NODE_DELIVERABLE_DOC / 
                 NODE_ATTACHMENT / EVENT_DOC / MEETING_DOC
    """
    with get_session() as session:
        f = session.query(File).filter(File.id == file_id, File.is_deleted == False).first()
        if f is None:
            return None
        f.source_module_id = module_id
        f.source_module_type = module_type
        session.commit()
        return f

def create_version(
    self, parent_file_no: str, new_file_id: str, version_label: str,
    operator_id: str = "",
) -> File | None:
    """创建新版本：旧版本 is_latest=False，新版本 parent_file_id 指向旧版本。"""
    ...

def soft_delete(self, file_id: str, operator_id: str = "") -> bool:
    """软删除：is_deleted=True。"""
    ...

def list_versions(self, file_no: str) -> list[File]:
    """列出某文件的所有版本（按 version 排序）。"""
    ...
```

### 在 `tools/file_tool.py` 新增 4 个 handler

| 工具 | 动作 | 权限 |
|------|------|------|
| `link_file` | 关联文件到节点/事件/会议 | write |
| `new_file_version` | 创建新版本（旧版本归档） | write |
| `delete_file` | 软删除文件 | write |
| `list_file_versions` | 列出文件所有版本 | all |

每个工具 schema 遵循 [file_tool.py:21-62](../emily-core/emily_core/tools/file_tool.py#L21-L62) 模式。

### 五处同步（每个新工具都要）

`tools/registry.py` + `tools_consistency.py`（3 处）+ `seed_tool_registry.sql`。

### M4 验收检测

```bash
# 1. 清 pycache + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 工具一致性检查
uv run python scripts/check_tools_consistency.py

# 3. 关联测试
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把文件 FIL-XXXXXXXX-XXXX 关联到节点 SG-001" --sender "<用户名>"
docker exec emily-postgres psql -U emily -d emily -c "
SELECT file_no, source_module_id, source_module_type FROM files WHERE file_no='FIL-XXXXXXXX-XXXX';"

# 4. 版本测试
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "把文件 FIL-XXXXXXXX-XXXX 升版到 V2.0" --sender "<用户名>"
# 验证旧版本 is_latest=False，新版本 parent_file_id 指向旧版本

# 5. 删除测试
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "删除文件 FIL-XXXXXXXX-XXXX" --sender "<用户名>"
# 验证 is_deleted=True，但 query_files 不再返回

# 6. 版本列表测试
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "列出文件 FIL-XXXXXXXX-XXXX 的所有版本" --sender "<用户名>"
```

**通过标准**：
- 工具一致性 0 fatal，4 个新工具三方同步
- 关联：source_module_id/type 正确写入
- 版本：旧版本 is_latest=False，新版本 parent_file_id 正确
- 删除：is_deleted=True，检索不再返回
- 版本列表：返回所有版本按 version 排序

---

## 端到端验收（全模块完成后）

```bash
# 完整生命周期测试
# 1. 用户发图 → 自动下载（M3）
# 2. Emily 归档 → record_file（M1，复用已下载）
# 3. 用户查询 → query_files（M1，权限统一）
# 4. 用户说"发给我" → send_file（M2）
# 5. 关联到节点 → link_file（M4）
# 6. 升版 → new_file_version（M4）
# 7. 删除 → delete_file（M4）

# 文档同步检查
git diff --name-only | grep -E "docs/业务模块与运转全景|docs/接口协议与调用约定|docs/代码文件目录"
# 确认三份文档已更新（CLAUDE.md 维护约定 1）
```

---

## 风险与回退

| 风险 | 影响 | 回退方案 |
|------|------|----------|
| M1 权限统一后低权限用户可见文件数变化 | 中 | 回退 `handle_query_files` 走旧 `project_ids` 路径（git revert M1 file_tool.py 改动） |
| M3 异步下载耗尽磁盘 | 高 | 限制单消息附件数 + 单文件大小（>50MB 跳过）+ 监控磁盘水位 |
| M3 重复下载（去重失效） | 中 | URL 级去重已防同一消息重复；全局 SHA256 去重作为 V2 |
| M2 SSE 无订阅者时事件丢弃 | 中 | 插件未连接时 file_send 事件丢失。回退：同步路径走 ReplyMessage.file_paths（[reply.py:27](../emily-core/emily_core/adapters/standard/reply.py#L27)） |
| M4 版本链断裂（parent_file_id 错指） | 中 | create_version 内校验 parent 存在 + 事务原子操作 |

---

## 文档同步清单（CLAUDE.md 维护约定 1）

全模块完成后必须更新：
1. [docs/业务模块与运转全景.md](../docs/业务模块与运转全景.md) — 文件管理模块清单 + 新工具
2. [docs/接口协议与调用约定.md](../docs/接口协议与调用约定.md) — send_file/file_send 事件 + 新工具 schema
3. [docs/代码文件目录.md](../docs/代码文件目录.md) — file_manager.py / attachment_downloader.py 新文件
4. [docs/数据库设计.md](../docs/数据库设计.md) — files 表 version/source_module 字段启用说明（若 M4 启用）
5. [docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md) — 附件自动下载/权限统一相关踩坑（如有）
6. [需求/文件管理系统.md](../需求/文件管理系统.md) — 补全现状梳理（基线文档）
