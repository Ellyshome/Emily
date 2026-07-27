# 群聊多用户权限越界与 Session 生命周期重构 — 实施计划 V1

> **创建日期**：2026-07-26
> **作者**：Claude（与用户讨论确认）
> **状态**：待执行
> **执行方**：后续 AI 开发会话
> **需求来源**：[需求/群聊多用户权限越界问题报告.md](../需求/群聊多用户权限越界问题报告.md)
>
> **本计划已经过完整可行性讨论与技术预研（astrbot 能力验证、emily-core 现状代码核查），所有技术前置无阻塞项。执行方应先通读"架构总览"与"模块依赖"，再按序实施。**

---

## 0. 背景与目标

### 0.1 问题

Emily 群聊场景存在两类根因相关的缺陷：

1. **权限越界**：群聊 `conversation_id = group_id`，群内所有人共享一个 Session。SessionPool 命中已有 Session 时忽略 `user_id`，导致 SessionContext 锁定为"第一个 @emily 的用户"的权限快照。后续低权限用户 @emily 时，AuthHook 用第一个用户的 level/sop_allow 鉴权——**权限逃逸 + 审计归属错误**。

2. **Session 生命周期错配**：当前 TTL 超时制（默认 10 分钟无消息归档）以"群数据流间隙"为计时基准，但用户诉求是"问题段边界"。群内连续 @emily 会让 Session 永不归档，导致 `message_history`/`focus_lock`/`confirm_queue` 跨话题污染。

### 0.2 目标

- **权限正确**：每条消息用**当前操作者**的权限快照鉴权，操作归属当前操作者
- **生命周期正确**：Session 跟随"一个完整问题处理段"，任务完成 + 短空闲超时即归档
- **上下文完整**：跨 Session 的群聊历史通过 DB 回溯获取，不依赖 Session 复用
- **群聊感知**：Emily 知道自己在哪些群，默认静默收集群消息，冷启动通知管理员

### 0.3 关键决策（已与用户确认）

| # | 决策 | 说明 |
|---|---|---|
| D1 | 权限修复方案 C1 | SessionContext 保持创建者语义；每条消息额外携带"当前操作者权限快照"给 AuthHook/WorkItem |
| D2 | 归档按群名存 | 依赖 group_name 提取（当前缺失，需补） |
| D3 | 谁发起谁确认 | confirm_queue 带 user_id 校验，他人不可替代 |
| D4 | 群消息跨 Session 共享 | message_history 管当前任务段；跨段历史走 DB 回溯 |
| D5 | 默认 monitor 模式 | 群消息静默落库（takeover=true, should_reply=false） |
| D6 | Session 任务段生命周期 | 任务完成 + 短空闲超时（3 分钟）+ 硬上限（30 分钟）；**不做显式终止判断** |
| D7 | DB 回溯 | 首批 10 条 + LLM 判断充分性 + 继续回溯，上限 50 轮；文件给元信息 + handle |
| D8 | 群长期记忆 | 本次一并实现，数据基础已具备 |
| D9 | 群清单 + 管理员通知 | 插件调 `get_group_list` 同步 core；跟随 bootstrap 启动邮件通知流 |

---

## 1. 架构总览

### 1.1 改动前后对比

```
═══ 改动前 ═══
群消息 → 插件(ALL) → core.handle_message
  ├─ takeover=collaborate: 未@emily 不落库直接返回
  └─ takeover=@emily: 落库 → SessionPool.route(conv_id=group_id)
      └─ 命中已有 Session → user_id 被丢
          └─ SessionContext(第一个用户的权限) ← 锁定
              └─ WorkItem(user_id=ctx.user_id) ← 第一个用户
                  └─ BusContext(user_id=wi.user_id, session_ctx=第一个用户)
                      └─ AuthHook: level/sop_allow 全是第一个用户 ← 越权

═══ 改动后 ═══
群消息 → 插件(ALL) → core.handle_message
  ├─ takeover=monitor: 未@emily 静默落库（should_reply=false）→ 返回
  └─ takeover=@emily: 落库 + group_name 缓存
      └─ SessionPool.route(conv_id=group_id, user_id=当前用户)
          └─ 命中已有 Session → 用 message.sender_id 取当前操作者权限快照
              ├─ WorkItem(user_id=message.sender_id) ← 当前操作者
              └─ BusContext(user_id=wi.user_id, actor_snapshot=当前操作者权限, session_ctx=创建者)
                  └─ AuthHook: 优先读 actor_snapshot ← 当前操作者权限 ✓
                      └─ Session 结束: 任务完成 + 3min空闲 / 30min硬上限 → 按群名归档
```

### 1.2 LLM Context 拼装（改动后）

```
[系统 prompt（Session 级缓存，M4 预渲染）]
[DB 回溯的群聊前置上下文]        ← @emily 拉起时从 DB 取最近群聊记录（模块④）
[群级长期记忆摘要]               ← 新 Session 拉起时注入（模块⑤）
[message_history 当前任务段]     ← Session 内多轮对话（模块③约束跨度）
[当前 user message]
```

### 1.3 核心概念定义

| 概念 | 定义 |
|---|---|
| **Session 创建者** | 拉起该 Session 的第一个用户。SessionContext.user_id 锁定为此人，用于归档元数据、群级记忆沉淀归属 |
| **当前操作者** | 当前 @emily 的用户。每条消息独立判定，用于 WorkItem 归属、AuthHook 鉴权、审计日志 |
| **任务段** | 一次 @emily 拉起到任务完成（+短空闲超时）的完整处理过程。一个 Session = 一个任务段 |
| **actor_snapshot** | 当前操作者的权限快照（level/sop_allow/authorized_node_ids 等），每条消息独立获取，挂在 BusContext 上 |

---

## 2. 模块依赖与执行顺序

```
① group_name 提取 + monitor 切换
    │  （基础设施，无依赖）
    ▼
② 权限越界修复 C1 ──────────────┐
    │  （独立，可与③并行）        │
    ▼                            │
③ Session 任务段生命周期          │
    │  （依赖②的 WorkItem user_id）│
    ▼                            ▼
④ DB 回溯上下文 ←──── 依赖③的 Session 生命周期
    │
    ▼
⑤ 群长期记忆（依赖④的回溯机制 + ③的归档时机）
    │
    ▼
⑥ 群清单 + 管理员通知（独立，可并行于③④⑤）
```

**推荐执行序**：① → ② → ③ → ④ → ⑤ → ⑥。其中 ⑥ 可与 ③④⑤ 并行。

**每个模块独立验收**，前序模块验收通过后再进入下一模块。

---

## 模块 ① group_name 提取 + monitor 模式切换

### 目标

