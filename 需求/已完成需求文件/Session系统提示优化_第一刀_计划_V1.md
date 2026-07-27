# Session 系统提示优化 — 第一刀实施计划

## Context

**为什么做这个改动**：SessionAgent 每次 LLM 调用都重发数千 token 的 system prompt，且因 `{current_datetime}` 等易变字段污染前缀，DeepSeek Context Caching（前缀一致按 0.1x 计费）完全没生效——全价计费。同时每轮重复渲染模板、路由用昂贵的主模型。

**本计划范围（第一刀）**：仅 P0-1（前缀稳定化 + cache 观测）+ P0-2（预渲染缓存）+ P0-3（路由启用 router_model + fallback）。**不含** P1-1（目录工具化）和 P2-1（双模型分层）——它们依赖第一刀先稳定前缀。

**预期收益**：system prompt 命中 cache 后按 0.1x 计费，单次调用 token 成本降 60-80%；渲染结果复用；路由用 flash 模型更便宜更快。

**需求依据**：[需求/Session系统提示优化_需求_V1.md](../需求/Session系统提示优化_需求_V1.md) §四 P0-1/P0-2/P0-3 + §五 第一刀。

---

## 探查发现的关键约束（影响实施）

1. **`_recognize_intent` 自己组装 messages**（[session_agent.py:264-299](../emily-core/emily_core/session/session_agent.py#L264-L299)），**不走 `build_llm_messages`**。P0-2 预渲染缓存改这里，不是 build_llm_messages。
2. **`_recognize_intent` L326-328 注释警告**：router_model（v4-flash）在大 prompt 上可能把输出放进 reasoning_content 返回空白 content。**P0-3 必须带 fallback**：flash 返回空白/失败时回退主模型重试。
3. **trace callback 两处**要补 cache 字段：tool_call 分支 [client.py:171-185](../emily-core/emily_core/infrastructure/llm/client.py#L171-L185) + 正常 end 分支 [client.py:210-227](../emily-core/emily_core/infrastructure/llm/client.py#L210-L227)。
4. **config 里 `llm_router_model` 已存在**（[config.py:44](../emily-core/emily_core/config.py#L44)），LLMClient 已有 `self.router_model` 字段（[client.py:37](../emily-core/emily_core/infrastructure/llm/client.py#L37)）。P0-3 只是补传 `model=router_model`，不用加配置。
5. **`prompt_loader` 已有内存缓存**（[prompt_loader.py:25](../emily-core/emily_core/infrastructure/llm/prompt_loader.py#L25) `_cache` + `reload_prompt`），模板加载层不动。
6. **`get_prompt_variables`**（[session_context.py:360-389](../emily-core/emily_core/session/session_context.py#L360-L389)）返回的 dict 含 `"{current_datetime}"`，P0-1 移除模板占位符后这行要清理。

---

## 你的角色

你作为 **Emily 开发者资深架构师** + **实施计划编制专家**，严格按 M1→M5 顺序执行，逐模块验证，验证不通过不进入下一个模块。

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`build_llm_messages` / `chat_messages` / `get_prompt_variables` 的签名不得变，只能在方法内部改实现或新增方法
2. **业务内核独立**：`emily_core` 不 import 任何 `astrbot.*` 包（[CLAUDE.md](../CLAUDE.md) 约束 1）
3. **分层不可跳**：本计划只改 Session/Application/Infrastructure 层，不动 Repository/DB
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **P0-3 必须带 fallback**：flash 模型返回空白/失败时回退主模型，否则路由会退化
6. **不移除目录类占位符**：`{available_tools}` `{sop_catalog}` `{visible_schema}` 等保留（那是 P1-1 范围），P0-1 只移除 `{current_datetime}` 并重排顺序
7. **改完代码必须清 `__pycache__`**：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`

## 代码模式参照表

| 层 | 参照源 | 要模仿的要点 |
|----|------|-------------|
| LLM trace callback | [client.py:171-227](../emily-core/emily_core/infrastructure/llm/client.py#L171-L227) | `getattr(response.usage, "xxx", 0) if response.usage else 0` 安全取值模式 |
| prompt 渲染 | [session_agent.py:264-273](../emily-core/emily_core/session/session_agent.py#L264-L273) | `.replace()` 循环 + 空值替换为"（无）" |
| 消息组装 | [session_agent.py:276-299](../emily-core/emily_core/session/session_agent.py#L276-L299) | system → history → pending system → user 顺序 |
| 异常降级 | [client.py:134-153](../emily-core/emily_core/infrastructure/llm/client.py#L134-L153) | try/except 回退主路径模式 |

## 模块依赖图

```
M1(trace cache 字段补全) ──────────────────────────┐
                                                   ↓
M2(session.md 重排+移除 datetime) → M3(_recognize_intent datetime 迁移) → M4(预渲染缓存) → M5(router_model+fallback)
```

- M1 独立，先做（提供观测能力，否则后续无法验证 cache 命中）
- M2→M3→M4→M5 顺序依赖
- M5 必须在 M4 后（同文件 session_agent.py，且 M4 稳定 prompt 后 flash 才更可靠）

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/infrastructure/llm/client.py` | 修改 | 两处 trace callback 补 cache 字段 |
| M2 | `emily-data/prompts/session.md` | 修改 | 重排为 L1/L2 分层 + 移除 `{current_datetime}` 占位符 |
| M3 | `emily-core/emily_core/session/session_agent.py` + `session_context.py` | 修改 | datetime 从 system 迁到 user message 末尾 |
| M4 | `emily-core/emily_core/session/session_agent.py` | 修改 | 新增 `_build_rendered_system_prompt()` + `__init__` 预渲染缓存 |
| M5 | `emily-core/emily_core/session/session_agent.py` | 修改 | `_recognize_intent` 传 `model=router_model` + flash 失败回退主模型 |

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/infrastructure/llm/client.py` | 修改 | trace callback 两处补 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` |
| `emily-data/prompts/session.md` | 修改 | 重排章节顺序（L1 静态在前 / L2 半静态在后）+ 移除 `{current_datetime}` 占位符行 |
| `emily-core/emily_core/session/session_agent.py` | 修改 | `_recognize_intent` 改用缓存 prompt + datetime 迁 user msg + router_model + fallback；`__init__` 增预渲染 |
| `emily-core/emily_core/session/session_context.py` | 修改 | `build_llm_messages` 同步 datetime 迁移；`get_prompt_variables` 清理 `{current_datetime}` 条目 |

---

## M1: trace cache 字段补全

**依赖**：无（首建模块，提供观测能力）

**职责**：在 LLM trace callback 中记录 DeepSeek 返回的 cache 命中字段，使后续模块的 cache 命中率可观测。

### 代码

#### `emily-core/emily_core/infrastructure/llm/client.py` — tool_call 分支 trace（约 L181 后）追加字段

定位 [client.py:171-185](../emily-core/emily_core/infrastructure/llm/client.py#L171-L185) 的 tool_call 分支 `self._trace_callback({..."total_tokens": ..., "latency_ms": elapsed_ms})`，在 `"total_tokens"` 行后追加两行：

```python
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                        "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                        "prompt_cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", 0) if response.usage else 0,
                        "prompt_cache_miss_tokens": getattr(response.usage, "prompt_cache_miss_tokens", 0) if response.usage else 0,
                        "latency_ms": elapsed_ms,
```

#### `emily-core/emily_core/infrastructure/llm/client.py` — 正常 end 分支 trace（约 L223 后）追加字段

定位 [client.py:210-227](../emily-core/emily_core/infrastructure/llm/client.py#L210-L227) 的正常 end 分支 `self._trace_callback({..."total_tokens": ..., "latency_ms": elapsed_ms})`，在 `"total_tokens"` 行后追加同样的两行：

```python
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                        "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                        "prompt_cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", 0) if response.usage else 0,
                        "prompt_cache_miss_tokens": getattr(response.usage, "prompt_cache_miss_tokens", 0) if response.usage else 0,
                        "latency_ms": elapsed_ms,
```

### 模块验收检测

```powershell
# 验收 1：两处 trace callback 都含新字段
docker exec emily-core grep -c "prompt_cache_hit_tokens" /app/emily_core/infrastructure/llm/client.py
→ 预期输出：4（两处 callback × 2 个字段 = 4 次出现；其中 hit 字段出现 2 次）

# 验收 2：重启后发一条消息，看 jsonl 是否落盘新字段
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
# 等待 5 秒后查最新一条 trace
docker exec mitmproxy tail -1 /app/logs/llm_trace.jsonl
→ 预期输出：jsonl 最后一行 JSON 含 "prompt_cache_hit_tokens" 和 "prompt_cache_miss_tokens" 字段（数值可能为 0，首次调用未命中正常）
```

**失败处理**：若 jsonl 不含新字段，检查 mitmproxy 的 trace_addon.py 是否过滤了这些字段——若过滤了，需在 addon 的白名单里加上。

---

## M2: session.md 模板重排 + 移除 datetime 占位符

**依赖**：无（可与 M1 并行）

**职责**：把 session.md 重排为"L1 全局静态在前 / L2 Session 级半静态在后"的稳定结构，移除 `{current_datetime}` 占位符（迁到 user message）。**不移除**目录类占位符（P1-1 范围）。

### 代码

#### `emily-data/prompts/session.md` — 重排章节顺序

当前章节顺序（[session.md](../emily-data/prompts/session.md)）：
```
一、角色与定位          ← L1 静态
二、业务背景（三书）     ← L2 半静态
三、当前会话上下文       ← L2 半静态（含 {current_datetime}，需迁出）
四、能力清单            ← L2 半静态
五、行为规范            ← L1 静态
```

重排为：
```
一、角色与定位                      ← L1 静态（原一，不动）
二、行为规范                        ← L1 静态（原五，上移）—— 路由规则/输出要求/output_spec/query_type
三、业务背景（三书）                 ← L2 半静态（原二）
四、当前会话上下文                   ← L2 半静态（原三，移除 {current_datetime} 行）
五、能力清单                        ← L2 半静态（原四，不动）
```

**关键改动**：
1. 把原"五、行为规范"整段移到"二、"位置（紧跟角色定位），成为 L1 静态前缀的一部分
2. 在原"三、当前会话上下文"的"### 当前时间"小节（[session.md:67-68](../emily-data/prompts/session.md#L67-L68)）删除以下两行：
   ```
   ### 当前时间
   {current_datetime}
   ```
3. 顶部 HTML 注释里的模板变量说明（[session.md:3](../emily-data/prompts/session.md#L3)）移除 `{current_datetime}`

**注意**：所有占位符（`{project_world_book}` `{rule_book}` `{available_tools}` `{sop_catalog}` 等）**全部保留**，只动 `{current_datetime}` 和章节顺序。

### 模块验收检测

```powershell
# 验收 1：session.md 不再含 {current_datetime} 占位符
Select-String -Path "emily-data\prompts\session.md" -Pattern "\{current_datetime\}"
→ 预期输出：无匹配（空）

# 验收 2：行为规范章节在能力清单之前
$lines = Get-Content "emily-data\prompts\session.md"
$behaviorIdx = ($lines | Select-String "## 二、行为规范" | Select-Object -First 1).LineNumber
$capabilityIdx = ($lines | Select-String "## 五、能力清单" | Select-Object -First 1).LineNumber
$behaviorIdx -lt $capabilityIdx
→ 预期输出：True

# 验收 3：所有目录类占位符仍在
Select-String -Path "emily-data\prompts\session.md" -Pattern "\{(available_tools|sop_catalog|visible_schema|visible_files|project_world_book|rule_book|system_description)\}"
→ 预期输出：至少 7 行匹配
```

**失败处理**：若占位符误删，从 git 恢复 `git checkout -- emily-data/prompts/session.md` 后重做。

---

## M3: datetime 从 system prompt 迁到 user message

**依赖**：M2（模板已移除 `{current_datetime}` 占位符）

**职责**：在 `_recognize_intent` 和 `build_llm_messages` 中，把当前时间从 system prompt 拼接改为 user message 末尾追加，使 system prompt 前缀完全稳定。

### 代码

#### `emily-core/emily_core/session/session_agent.py` — `_recognize_intent` 渲染段（约 L264-266）移除 datetime replace

定位 [session_agent.py:264-266](../emily-core/emily_core/session/session_agent.py#L264-L266)：

```python
# 原代码
system_prompt = (_SESSION_SYSTEM_PROMPT
    .replace("{sop_catalog}", sop_catalog)
    .replace("{current_datetime}", _beijing_now_str()))
```

改为（移除 datetime replace，M2 模板已无此占位符；M4 会把整段改为用缓存）：

```python
# M3: datetime 已从模板移除，system prompt 不再含时间字段（迁到 user message）
# M4 将把这段渲染改为复用缓存的 _rendered_system_prompt
system_prompt = _SESSION_SYSTEM_PROMPT.replace("{sop_catalog}", sop_catalog)
```

#### `emily-core/emily_core/session/session_agent.py` — user message 组装（约 L295-299）追加时间

定位 [session_agent.py:294-299](../emily-core/emily_core/session/session_agent.py#L294-L299)：

```python
# 原代码
sender = getattr(message, "sender_name", "") or ""
full_messages.append({
    "role": "user",
    "content": content,
    "name": sender if sender else None,
})
```

改为（content 末尾追加时间标记）：

```python
sender = getattr(message, "sender_name", "") or ""
# M3: 当前时间从 system prompt 迁到 user message 末尾，保持 system 前缀稳定
user_content = f"{content}\n\n[当前时间: {_beijing_now_str()}]"
full_messages.append({
    "role": "user",
    "content": user_content,
    "name": sender if sender else None,
})
```

#### `emily-core/emily_core/session/session_context.py` — `build_llm_messages`（约 L350-356）同步迁移

定位 [session_context.py:350-356](../emily-core/emily_core/session/session_context.py#L350-L356) 的当前用户消息组装段：

```python
# 原代码
if current_user_msg:
    full_messages.append({
        "role": "user",
        "content": current_user_msg,
        "name": sender_name if sender_name else None,
    })
```

改为：

```python
if current_user_msg:
    # M3: 当前时间迁到 user message 末尾，保持 system 前缀稳定
    from datetime import datetime, timezone, timedelta
    _now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    full_messages.append({
        "role": "user",
        "content": f"{current_user_msg}\n\n[当前时间: {_now_str}]",
        "name": sender_name if sender_name else None,
    })
```

#### `emily-core/emily_core/session/session_context.py` — `get_prompt_variables`（约 L379）清理 datetime 条目

定位 [session_context.py:379](../emily-core/emily_core/session/session_context.py#L379)：

```python
# 原代码（删除这一行）
"{current_datetime}": self.current_datetime,
```

直接删除该行（M2 模板已无此占位符，replace 不到，保留是死代码）。

### 模块验收检测

```powershell
# 验收 1：session_agent.py 不再 replace {current_datetime}
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern 'replace\("\{current_datetime\}"'
→ 预期输出：无匹配（空）

# 验收 2：user message 拼接了时间
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "\[当前时间:"
→ 预期输出：1 行匹配

# 验收 3：get_prompt_variables 不再含 current_datetime
Select-String -Path "emily-core\emily_core\session\session_context.py" -Pattern 'current_datetime'
→ 预期输出：仅 build_llm_messages 内部的 _now_str 相关行，无 get_prompt_variables 中的条目

# 验收 4：重启后发消息，system prompt 不含时间，user message 含时间
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
docker exec mitmproxy tail -1 /app/logs/llm_trace.jsonl
→ 预期输出：jsonl 中 messages[0].content（system）不含"当前时间"；messages[-1].content（user）含"[当前时间: 2026-..."
```

**失败处理**：若 system prompt 仍含时间，检查是否还有其他地方 replace `{current_datetime}`——全局 grep `current_datetime` 排查。

---

## M4: Session 级 prompt 预渲染缓存

**依赖**：M3（datetime 已迁出，system prompt 内容在 Session 内完全稳定）

**职责**：SessionAgent 创建时渲染一次 system prompt（L1+L2），缓存到实例变量，后续 `_recognize_intent` 直接复用，省每轮 replace 循环，且保证 system prompt 在 Session 内字节级稳定（cache 命中前提）。

### 代码

#### `emily-core/emily_core/session/session_agent.py` — 新增 `_build_rendered_system_prompt` 方法 + `__init__` 预渲染

在 `SessionAgent` 类中新增实例方法，放在 `__init__` 方法之后：

```python
def _build_rendered_system_prompt(self) -> str:
    """渲染 Session 级 system prompt（L1+L2），Session 内缓存复用。

    M4: 把 _recognize_intent 每轮的 replace 循环提到 Session 创建时一次完成。
    system prompt 在 Session 内字节级稳定，是 DeepSeek cache 命中的前提。
    sop_catalog / 三书 / 身份 / 能力清单 在 Session 内不变，可安全缓存。
    current_datetime 已在 M3 迁到 user message，不在此处渲染。
    """
    try:
        sop_catalog = self._skill_registry.dump_as_text()
    except Exception as e:
        logger.warning("Failed to dump SOP catalog for cached prompt: %s", e)
        sop_catalog = "（SOP 目录加载失败）"

    prompt = _SESSION_SYSTEM_PROMPT.replace("{sop_catalog}", sop_catalog)
    # Session 级变量替换（current_datetime 已移除，不再处理）
    prompt_vars = self.context.get_prompt_variables()
    for key, value in prompt_vars.items():
        replacement = str(value) if value else "（无）"
        prompt = prompt.replace(key, replacement)
    return prompt
```

在 `__init__` 方法末尾（[session_agent.py:167](../emily-core/emily_core/session/session_agent.py#L167) `_log_session_lifecycle(...)` 调用后）追加预渲染：

```python
        # ── 进化日志：Session 创建 ──
        _log_session_lifecycle(self.conversation_id, self.context.user_id, "created")

        # M4: 预渲染 Session 级 system prompt 并缓存（Session 内字节级稳定）
        self._rendered_system_prompt = self._build_rendered_system_prompt()
        logger.debug("Session[%s] rendered system prompt cached: %d chars",
                     self.conversation_id, len(self._rendered_system_prompt))
```

#### `emily-core/emily_core/session/session_agent.py` — `_recognize_intent` 改用缓存（约 L264-273）

定位 [session_agent.py:264-273](../emily-core/emily_core/session/session_agent.py#L264-L273) 的渲染段（M3 已改过一次）：

```python
# M3 后的代码
system_prompt = _SESSION_SYSTEM_PROMPT.replace("{sop_catalog}", sop_catalog)

# 注入 Session 级变量（D5：两阶段 format）
prompt_vars = self.context.get_prompt_variables()
for key, value in prompt_vars.items():
    replacement = str(value) if value else "（无）"
    system_prompt = system_prompt.replace(key, replacement)
```

整段替换为直接复用缓存：

```python
# M4: 直接复用 __init__ 时预渲染的 system prompt（Session 内字节级稳定）
system_prompt = self._rendered_system_prompt
```

同时，M3 段保留下来的 `sop_catalog = catalog_source.dump_as_text()`（[session_agent.py:258](../emily-core/emily_core/session/session_agent.py#L258)）和后续 `sop_catalog` 变量在 `_last_intent_prompt_info`（L310）中仍被引用，保留 dump 调用不变（仅用于归档记录字符数），不影响缓存。

### 模块验收检测

```powershell
# 验收 1：_build_rendered_system_prompt 方法存在
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "def _build_rendered_system_prompt"
→ 预期输出：1 行匹配

# 验收 2：__init__ 含预渲染调用
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "_rendered_system_prompt = self._build_rendered_system_prompt"
→ 预期输出：1 行匹配

# 验收 3：_recognize_intent 复用缓存而非每轮 replace
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "system_prompt = self\._rendered_system_prompt"
→ 预期输出：1 行匹配

# 验收 4：连发两条消息，两次 system prompt 字节级一致（cache 命中前提）
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
Start-Sleep -Seconds 3
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "在吗" --sender "真实用户名"
# 取最近两条 intent 调用的 system prompt 比对
docker exec mitmproxy tail -5 /app/logs/llm_trace.jsonl
→ 预期输出：两次调用的 messages[0].content 完全相同（同一 Session 内）
```

**失败处理**：若两次 system prompt 不一致，检查 `_rendered_system_prompt` 是否被某处重新赋值，或 `get_prompt_variables` 是否返回了 Session 内会变的字段（如 `current_datetime` 残留）。

---

## M5: 路由启用 router_model + flash 失败回退

**依赖**：M4（prompt 已缓存稳定，flash 在稳定 prompt 上更可靠）

**职责**：`_recognize_intent` 调用 LLM 时显式传 `model=router_model`（v4-flash，便宜快），并带 fallback——flash 返回空白/解析失败/异常时回退主模型重试，避免路由退化。

### 代码

#### `emily-core/emily_core/session/session_agent.py` — `_recognize_intent` 调用段（约 L325-338）改为 router_model + fallback

定位 [session_agent.py:325-338](../emily-core/emily_core/session/session_agent.py#L325-L338)：

```python
# 原代码
try:
    # 意图识别 prompt 较大（含 SOP catalog + 对话历史），router_model（v4-flash）
    # 在大 prompt 上可能把输出放进 reasoning_content 而返回空白 content；
    # 用主模型（v4-pro）更可靠
    result = await self._llm.chat_messages(full_messages, json_mode=True)
    data = result.get("data", {})
    logger.debug("SessionAgent intent for '%s': sop=%s conf=%s compound=%s",
                 content[:40], data.get("sop_id"), data.get("confidence"),
                 data.get("is_compound"))
    return data
except Exception as e:
    logger.warning("SessionAgent intent recognition failed: %s", e)
    return {"sop_id": None, "confidence": "none", "reasoning": f"LLM调用失败: {e}",
            "is_compound": False, "sub_tasks": [], "fallback": True}
finally:
    LLMInteractionLogger.clear_context()
```

替换为（router_model 优先 + flash 失败/空白回退主模型）：

```python
try:
    # M5: 路由用 router_model（v4-flash，便宜快）。M4 已缓存稳定 prompt，flash 可靠性提升。
    # fallback：flash 返回空白/解析失败/异常时回退主模型（v4-pro），避免路由退化。
    router_model = getattr(self._llm, "router_model", None) or self._llm.model
    data = {}
    try:
        result = await self._llm.chat_messages(full_messages, json_mode=True, model=router_model)
        data = result.get("data", {}) or {}
    except Exception as router_err:
        logger.warning("router_model (%s) intent failed, fallback to main model: %s",
                       router_model, router_err)
        result = await self._llm.chat_messages(full_messages, json_mode=True)
        data = result.get("data", {}) or {}

    # flash 可能把输出放进 reasoning_content 返回空白 content → data 为空 dict
    if not data or not data.get("sop_id"):
        logger.warning("router_model returned empty data, fallback to main model")
        result = await self._llm.chat_messages(full_messages, json_mode=True)
        data = result.get("data", {}) or {}

    logger.debug("SessionAgent intent for '%s': sop=%s conf=%s compound=%s",
                 content[:40], data.get("sop_id"), data.get("confidence"),
                 data.get("is_compound"))
    return data
except Exception as e:
    logger.warning("SessionAgent intent recognition failed: %s", e)
    return {"sop_id": None, "confidence": "none", "reasoning": f"LLM调用失败: {e}",
            "is_compound": False, "sub_tasks": [], "fallback": True}
finally:
    LLMInteractionLogger.clear_context()
```

### 模块验收检测

```powershell
# 验收 1：_recognize_intent 传了 model=router_model
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "model=router_model"
→ 预期输出：1 行匹配

# 验收 2：含 fallback 逻辑
Select-String -Path "emily-core\emily_core\session\session_agent.py" -Pattern "fallback to main model"
→ 预期输出：至少 2 行匹配（异常回退 + 空白回退）

# 验收 3：重启后发消息，trace 显示 intent 调用用 v4-flash
docker compose -f docker-compose-napcat.yml restart emily-core
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"
docker exec mitmproxy tail -3 /app/logs/llm_trace.jsonl
→ 预期输出：intent 调用（call_category=intent 或 call_type=chat_messages_json）的 model 字段为 "deepseek-v4-flash"；且 sop_id 非空（路由成功，未退化）
```

**失败处理**：若 sop_id 为空且 fallback 触发，说明 flash 仍不可靠——检查 fallback 是否正确回退到主模型并成功；若主模型也失败，检查 prompt 是否因 M2 重排导致格式破坏。

---

## 组装验证

所有模块完成后，端到端验证 cache 命中率 + 路由正确性：

```powershell
# 1. 清缓存 + 重启
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 先查真实用户 UUID
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, permission_level FROM users WHERE status = 'active' ORDER BY permission_level LIMIT 5;"

# 3. 同一 Session 连发 3 条消息（触发同 Session 内多次 intent 调用）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
Start-Sleep -Seconds 3
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"
Start-Sleep -Seconds 3
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天有什么事件" --sender "真实用户名"

# 4. 查看 cache 命中率
docker exec mitmproxy tail -10 /app/logs/llm_trace.jsonl
```

**预期输出**：
- 第 2、3 条消息的 intent 调用，`prompt_cache_hit_tokens` > 0（第 1 条可能 miss，正常）
- cache 命中率（`prompt_cache_hit_tokens / prompt_tokens`）随调用次数递增，稳定后 >50%
- 所有调用 `sop_id` 非空或 fallback 正确（路由未退化）
- intent 调用 `model` 为 `deepseek-v4-flash`

**失败处理**：
- cache 命中率为 0 → 检查 M2/M3/M4 是否让 system prompt 在 Session 内字节级稳定（两次调用 messages[0].content 必须完全相同）
- 路由退化（sop_id 全空）→ 检查 M5 fallback 是否生效，必要时临时回退 `model=router_model` 注释掉保持主模型
- jsonl 不含 cache 字段 → 检查 M1 + mitmproxy trace_addon.py 白名单

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户

---

## 关键提醒

1. **`__pycache__` 必须清**：每次改完代码重启 emily-core 前必须清，否则 Docker bind-mount 不刷新（[CLAUDE.md](../CLAUDE.md) 踩坑速查）
2. **emy-test 禁用假 sender-id**：必须用真实用户名（`--sender`），否则自动创建用户污染 DB + 权限降级使结果不可信
3. **PowerShell GBK 乱码**：必要时 `$env:PYTHONIOENCODING="utf-8"`
4. **mitmproxy jsonl 路径**：`emily-data/logs/llm_trace.jsonl`（宿主）/ `/app/logs/llm_trace.jsonl`（容器内）
5. **本计划不含 P1-1**：不要顺手移除 `{available_tools}` `{sop_catalog}` 等占位符——那是第二刀的范围，提前移除会导致 LLM 能力断档

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。基于 [需求/Session系统提示优化_需求_V1.md](../需求/Session系统提示优化_需求_V1.md) 第一刀范围（P0-1 + P0-2 + P0-3）。*