- 补齐 inbound_adapter 对 group_name 的提取（当前始终为 None，[inbound_adapter.py:139-153](../data/plugins/emily_agent/adapters/astrbot/inbound_adapter.py#L139-L153)）
- 将默认 takeover_mode 从 collaborate 切到 monitor，使群消息静默落库
- conversations 表缓存 group_name（写入 title 字段）

### 交付物清单

| 文件 | 改动 |
|---|---|
| `data/plugins/emily_agent/adapters/astrbot/inbound_adapter.py` | 补 `get_group()` 调用提取 group_name |
| `emily-core/emily_core/config.py` | `takeover_mode` 默认值改 `"monitor"` |
| `emily-core/emily_core/services/message_service.py` | 落库时同步 group_name 到 conversations.title |
| `emily-core/emily_core/repositories/message_repo.py` | `create_from_standard` 增加 group_name 写入逻辑（upsert conversations.title） |

### 代码骨架

**1. inbound_adapter.py — 提取 group_name**

```python
# to_standard_message 方法内，构造 standard 之前补充：
group_name = ""
if conversation_type == "group" and group_id:
    try:
        group_obj = await event.get_group()  # async，调 OneBot get_group_info
        if group_obj is not None:
            group_name = getattr(group_obj, "group_name", "") or ""
    except Exception as e:
        logger.debug("get_group failed (non-blocking): %s", e)
        group_name = ""

# 注意：to_standard_message 当前是 staticmethod 同步方法，需改为 async
# 调用方 main.py:81 `msg = self.inbound.to_standard_message(event)` 改为 `await`
```

**性能优化**：每条群消息都调 `get_group()`（2 次 OneBot API）过重。优化策略：
- 插件层维护 `group_name_cache: dict[group_id, str]`（进程内），首次见 group_id 时调一次 `get_group()`，后续命中缓存
- 缓存 miss 才调 API；缓存写回后该群后续消息直接用缓存
- `event.get_group()` 内部已含群信息，无需额外调 member_list（可传 `no_fetch=True` 之类参数，若 astrbot 支持；不支持则接受 2 次 API 开销，仅首次）

**2. config.py — 默认 monitor**

```python
# 查找 takeover_mode 字段定义，默认值 "collaborate" → "monitor"
takeover_mode: str = "monitor"
```

**3. message_repo.py — conversations.title 缓存 group_name**

```python
# create_from_standard 内，解析/创建 conversation 时：
def _resolve_or_create_conversation(msg, decision):
    # 已有逻辑：按 (im_platform, conversation_id) 查 conversations
    # 新增：若 msg.group_name 非空且 conversation.title 为空，则更新 title
    conv = session.query(Conversation).filter(
        Conversation.im_platform == msg.platform,
        Conversation.conversation_id == msg.conversation_id,
    ).first()
    if conv is None:
        conv = Conversation(
            im_platform=msg.platform,
            conversation_type=msg.conversation_type,
            conversation_id=msg.conversation_id,
            group_id=msg.group_id,
            title=msg.group_name or "",   # 群名写入 title
            takeover_mode=decision.mode,
        )
        session.add(conv)
    elif msg.group_name and not conv.title:
        conv.title = msg.group_name      # 补写群名
    session.commit()
    return conv.id
```

### 验收检测

1. **group_name 落库验证**：
   ```powershell
   # 群内发一条 @emily 消息后
   docker exec emily-postgres psql -U emily -d emily -c "SELECT conversation_id, group_id, title FROM conversations WHERE conversation_type='group' ORDER BY updated_at DESC LIMIT 5;"
   # 预期：title 字段为真实群名（非空）
   ```

2. **monitor 模式静默收集验证**：
   ```powershell
   # 群内发一条不 @emily 的消息后
   docker exec emily-postgres psql -U emily -d emily -c "SELECT direction, content, status FROM messages WHERE conversation_id='<群conv_id>' ORDER BY created_at DESC LIMIT 5;"
   # 预期：消息已落库（direction=user_to_agent, status=received），但 emily 不回复
   docker logs --tail 30 emily-core 2>&1 | Select-String "Silent collect"
   # 预期：日志含 "Silent collect: msg persisted conv=..."
   ```

3. **群名缓存命中验证**：同群连续发 3 条消息，`docker logs emily-core` 中 `get_group` 调用次数应为 1（缓存命中后不再调）。

### 依赖与注意事项

- **`to_standard_message` 改 async 的影响面**：调用方 `main.py:81` 需改 `await`。全项目搜索 `to_standard_message` 确认无其他同步调用方。
- **monitor 模式的副作用**：所有群消息（含非 @emily）都落库 + 附件下载，DB 写入量和附件下载量显著增加。确认 `emily-data/logs/` 和附件存储空间预算。
- **observe 模式兼容**：若运维需要临时静默，仍可切 observe。config 切换路径不变。

---

## 模块 ② 权限越界修复（方案 C1）

### 目标

- 每条消息处理时，用 `message.sender_id` 获取当前操作者的权限快照
- WorkItem 归属、BusContext.user_id、AuthHook 鉴权、审计日志全部用当前操作者
- SessionContext 保持创建者语义不变（归档、群级记忆归属仍按创建者）

### 交付物清单

| 文件 | 改动 |
|---|---|
| `emily-core/emily_core/session/session_agent.py` | `handle()` 入口获取 actor_snapshot；4 处 WorkItem user_id 改 message.sender_id |
| `emily-core/emily_core/workitem/pipeline/context.py` | BusContext 新增 `_actor_snapshot` 字段 + `get_actor_snapshot()` |
| `emily-core/emily_core/workitem/scheduler.py` | `_run_one` 注入 actor_snapshot 到 BusContext |
| `emily-core/emily_core/workitem/pipeline/hook.py` | AuthHook 优先读 actor_snapshot；AuditHook user_id 改读 actor_snapshot |
| `emily-core/emily_core/session/session_data_fetcher.py` | 提取"按 user_id 取权限快照"的轻量函数（复用现有 fetch 逻辑） |

### 代码骨架

**1. session_data_fetcher.py — 提取权限快照函数**

```python
# 新增轻量函数，只取权限相关字段（不走完整 SessionDataFetcher.fetch）
def fetch_actor_snapshot(user_id: str, core=None) -> dict:
    """获取当前操作者的权限快照（轻量，仅权限字段）。
    
    复用 SessionDataFetcher 的权限采集逻辑，但跳过记忆/项目上下文/能力清单等
    Session 级字段，减少查询开销。
    
    Returns:
        dict: {level, sop_allow, authorized_node_ids, db_perms, info_level,
               supervisor_id, granted_codes, denied_codes, company_id,
               company_type, is_management_unit, department, scopes, ...}
    """
    # 复用 SessionDataFetcher._fetch_permission_snapshot(user_id) 等内部方法
    # 若现有 SessionDataFetcher 无此拆分，则从 fetch() 结果中提取 session_snapshot 子集
    data = SessionDataFetcher.fetch(user_id=user_id, conversation_id="", core=core)
    snapshot = data.get("session_snapshot", {})
    # 只保留权限字段
    _PERM_KEYS = {
        "level", "is_management_unit", "company_id", "company_type", "company_name",
        "department", "project_ids", "partner_ids", "scopes", "sop_allow",
        "db_perms", "info_level", "supervisor_id", "granted_codes", "denied_codes",
        "authorized_node_ids", "permission_version", "permissions_loaded_at",
    }
    return {k: snapshot.get(k) for k in _PERM_KEYS}
```

**2. session_agent.py — handle 入口取 actor_snapshot**

```python
async def handle(self, message: "StandardMessage", db_message_id: str = "") -> ReplyMessage | None:
    # ── 新增：取当前操作者权限快照（每条消息独立）──
    actor_user_id = message.sender_id  # 已在 handle_message 解析为 user UUID
    # 兜底：若 sender_id 不是 UUID（未绑定），回退 SessionContext.user_id
    if not actor_user_id or not _looks_like_uuid(actor_user_id):
        actor_user_id = self.context.user_id
    try:
        from .session_data_fetcher import fetch_actor_snapshot
        actor_snapshot = await asyncio.to_thread(
            fetch_actor_snapshot, actor_user_id, self._core
        )
        actor_snapshot["user_id"] = actor_user_id
    except Exception as e:
        logger.warning("fetch_actor_snapshot failed, fallback to session ctx: %s", e)
        actor_snapshot = None  # 回退：AuthHook 用 session_ctx
    
    # 暂存到实例，供 _split_into_workitems 和 scheduler 使用
    self._current_actor = actor_snapshot
    
    # ... 原有 _append_archive_turn_start / _handle_impl 逻辑 ...
```

**3. session_agent.py — WorkItem user_id 改 message.sender_id**

4 处改动（[session_agent.py:404,416,426,440,451](../emily-core/emily_core/session/session_agent.py#L404)）：
```python
# 统一改法（5 处 WorkItem 构造）：
actor_uid = getattr(self, "_current_actor", {}).get("user_id") or self.context.user_id
wi = WorkItem(
    session_id=self.conversation_id,
    user_input=content,
    user_id=actor_uid,   # 原 self.context.user_id
    ...
)
```

**4. context.py — BusContext 新增 actor_snapshot**

```python
@dataclass
class BusContext:
    # ... 原有字段 ...
    _session_context: Optional["SessionContext"] = None
    
    # ── 新增：当前操作者权限快照（每条消息独立）──
    _actor_snapshot: Optional[dict] = None
    
    def get_actor_snapshot(self) -> Optional[dict]:
        """获取当前操作者权限快照。
        
        AuthHook 优先读此字段鉴权；为 None 时回退 get_session_context()。
        """
        return self._actor_snapshot
    
    def get_auth_context(self) -> tuple[str, dict | None]:
        """统一鉴权数据源：返回 (user_id, perm_snapshot)。
        
        优先用 actor_snapshot；缺失时回退 session_context 的字段。
        """
        if self._actor_snapshot is not None:
            return self._actor_snapshot.get("user_id", ""), self._actor_snapshot
        # 回退：私聊场景 actor_snapshot = session_context 权限
        if self._session_context is not None:
            return self._session_context.user_id, _snapshot_from_session_ctx(self._session_context)
        return "", None
```

**5. scheduler.py — 注入 actor_snapshot**

```python
# _run_one 方法内，构造 BusContext 时（[scheduler.py:104-111](../emily-core/emily_core/workitem/scheduler.py#L104)）：
# 从 SessionAgent 暂存的 _current_actor 取（需经 message 传递或 scheduler 持有引用）
# 推荐方式：scheduler 在 run_all_with_message 时接收 actor_snapshot 参数

async def run_all_with_message(self, message, db_message_id="", actor_snapshot=None):
    self._current_actor = actor_snapshot  # 暂存
    # ... 原有循环 ...

async def _run_one(self, wi, message=None, db_message_id=""):
    context = BusContext(
        work_item=wi,
        message=message,
        user_id=wi.user_id,
        is_admin=wi.is_admin,
        db_message_id=db_message_id,
        _session_context=self._session_context,
        _actor_snapshot=getattr(self, "_current_actor", None),  # 新增
    )
```

**6. hook.py — AuthHook 改读 actor_snapshot**

```python
async def execute(self, context: "PipelineContext") -> HookResult:
    user_id, perm = context.get_auth_context()  # 统一入口
    if not user_id:
        # ... 原有无 user_id 处理 ...
    
    # 管理员检查：用 perm["level"]
    if self.resource_type == "system" and self.action == "execute":
        from ...permission.level import is_admin as _is_admin
        if not _is_admin(perm.get("level", 1)):
            result = HookResult.block("仅管理员（L5+）可执行系统级操作")
            await _log_auth_block(user_id, self.resource_type, result.message)
            return result
    
    # SOP 权限检查：用 perm["sop_allow"]
    intent = context.intent
    sop_id = getattr(intent, "sop_id", None) if intent else None
    if sop_id and perm is not None:
        sop_allow = perm.get("sop_allow", [])
        if sop_id not in sop_allow and "all" not in sop_allow:
            reason = f"无权访问 {sop_id}"
            supervisor = perm.get("supervisor_id", "")
            if supervisor:
                reason += f"，可联系主管 {supervisor} 申请权限"
            result = HookResult.block(reason)
            await _log_auth_block(user_id, sop_id, reason)
            return result
    
    return HookResult.allow()
```

**AuditHook 同步改**：[hook.py:204](../emily-core/emily_core/workitem/pipeline/hook.py#L204) `user_id = context.user_id` 改为 `user_id, _ = context.get_auth_context()`，确保审计日志归属当前操作者。

### 验收检测

1. **越权拦截验证**（核心）：
   ```powershell
   # 查 users 表取两个不同权限用户
   docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status='active' ORDER BY permission_level DESC LIMIT 5;"
   
   # 用 emy-test 模拟：L6 用户 A 在群内 @emily 创建事件
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：测试越权" --sender "管理员A名" --group-id "test_group_001"
   
   # 用 L2 用户 B 在同群 @emily 尝试删除该事件
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "删除刚才那个事件" --sender "施工员B名" --group-id "test_group_001"
   
   # 预期：B 的请求被 AuthHook 拦截，回复"无权访问..."或"仅管理员..."
   docker logs --tail 50 emily-core 2>&1 | Select-String "AuthHook.*blocking"
   # 预期：日志含 blocking，且 user_id=B（非 A）
   ```

2. **审计归属验证**：
   ```powershell
   docker exec emily-postgres psql -U emily -d emily -c "SELECT user_id, sop_id, block_reason, created_at FROM hook_execution_logs WHERE block_reason != '' ORDER BY created_at DESC LIMIT 5;"
   # 预期：block 记录的 user_id 是当前操作者（B），非 Session 创建者（A）
   ```

3. **私聊无回归验证**：
   ```powershell
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "查询我的事件" --sender "管理员A名"
   # 预期：私聊场景行为不变（actor_snapshot = session_context 权限）
   ```

### 依赖与注意事项

- **`_looks_like_uuid` 复用**：[__init__.py:940](../emily-core/emily_core/__init__.py#L940) `EmilyCore._looks_like_uuid` 已有，可提取为模块级函数或在 fetcher 内复用。
- **`message.sender_id` 在 handle_message 已解析为 user UUID**：[__init__.py:936-957](../emily-core/emily_core/__init__.py#L936-L957) 已完成 IM 绑定解析。但 `message.sender_id` 字段本身仍是 IM ID，需用解析后的 `user_id`。**注意**：route 时传的 `user_id` 参数已是 UUID，SessionAgent 应从 route 接收或从 message 取解析后字段。建议在 StandardMessage 增加 `resolved_user_id` 字段，或在 SessionPool.route 把 user_id 写入 message。
- **性能**：每条 @emily 消息多 1 次 `fetch_actor_snapshot`（DB 查询）。用户已确认可接受。
- **SessionContext.user_id 不变**：保持创建者语义。归档元数据、群级记忆沉淀仍按创建者。

---

## 模块 ③ Session 任务段生命周期

### 目标

- Session 跟随"问题处理段"生命周期，不再跟随群数据流
- 结束条件：任务完成 + 短空闲超时（3 分钟）/ 硬上限（30 分钟）
- confirm_queue 带 user_id 校验（谁发起谁确认）
- 归档按群名存

### 交付物清单

| 文件 | 改动 |
|---|---|
| `emily-core/emily_core/adapters/session/session_pool.py` | TTL 逻辑改为任务段制；sweeper 判定条件调整 |
| `emily-core/emily_core/adapters/session/session_config.py` | 新增 `task_idle_seconds=180` / `hard_limit_seconds=1800` |
| `emily-core/emily_core/session/session_agent.py` | 任务完成判定 + 归档触发；confirm_queue user_id 校验 |
| `emily-core/emily_core/session/confirm_queue.py` | 入队/出队带 user_id；跨用户取确认项校验 |
| `emily-core/emily_core/services/session_archive_writer.py` | 归档 md 文件名按群名 |

### 代码骨架

**1. session_config.py — 新增配置**

```python
@dataclass
class SessionConfig:
    # ... 原有字段 ...
    ttl_seconds: int = 600           # 保留（兼容，但语义改为"任务段空闲超时"）
    task_idle_seconds: int = 180     # 新增：任务完成后空闲超时（3 分钟）
    hard_limit_seconds: int = 1800   # 新增：Session 硬上限（30 分钟）
    sweep_interval_seconds: int = 60 # 调短（原 300），任务段更敏感
```

**2. session_pool.py — 任务段判定**

```python
def _is_task_complete(self, entry: _Entry) -> bool:
    """判定 Session 当前任务段是否完成。
    
    完成条件：
      - 无 EXECUTING/WAITING_CONFIRM 状态的 WorkItem
      - confirm_queue 为空（或仅含其他用户的待确认项，本用户无 pending）
      - 距最后活跃时间超过 task_idle_seconds
    """
    agent = entry.agent
    scheduler = agent.scheduler
    # 无活跃 WorkItem
    if scheduler.active_count > 0:
        return False
    # 无 pending confirm
    if not agent.confirm_queue.is_empty:
        return False
    # 空闲超时
    idle = time.time() - entry.last_active
    return idle > self._config.task_idle_seconds

def _is_hard_limit_exceeded(self, entry: _Entry) -> bool:
    """硬上限判定。"""
    # 需在 _Entry 记录 created_at
    return (time.time() - entry.created_at) > self._config.hard_limit_seconds

def sweep_expired(self) -> int:
    """扫描并归档过期 Session（任务段制）。"""
    now = time.time()
    expired = []
    for cid, entry in self._sessions.items():
        if self._is_hard_limit_exceeded(entry):
            expired.append((cid, "hard_limit"))
        elif self._is_task_complete(entry):
            expired.append((cid, "task_complete"))
    for cid, reason in expired:
        entry = self._sessions.pop(cid, None)
        if entry:
            try:
                asyncio.ensure_future(entry.agent.archive(reason=reason))
            except Exception as e:
                logger.warning("SessionPool sweep archive failed for %s: %s", cid, e)
    return len(expired)
```

**_Entry 增加 created_at**：
```python
class _Entry:
    __slots__ = ("agent", "last_active", "lock", "created_at")
    def __init__(self, agent):
        self.agent = agent
        self.last_active = time.time()
        self.created_at = time.time()
        self.lock = asyncio.Lock()
```

**3. confirm_queue.py — user_id 校验**

```python
@dataclass
class ConfirmEntry:
    workitem_id: str
    prompt: str
    priority: int
    user_id: str          # 新增：发起者 user_id
    created_at: float = field(default_factory=time.time)

class ConfirmQueue:
    def add(self, workitem_id, prompt, priority, user_id: str):
        self._queue.append(ConfirmEntry(workitem_id, prompt, priority, user_id))
        self._queue.sort(key=lambda e: e.priority)
    
    def pop_for_user(self, user_id: str) -> ConfirmEntry | None:
        """取出指定用户的待确认项（谁发起谁确认）。"""
        for i, entry in enumerate(self._queue):
            if entry.user_id == user_id:
                return self._queue.pop(i)
        return None
    
    def has_pending_for_user(self, user_id: str) -> bool:
        return any(e.user_id == user_id for e in self._queue)
```

**4. session_agent.py — 待确认项按用户过滤**

```python
def _collect_pending_confirms(self, done_workitems: list, actor_user_id: str) -> str | None:
    # 入队时带 actor_user_id
    needs_confirm = [wi for wi in done_workitems if wi.state == WorkItemState.WAITING_CONFIRM]
    for wi in needs_confirm:
        self.confirm_queue.add(
            workitem_id=wi.id,
            prompt=f"关于「{wi.user_input[:50]}...」需要你的确认",
            priority=wi.priority,
            user_id=actor_user_id,  # 谁发起谁确认
        )
    # 出队只取当前操作者的
    entry = self.confirm_queue.pop_for_user(actor_user_id)
    return entry.prompt if entry else None
```

**5. session_archive_writer.py — 按群名归档**

```python
def ensure_header(self, conversation_id, user_name, started_at, context):
    # 文件名原: {conversation_id}_{timestamp}.md
    # 改: 群聊用群名，私聊用 user_name
    group_name = context.get("group_name", "")
    if group_name:
        safe_name = _sanitize_filename(group_name)
        filename = f"{safe_name}_{started_at[:19].replace(':', '-')}.md"
    else:
        filename = f"{user_name}_{started_at[:19].replace(':', '-')}.md"
    # ... 原有路径拼接 ...
```

### 验收检测

1. **任务段结束验证**：
   ```powershell
   # @emily 完成一个任务后，3 分钟内不再 @emily
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "创建事件：任务段测试" --sender "用户A" --group-id "test_group_002"
   # 等待 3 分钟后
   docker logs --tail 100 emily-core 2>&1 | Select-String "SessionPool swept|task_complete"
   # 预期：Session 被 sweeper 归档，reason=task_complete
   ```

2. **连续 @emily 不归档验证**：
   ```powershell
   # 同群连续 @emily（不同任务），间隔 < 3 分钟
   # 预期：Session 保持活跃，不归档
   docker logs emily-core 2>&1 | Select-String "SessionPool hit"
   ```

3. **谁发起谁确认验证**：
   ```powershell
   # 用户 A @emily 创建事件（进 pending_confirm）
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "创建事件：确认测试" --sender "用户A" --group-id "test_group_003"
   # 用户 B @emily 尝试确认
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "确认" --sender "用户B" --group-id "test_group_003"
   # 预期：B 收到"无待确认项"或"该确认由 用户A 发起，无法替代"
   ```

4. **归档文件名验证**：
   ```powershell
   ls emily-data/archives/sessions/ | findstr "群名"
   # 预期：存在以群名命名的归档 md 文件
   ```

### 依赖与注意事项

- **依赖模块②**：WorkItem.user_id 必须先改为当前操作者，否则 confirm_queue 的 user_id 还是创建者。
- **硬上限的副作用**：长任务（如复杂 SOP 多步执行）可能被硬上限切断。30 分钟上限需观察实际任务时长，必要时调整。建议先观察 1 周。
- **sweeper 间隔调短**：从 300s → 60s，CPU 开销略增但可接受（仅扫内存 dict）。
- **Session 复用的语义变化**：改动后，同群连续 @emily 在 3 分钟内复用 Session（任务段内）；超过 3 分钟拉起新 Session。focus_lock / message_history 不跨 Session。

---

## 模块 ④ DB 回溯上下文

### 目标

- @emily 拉起 Session 时，从 DB 回溯最近群聊记录，作为 LLM 前置上下文
- 首批 10 条 + LLM 判断充分性 + 继续回溯，上限 50 轮
- 文件附件给元信息 + handle，不直接塞内容进 prompt

### 交付物清单

| 文件 | 改动 |
|---|---|
| `emily-core/emily_core/services/group_context_service.py` | **新建**：DB 回溯 + 充分性判断 + 注入 |
| `emily-core/emily_core/repositories/message_repo.py` | 新增 `list_recent_by_group` 方法（按 group_id 倒序取 N 条） |
| `emily-core/emily_core/session/session_agent.py` | `handle()` 入口调用回溯，注入 `_recognize_intent` 的 messages |
| `emily-core/emily_core/session/session_context.py` | `build_llm_messages` 支持注入 group_context 段 |

### 代码骨架

**1. message_repo.py — 按群回溯**

```python
@staticmethod
def list_recent_by_group(group_id: str, limit: int = 10, before_id: str = "") -> list[Message]:
    """按 group_id 倒序取最近 N 条群聊记录（含入站+出站）。
    
    Args:
        group_id: 群 ID
        limit: 取多少条
        before_id: 锚点 message id，取此 id 之前的记录（分页回溯用）
    
    Returns:
        list[Message]: 倒序排列（最新在前），调用方需反转为正序拼 prompt
    """
    with get_session() as session:
        q = session.query(Message).filter(Message.group_id == group_id)
        if before_id:
            anchor = session.query(Message).filter(Message.id == before_id).first()
            if anchor:
                q = q.filter(Message.created_at < anchor.created_at)
        q = q.order_by(Message.created_at.desc()).limit(limit)
        return q.all()
```

**2. group_context_service.py — 回溯 + 充分性判断**

```python
"""GroupContextService — @emily 拉起时从 DB 回溯群聊上下文。"""

class GroupContextService:
    INITIAL_BATCH = 10      # 首批回溯条数
    MAX_BATCHES = 5         # 最多回溯批次（10 * 5 = 50 条上限）
    
    def __init__(self, llm_client=None):
        self._llm = llm_client
    
    async def build_group_context(
        self,
        group_id: str,
        current_message_id: str,
        user_question: str,
    ) -> str:
        """回溯群聊记录，LLM 判断充分性，不足继续回溯。
        
        Returns:
            str: 拼好的群聊上下文文本（注入 LLM system prompt）
        """
        collected: list[Message] = []
        anchor_id = current_message_id
        all_messages: list[Message] = []
        
        for batch_idx in range(self.MAX_BATCHES):
            batch = MessageRepository.list_recent_by_group(
                group_id=group_id,
                limit=self.INITIAL_BATCH,
                before_id=anchor_id,
            )
            if not batch:
                break  # 没有更早的记录了
            all_messages = batch + all_messages  # 倒序取，前置拼接
            anchor_id = batch[-1].id  # 下一批从此批最旧条之前取
            
            # LLM 判断充分性
            if await self._is_sufficient(all_messages, user_question):
                break
        
        return self._format_for_prompt(all_messages)
    
    async def _is_sufficient(self, messages: list[Message], question: str) -> bool:
        """LLM 判断已有群聊记录是否足以回答用户问题。"""
        if not self._llm:
            return True  # 无 LLM 时默认充分（fail-open，不阻塞）
        if len(messages) >= 50:
            return True  # 硬上限
        
        prompt = self._build_sufficiency_prompt(messages, question)
        try:
            result = await self._llm.chat_messages(prompt, json_mode=True)
            return bool(result.get("data", {}).get("sufficient", True))
        except Exception:
            return True  # fail-open
    
    def _format_for_prompt(self, messages: list[Message]) -> str:
        """格式化群聊记录为 prompt 文本（含附件元信息 + handle）。"""
        if not messages:
            return "（无群聊历史上下文）"
        lines = ["## 群聊历史上下文（最近记录）"]
        for msg in messages:  # 正序
            sender = msg.sender_name or "未知"
            direction = "Emily" if msg.direction == "agent_to_user" else sender
            content = (msg.content or "")[:500]
            line = f"[{msg.created_at[:19]}] {direction}: {content}"
            # 附件元信息 + handle
            if msg.attachments_rel:
                for att in msg.attachments_rel:
                    line += f"\n  📎 附件: {att.file_name or '未命名'} (type={att.attachment_type}, size={att.file_size})"
                    line += f"     handle: msg://{msg.id}/att/{att.id}"
            lines.append(line)
        return "\n".join(lines)
```

**3. session_agent.py — handle 入口调用回溯**

```python
async def handle(self, message: "StandardMessage", db_message_id: str = ""):
    # ... actor_snapshot 获取（模块②）...
    
    # ── 新增：群聊时回溯 DB 上下文 ──
    self._group_context = ""
    if message.conversation_type == "group" and message.group_id:
        try:
            from ..services.group_context_service import GroupContextService
            svc = GroupContextService(llm_client=self._llm)
            self._group_context = await svc.build_group_context(
                group_id=message.group_id,
                current_message_id=db_message_id,
                user_question=message.content or "",
            )
        except Exception as e:
            logger.warning("group context build failed (non-blocking): %s", e)
            self._group_context = ""
    
    # ... 原有 _handle_impl ...
```

**4. session_agent.py — 意图识别注入 group_context**

```python
async def _recognize_intent(self, message):
    # ... 原有 full_messages 拼装 ...
    
    # 注入群聊上下文（在 message_history 之后、当前 user message 之前）
    if getattr(self, "_group_context", ""):
        full_messages.append({
            "role": "system",
            "content": self._group_context,
        })
    
    # ... 当前 user message ...
```

### 验收检测

1. **回溯注入验证**：
   ```powershell
   # 群内先发若干条非@emily消息（静默落库），再@emily提问
   uv run python .claude/skills/emy-test/cli.py --managed --llm --message "刚才大家讨论的那件事帮我记录一下" --sender "用户A" --group-id "test_group_004"
   # 预期：emily 能引用群内最近讨论内容
   docker logs emily-core 2>&1 | Select-String "group context"
   # 预期：日志显示回溯了 N 条记录
   ```

2. **文件 handle 验证**：
   ```powershell
   # 群内发一条带附件的消息，再@emily引用该附件
   # 预期：LLM context 含附件元信息 + handle（msg://...）
   # 预期：LLM 可通过 handle 调文件工具读取内容
   ```

3. **回溯上限验证**：
   ```powershell
   # 群内发 60+ 条消息，@emily 提问需要很早之前的信息
   # 预期：回溯到 50 条停止，LLM 回"信息不足"或基于已有信息回答
   docker logs emily-core 2>&1 | Select-String "batch_idx|MAX_BATCHES"
   ```

4. **LLM 不可用 fail-open 验证**：临时禁用 LLM，@emily 提问，预期：回溯首批 10 条直接注入，不阻塞。

### 依赖与注意事项

- **依赖模块③**：Session 任务段生命周期，否则跨任务段的 history 污染回溯结果。
- **附件 handle 格式**：`msg://{message_id}/att/{attachment_id}` 是建议格式，需与文件工具（read_file 类）的解析逻辑对齐。若现有文件工具用其他格式，统一之。
- **LLM 充分性判断的 prompt**：需设计清晰的判断 prompt（给出用户问题 + 已有记录摘要，让 LLM 输出 `{sufficient: bool, reason: str}`）。该 prompt 应缓存或精简，避免额外 token 开销。
- **性能**：每次 @emily 最多 5 次 DB 查询 + 5 次 LLM 充分性判断。用户已确认可接受。但充分性判断的 LLM 调用建议用 router_model（v4-flash，便宜快）。

---

## 模块 ⑤ 群长期记忆

### 目标

- Session 归档时，提取关键事实沉淀到群级长期记忆
- 新 Session 拉起时注入群级记忆摘要

### 交付物清单

| 文件 | 改动 |
|---|---|
| `emily-core/emily_core/infrastructure/database/models.py` | **新增** `GroupMemory` 表 |
| `emily-core/emily_core/repositories/group_memory_repo.py` | **新建**：群级记忆 CRUD |
| `emily-core/emily_core/services/group_memory_service.py` | **新建**：归档沉淀 + 拉起注入 |
| `emily-core/emily_core/session/session_context.py` | `persist_and_consolidate` 群聊分支调沉淀 |
| `emily-core/emily_core/session/session_agent.py` | `handle` 入口注入群级记忆 |

### 代码骨架

**1. models.py — GroupMemory 表**

```python
class GroupMemory(Base):
    """群级长期记忆 —— Session 归档时沉淀的关键事实。"""
    __tablename__ = "group_memories"
    id = Column(String, primary_key=True, default=_new_uuid)
    group_id = Column(String(200), nullable=False, index=True)
    group_name = Column(String(500))                    # 冗余，便于查询展示
    summary = Column(Text)                              # LLM 整合的群级记忆摘要
    key_facts = Column(String, default="[]")            # JSON 数组：关键事实列表
    last_session_id = Column(String)                    # 最后沉淀的 Session ID
    last_speaker_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    fact_count = Column(Integer, default=0)             # 累积事实数
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
```

**2. group_memory_repo.py — CRUD**

```python
class GroupMemoryRepository:
    @staticmethod
    def get_by_group(group_id: str) -> GroupMemory | None:
        with get_session() as session:
            return session.query(GroupMemory).filter(
                GroupMemory.group_id == group_id
            ).first()
    
    @staticmethod
    def upsert(group_id: str, group_name: str, summary: str,
               key_facts: list, session_id: str, speaker_user_id: str) -> GroupMemory:
        with get_session() as session:
            mem = session.query(GroupMemory).filter(
                GroupMemory.group_id == group_id
            ).first()
            if mem is None:
                mem = GroupMemory(group_id=group_id, group_name=group_name)
                session.add(mem)
            mem.summary = summary
            mem.key_facts = json.dumps(key_facts, ensure_ascii=False)
            mem.last_session_id = session_id
            mem.last_speaker_user_id = speaker_user_id
            mem.fact_count = len(key_facts)
            session.commit()
            return mem
```

**3. group_memory_service.py — 沉淀 + 注入**

```python
class GroupMemoryService:
    def __init__(self, llm_client=None):
        self._llm = llm_client
    
    async def consolidate_on_archive(
        self,
        group_id: str,
        group_name: str,
        session_id: str,
        speaker_user_id: str,
        message_history: list[dict],
        existing_memory: GroupMemory | None,
    ) -> None:
        """Session 归档时，整合本次对话到群级记忆。"""
        if not message_history or not self._llm:
            return
        
        existing_summary = existing_memory.summary if existing_memory else ""
        existing_facts = json.loads(existing_memory.key_facts) if existing_memory and existing_memory.key_facts else []
        
        current_conversation = _format_message_history(message_history)
        prompt = self._build_consolidate_prompt(
            existing_summary, existing_facts, current_conversation, group_name
        )
        try:
            result = await self._llm.chat_messages(prompt, json_mode=True)
            data = result.get("data", {})
            new_summary = data.get("summary", "")
            new_facts = data.get("key_facts", [])
            if new_summary:
                GroupMemoryRepository.upsert(
                    group_id=group_id,
                    group_name=group_name,
                    summary=new_summary,
                    key_facts=new_facts,
                    session_id=session_id,
                    speaker_user_id=speaker_user_id,
                )
        except Exception as e:
            logger.warning("group memory consolidate failed: %s", e)
    
    def build_injection(self, group_id: str) -> str:
        """新 Session 拉起时，生成群级记忆注入文本。"""
        mem = GroupMemoryRepository.get_by_group(group_id)
        if not mem or not mem.summary:
            return ""
        facts = json.loads(mem.key_facts) if mem.key_facts else []
        lines = [f"## 群级长期记忆（{mem.group_name or mem.group_id}）"]
        lines.append(f"摘要: {mem.summary}")
        if facts:
            lines.append("关键事实:")
            for f in facts[:20]:
                lines.append(f"  - {f}")
        return "\n".join(lines)
```

**4. session_context.py — persist_and_consolidate 群聊分支**

```python
async def _consolidate_conversation_summary(self, llm_client) -> None:
    # 原有私聊守卫保持（群聊跳过个人摘要）
    # ... 原有逻辑 ...
    
    # ── 新增：群聊走群级记忆沉淀 ──
    if conv and conv.conversation_type == "group":
        from ..services.group_memory_service import GroupMemoryService
        from ..repositories.group_memory_repo import GroupMemoryRepository
        existing = GroupMemoryRepository.get_by_group(conv.group_id)
        group_name = conv.title or ""
        svc = GroupMemoryService(llm_client=llm_client)
        await svc.consolidate_on_archive(
            group_id=conv.group_id,
            group_name=group_name,
            session_id=self.conversation_id,
            speaker_user_id=self.user_id,
            message_history=self.message_history,
            existing_memory=existing,
        )
        return
```

**5. session_agent.py — 拉起时注入群级记忆**

```python
async def handle(self, message, db_message_id=""):
    # ... group_context 回溯（模块④）...
    
    # ── 新增：群级长期记忆注入 ──
    self._group_memory_injection = ""
    if message.conversation_type == "group" and message.group_id:
        try:
            from ..services.group_memory_service import GroupMemoryService
            svc = GroupMemoryService()
            self._group_memory_injection = svc.build_injection(message.group_id)
        except Exception as e:
            logger.debug("group memory injection failed: %s", e)
    
    # ... 后续 ...
```

注入位置（`_recognize_intent` 的 full_messages）：
```python
# 顺序：system prompt → message_history → 群级记忆 → 群聊上下文 → 当前 user msg
if getattr(self, "_group_memory_injection", ""):
    full_messages.append({"role": "system", "content": self._group_memory_injection})
if getattr(self, "_group_context", ""):
    full_messages.append({"role": "system", "content": self._group_context})
```

### 验收检测

1. **沉淀验证**：
   ```powershell
   # 群内 @emily 完成一次任务段，等待归档（3 分钟）
   docker exec emily-postgres psql -U emily -d emily -c "SELECT group_id, group_name, summary, fact_count FROM group_memories ORDER BY updated_at DESC LIMIT 5;"
   # 预期：存在群级记忆记录，summary 非空，fact_count > 0
   ```

2. **注入验证**：
   ```powershell
   # 第二次 @emily（同群，新 Session）
   # 查 LLM trace 确认 prompt 含"群级长期记忆"段
   docker exec mitmproxy tail -3 /app/logs/llm_trace.jsonl | python -c "import sys,json; [print(json.loads(l).get('messages',[''])[0].get('content','')[:200]) for l in sys.stdin]"
   # 预期：system prompt 含群级记忆摘要
   ```

3. **跨 Session 记忆延续验证**：
   ```powershell
   # 第一次 @emily: "我叫张三，负责 A 项目"
   # 等 Session 归档
   # 第二次 @emily: "我是谁来着？"
   # 预期：emily 能从群级记忆回答"张三，负责 A 项目"
   ```

### 依赖与注意事项

- **依赖模块③**：归档时机由任务段生命周期决定。
- **依赖模块④**：群级记忆与群聊上下文回溯是互补关系——记忆是沉淀后的摘要（跨多次 Session），回溯是原始记录（当前 Session 拉起时）。
- **`_consolidate_conversation_summary` 已有群聊守卫**：[session_context.py:432-456](../emily-core/emily_core/session/session_context.py#L432-L456) 当前群聊跳过个人摘要。本次改动在该守卫内增加群级记忆沉淀分支。
- **新增表 GroupMemory**：需在 `_PENDING_COLUMNS` 映射注册（见 CLAUDE.md 踩坑"create_all 不 ALTER 已有表"），或通过 Alembic 迁移。表是全新的，`create_all()` 能直接建。
- **LLM 沉淀的 prompt 设计**：整合 prompt 应明确"保留关键事实：人物、事件、决策、任务、时间；不超过 500 字"。

---

## 模块 ⑥ 群清单 + 管理员通知

### 目标

- 插件层冷启动时调 `bot.call_action("get_group_list")` 获取 bot 加入的所有群
- 通过 HTTP 推送给 core，写入 conversations 表
- bootstrap 启动邮件通知增加群清单段落

### 交付物清单

| 文件 | 改动 |
|---|---|
| `data/plugins/emily_agent/main.py` | `initialize` 增加群列表同步逻辑 |
| `data/plugins/emily_agent/adapters/api_client.py` | 新增 `sync_groups(group_list)` 方法 |
| `emily-core/api/routes/groups.py` | **新建**：`POST /api/v1/groups/sync` endpoint |
| `emily-core/api/server.py` | 注册 groups 路由 |
| `emily-core/emily_core/services/group_registry_service.py` | **新建**：群列表 upsert + 查询 |
| `emily-core/emily_core/bootstrap.py` | `_collect_system_snapshot` 加群统计；`startup_report` 加 groups；邮件模板扩展 |

### 代码骨架

**1. main.py — 插件层群列表同步**

```python
async def initialize(self) -> None:
    self._sse_task = asyncio.create_task(self.sse.listen(self._sse_url))
    health = await self.api.health_check()
    # ── 新增：同步群列表 ──
    try:
        await self._sync_group_list()
    except Exception as e:
        logger.warning("group list sync failed (non-blocking): %s", e)

async def _sync_group_list(self) -> None:
    """从 astrbot 获取 bot 加入的所有群，推给 core。"""
    groups = []
    for platform in self.context.platform_manager.platform_insts:
        # aiocqhttp 平台持有 bot 实例
        bot = getattr(platform, "bot", None)
        if bot is None:
            continue
        try:
            group_list = await bot.call_action("get_group_list")
            for g in group_list:
                groups.append({
                    "group_id": str(g.get("group_id", "")),
                    "group_name": g.get("group_name", ""),
                    "member_count": g.get("member_count", 0),
                    "platform": getattr(platform, "platform_name", "unknown"),
                })
        except Exception as e:
            logger.warning("get_group_list failed on platform %s: %s", platform, e)
    if groups:
        await self.api.sync_groups(groups)
        logger.info("synced %d groups to core", len(groups))
```

**2. api_client.py — sync_groups 方法**

```python
async def sync_groups(self, groups: list[dict]) -> dict:
    """推送群列表到 core。"""
    return await self._post("/api/v1/groups/sync", {"groups": groups})
```

**3. routes/groups.py — 新建 endpoint**

```python
"""POST /api/v1/groups/sync —— 同步 bot 加入的群列表。"""
from fastapi import APIRouter
from pydantic import BaseModel
from ..server import get_core

router = APIRouter()

class GroupItem(BaseModel):
    group_id: str
    group_name: str = ""
    member_count: int = 0
    platform: str = ""

class GroupsSyncIn(BaseModel):
    groups: list[GroupItem]

@router.post("/groups/sync")
async def sync_groups(payload: GroupsSyncIn):
    core = get_core()
    core.group_registry_service.upsert_groups(
        [{"group_id": g.group_id, "group_name": g.group_name,
          "member_count": g.member_count, "platform": g.platform}
         for g in payload.groups]
    )
    return {"synced": len(payload.groups)}
```

**4. group_registry_service.py — upsert + 查询**

```python
class GroupRegistryService:
    """群列表注册服务 —— 接收插件同步的群列表，upsert 到 conversations 表。"""
    
    def upsert_groups(self, groups: list[dict]) -> int:
        with get_session() as session:
            count = 0
            for g in groups:
                conv = session.query(Conversation).filter(
                    Conversation.im_platform == g["platform"],
                    Conversation.conversation_id == g["group_id"],
                ).first()
                if conv is None:
                    conv = Conversation(
                        im_platform=g["platform"],
                        conversation_type="group",
                        conversation_id=g["group_id"],
                        group_id=g["group_id"],
                        title=g["group_name"],
                        takeover_mode="monitor",
                    )
                    session.add(conv)
                else:
                    if g["group_name"]:
                        conv.title = g["group_name"]
                count += 1
            session.commit()
            return count
    
    def list_groups(self) -> list[dict]:
        """列出所有已知群（供启动通知用）。"""
        with get_session() as session:
            convs = session.query(Conversation).filter(
                Conversation.conversation_type == "group"
            ).all()
            return [{
                "group_id": c.group_id,
                "group_name": c.title or "(未命名)",
                "platform": c.im_platform,
                "last_active": c.updated_at,
            } for c in convs]
```

**5. bootstrap.py — 启动通知扩展**

```python
def _collect_system_snapshot() -> dict:
    # ... 原有逻辑 ...
    snapshot = {
        # ... 原有字段 ...
        "admins": ...,
    }
    # ── 新增：群清单 ──
    try:
        from .services.group_registry_service import GroupRegistryService
        groups = GroupRegistryService().list_groups()
        snapshot["groups"] = groups
        snapshot["groups_total"] = len(groups)
    except Exception as e:
        _logger.warning("group list collect failed: %s", e)
        snapshot["groups"] = []
        snapshot["groups_total"] = 0
    return snapshot

# startup_report 增加：
startup_report = {
    # ... 原有字段 ...
    "groups": snapshot.get("groups", []),
    "groups_total": snapshot.get("groups_total", 0),
}

# _send_startup_email 的邮件模板增加群清单段落：
# """
# 群聊覆盖 ({groups_total} 个群):
#   - {group_name} ({platform}, 最近活跃: {last_active})
#   - ...
# """
```

### 验收检测

1. **群列表同步验证**：
   ```powershell
   # 重启 emily-core + 插件
   docker compose -f docker-compose-napcat.yml restart emily-core astrbot
   # 等待插件 initialize 完成
   docker logs astrbot 2>&1 | Select-String "synced.*groups"
   # 预期：日志含 "synced N groups to core"
   
   docker exec emily-postgres psql -U emily -d emily -c "SELECT conversation_id, title, takeover_mode FROM conversations WHERE conversation_type='group';"
   # 预期：所有 bot 加入的群都在表里，takeover_mode=monitor，title=群名
   ```

2. **冷启动邮件验证**：
   ```powershell
   # 检查管理员邮箱（或日志中的邮件内容）
   docker logs --tail 200 emily-core 2>&1 | Select-String "群聊覆盖|groups_total"
   # 预期：启动通知含群清单段落
   ```

3. **沉默群发现验证**：
   ```powershell
   # 找一个从未发过消息的群（bot 已加入）
   # 重启后检查 conversations 表
   # 预期：该群在表里（被动收集路径发现不了，主动同步能发现）
   ```

### 依赖与注意事项

- **astrbot context 访问**：`self.context.platform_manager.platform_insts` 已验证可用（[context.py:471](#)）。aiocqhttp 平台实例的 `bot` 属性持有 OneBot bot 对象。
- **多平台兼容**：循环 `platform_insts` 处理多平台。非 aiocqhttp 平台（如 lark/telegram）`call_action("get_group_list")` 可能不支持，需 try-except 兜底。
- **同步时机**：`initialize` 时同步一次。可选：定期同步（如每小时）以发现新加入的群。首版只做启动同步。
- **API 鉴权**：`POST /api/v1/groups/sync` 复用现有 `emycore_api_token` 鉴权（与 `/message/send` 一致）。
- **与模块①的协同**：模块①的被动 `get_group()` 补群名 + 本模块的主动 `get_group_list` 同步，两者都写 conversations.title。本模块在冷启动时批量补齐，模块①在运行时补漏。

---

## 附录 A：验收检测汇总

### A.1 端到端验收（全部模块完成后）

```powershell
# ── 场景 1：权限越界拦截 ──
# L6 用户 A 在群内 @emily 创建事件
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "创建事件：端到端越权测试" --sender "管理员A" --group-id "e2e_group_001"

# L2 用户 B 在同群 @emily 尝试删除
uv run python .claude/skills/emy-test/cli.py --managed --llm `
  --message "删除那个事件" --sender "施工员B" --group-id "e2e_group_001"

# 预期：B 被拦截，审计日志 user_id=B


# ── 场景 2：任务段生命周期 ──
# @emily 完成任务，3 分钟内不操作
# 预期：Session 归档，归档文件按群名命名


# ── 场景 3：DB 回溯上下文 ──
# 群内讨论（不@emily）→ @emily 引用讨论
# 预期：emily 能引用群内最近讨论


# ── 场景 4：群级记忆延续 ──
# Session1: "我叫张三" → 归档
# Session2: "我是谁"
# 预期：emily 从群级记忆回答


# ── 场景 5：谁发起谁确认 ──
# A 创建事件进 pending → B 尝试确认
# 预期：B 被拒


# ── 场景 6：群清单通知 ──
# 重启 emily-core
# 预期：管理员收到含群清单的启动邮件
```

### A.2 回归验证

- **私聊无回归**：所有私聊场景行为不变（actor_snapshot = session_context 权限）
- **SOP 路由无回归**：意图识别准确率不下降（群聊上下文注入不应干扰路由）
- **闲聊短路无回归**：`_try_fast_reply` 仍正常工作
- **归档机制无回归**：session_archives 表正常写入，md 文件正常生成

---

## 附录 B：风险与回滚

### B.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| monitor 模式 DB 写入量激增 | 高 | 中 | 监控 messages 表增长；必要时加 TTL 清理旧记录 |
| actor_snapshot 查询拖慢响应 | 中 | 中 | 复用 SessionDataFetcher 缓存；必要时加权限缓存层 |
| LLM 充分性判断误判 | 中 | 中 | fail-open（默认充分）；监控回溯批次分布 |
| 群级记忆沉淀质量差 | 中 | 低 | prompt 调优；可配置关闭群级记忆 |
| 硬上限切断长任务 | 低 | 高 | 30 分钟上限可配置；观察后调整 |
| get_group_list API 失败 | 低 | 低 | fail-open；被动收集路径兜底 |

### B.2 回滚

每个模块独立回滚：

| 模块 | 回滚方式 |
|---|---|
| ① | config.takeover_mode 改回 "collaborate"；inbound_adapter group_name 提取回退 |
| ② | BusContext._actor_snapshot 置 None，AuthHook 回退读 session_context |
| ③ | session_config 配置改回 ttl_seconds=600 单一超时；sweeper 逻辑回退 |
| ④ | group_context_service 注入关闭（handle 入口跳过回溯） |
| ⑤ | GroupMemory 表保留不注入；session_context 群聊分支回退为跳过 |
| ⑥ | 插件 _sync_group_list 注释；groups 路由不注册 |

**全量回滚**：git revert 本计划涉及的 commit，无破坏性 schema 变更（GroupMemory 是新表，删除不影响现有功能）。

---

## 附录 C：执行方注意事项

1. **先读 CLAUDE.md**：了解项目架构、emy-test 规则、CodeGraph 使用（注：CodeGraph 当前未初始化，需 `codegraph init` 或直接用 Grep/Read）
2. **每个模块独立提交**：便于回滚和 review
3. **改代码后同步更新 docs/**：按 CLAUDE.md §10 维护约定，更新 [docs/业务模块与运转全景.md](../docs/业务模块与运转全景.md)、[docs/数据库设计.md](../docs/数据库设计.md)（GroupMemory 新表）、[docs/技术踩坑备忘录.md](../docs/技术踩坑备忘录.md)
4. **emy-test 强制规则**：`--sender-id` 必须用 users 表真实 UUID，或用 `--sender "用户名"` 自动解析。测试前先查 users 表
5. **容器内 __pycache__**：每次代码变更后 `docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`
6. **PowerShell 编码**：`$env:PYTHONIOENCODING="utf-8"` 防乱码

---

*本计划基于 2026-07-26 的代码现状与讨论结论。执行方实施前应 re-verify 关键文件位置（session_agent.py / hook.py / scheduler.py 等）未发生重大重构。*
