# LLM 通讯实时追踪 — AI 执行计划

> **基于需求**：实时观察 emily 调用 LLM 的实际通讯内容（请求 messages 全文 + 响应全文 + token + 延迟）
> **计划版本**：v1.0
> **目标**：挂载 emily-core 已写好但未接线的 `ConsoleLLMTracer`，通过环境变量开关启用，`docker logs -f emily-core` 实时查看 LLM 通讯报文
> **替代关系**：本计划替代 [LLM流量代理抓包_计划_V1.md](LLM流量代理抓包_计划_V1.md)——代理方案需独立容器，复杂度过高，改用应用层 trace（零容器零额外进程）。旧计划文件建议删除或归档到 `需求/已完成需求文件/` 并注明废弃

---

## 你的角色

你作为 **Emily 后端工程师**，严格按模块顺序执行，逐模块验证，验证不通过不进入下一个模块。改动量很小（3 个文件共 ~10 行 + 1 个环境变量），但每处都必须精准对齐现有代码模式。

---

## 硬约束（违反即失败）

1. **不引入新容器 / 新进程**：纯应用层 trace，复用 emily-core 现有进程，通过 stdout 输出
2. **不破坏 DB 日志链路**：现有 `LLMInteractionLogger`（写 `evolution_llm_interaction_logs` 表）必须保留，用 `add_trace_callback` 追加而非 `set_trace_callback` 替换
3. **环境变量开关**：通过 `EMILY_LLM_CONSOLE_TRACE_ENABLED=1` 控制，默认关闭，生产环境不开启
4. **不改 ConsoleLLMTracer 类**：[console_tracer.py](emily-core/emily_core/infrastructure/llm/console_tracer.py) 已写好，本次只接线不动它（可选优化除外，见后续章节）
5. **配置走统一通路**：环境变量 → bootstrap `_config_from_env` → Config dataclass → `__init__.py` 读取 `self.config.llm_console_trace_enabled`。不直接在 `__init__.py` 读 `os.environ`（保持项目配置惯例）
6. **每模块验收**：每个模块验收必须通过，否则停止

---

## 上下文（执行前必读）

### 问题背景

emily-core 调用 DeepSeek LLM 时，`LLMClient.chat_messages()`（[client.py:81](emily-core/emily_core/infrastructure/llm/client.py#L81)）在每次调用前后通过 `_fire_trace()` 触发 trace 回调，数据含完整 `messages` 请求体、`response_full` 响应体、`reasoning_content` 思维链、token 用量、延迟。但这个 trace 管线处于**半成品**状态：

| 组件 | 位置 | 状态 |
|------|------|------|
| `_fire_trace` 触发点 | `client.py:108-121, 175-187, 202-215` | ✅ 已就位（调用前后各触发一次） |
| `LLMInteractionLogger`（DB 日志） | `infrastructure/logging/llm_logger.py` | ✅ 已挂载（`__init__.py:145`），写 `evolution_llm_interaction_logs` 表 |
| `ConsoleLLMTracer`（控制台输出） | `infrastructure/llm/console_tracer.py` | ⚠️ **类已写好但从未挂载**（无调用者） |
| `Config.llm_console_trace_enabled` 字段 | `config.py` | ❌ **不存在**（bootstrap 预留了 bool_fields 但 Config 没字段） |
| `EMILY_LLM_CONSOLE_TRACE_ENABLED` 环境变量映射 | `bootstrap.py:55-68` env_map | ❌ **漏了映射条目**（bool_fields 有 llm_console_trace_enabled，但 env_map 没对应环境变量） |

**本次任务**：完成最后三处接线（Config 字段 + bootstrap 映射 + __init__ 挂载），让 `ConsoleLLMTracer` 通过环境变量开关启用。

### 架构决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据来源 | 应用层 trace（`_fire_trace` 回调） | 零容器零进程；数据含完整 messages + response，满足"看报文内容"需求 |
| 显示渠道 | `docker logs -f emily-core`（stdout） | 无需额外 UI；`ConsoleLLMTracer` 已实现格式化输出 |
| 开关机制 | Config 字段 + 环境变量 | 完成半成品预留的 bool_fields 通路，保持项目配置惯例 |
| 回调注册方式 | `add_trace_callback`（追加） | 不覆盖现有 `LLMInteractionLogger` 的 DB 日志链路 |

### 能力边界（重要）

| 能看到 | 看不到（需代理模式才能看到） |
|--------|-----------------------------|
| ✅ 完整 `messages` 请求体（system + user 全文） | ❌ HTTP headers（Authorization、User-Agent） |
| ✅ 完整响应（content + reasoning_content 思维链） | ❌ SDK 的 tools/json_mode 回退重试过程（[client.py:140-157](emily-core/emily_core/infrastructure/llm/client.py#L140-L157)） |
| ✅ token 用量（prompt/completion/total） | ❌ 原始 HTTP 字节流 |
| ✅ 延迟、finish_reason、call_sequence 编号 | ❌ `response.system_fingerprint` 等 SDK 丢弃的字段 |

> 若未来需要 wire-level 真相（headers/SDK 行为），再启用代理方案（见 [LLM流量代理抓包_计划_V1.md](LLM流量代理抓包_计划_V1.md)）。本次应用层 trace 覆盖 90% 调试场景。

### 已有的可复用组件

| 组件 | 位置 | 关键接口 | 本次怎么用 |
|------|------|----------|-----------|
| `ConsoleLLMTracer` | `emily-core/emily_core/infrastructure/llm/console_tracer.py` | `make_callback()` 返回闭包；类属性 `enabled`/`verbose`/`max_content_length=2000` | `add_trace_callback(ConsoleLLMTracer.make_callback())` |
| `LLMClient.add_trace_callback` | `client.py:54-61` | 追加回调到 `_trace_callbacks` 列表（不清空已有） | 替代 `set_trace_callback` |
| `Config.from_dict` | `config.py:258-264` | 用 dataclass 字段名做白名单过滤：`valid_keys = {f.name for f in cls.__dataclass_fields__.values()}` | 加字段后自动接收 |
| `bootstrap._config_from_env` | `bootstrap.py:52-78` | `env_map` 字典映射环境变量→配置键；`bool_fields` 声明哪些键转 bool | 加一条 env_map 映射 |

---

## 模块依赖图

```
M1(Config 字段 + bootstrap 映射) ──→ M2(__init__.py 挂载 ConsoleLLMTracer) ──→ M3(环境变量 + 重启验证)
```

**严格顺序**：M1 必须先完成（Config 有字段 + bootstrap 有映射），否则 M2 的 `self.config.llm_console_trace_enabled` 永远是默认 `False`，即使环境变量设了也不生效。

---

## 交付物总览

| 模块 | 交付文件 | 修改/新增 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/config.py` | 修改 | Config dataclass 加 `llm_console_trace_enabled: bool = False` |
| M1 | `emily-core/emily_core/bootstrap.py` | 修改 | env_map 加 `EMILY_LLM_CONSOLE_TRACE_ENABLED` 映射 |
| M2 | `emily-core/emily_core/__init__.py` | 修改 | `set_trace_callback`→`add_trace_callback` + 追加 ConsoleLLMTracer 挂载 |
| M3 | `docker-compose-napcat.yml` | 修改 | emily-core environment 加 `EMILY_LLM_CONSOLE_TRACE_ENABLED=1` |

---

## M1：Config 字段 + bootstrap 环境变量映射

### M1.1 Config 加字段

在 [config.py:41-42](emily-core/emily_core/config.py#L41-L42) `llm_max_tokens` 字段之后追加：

```python
    llm_console_trace_enabled: bool = False
    """是否启用 LLM 通讯控制台实时追踪（开发调试用，输出到 stdout/docker logs）。
    环境变量 EMILY_LLM_CONSOLE_TRACE_ENABLED=1 开启。开启后每次 LLM 调用
    会打印完整 messages 请求体 + 响应体 + token 用量。"""
```

**说明**：
- 字段名 `llm_console_trace_enabled` 必须与 [bootstrap.py:70](emily-core/emily_core/bootstrap.py#L70) `bool_fields` 集合里的名字一致
- 默认 `False`，生产环境不开启
- `Config.from_dict` 会自动接收（白名单基于 dataclass 字段名）

### M1.2 bootstrap env_map 加映射

在 [bootstrap.py:55-68](emily-core/emily_core/bootstrap.py#L55-L68) `env_map` 字典里，`"EMILY_KB_ENABLED": "kb_enabled"` 之后追加一行：

```python
        "EMILY_LLM_CONSOLE_TRACE_ENABLED": "llm_console_trace_enabled",
```

**说明**：
- `bool_fields` 已含 `llm_console_trace_enabled`（[bootstrap.py:70](emily-core/emily_core/bootstrap.py#L70)），无需改 bool_fields
- 环境变量值 `"1"`/`"true"`/`"yes"`/`"on"` 会被转成 `True`（[bootstrap.py:75](emily-core/emily_core/bootstrap.py#L75)）

### M1.3 验收 M1

- [ ] `config.py` 含 `llm_console_trace_enabled: bool = False` 字段
- [ ] `bootstrap.py` env_map 含 `"EMILY_LLM_CONSOLE_TRACE_ENABLED": "llm_console_trace_enabled"` 映射
- [ ] 本地验证配置通路（不重启容器）：
  ```powershell
  $env:EMILY_LLM_CONSOLE_TRACE_ENABLED="1"
  uv run python -c "from emily_core.bootstrap import _config_from_env; from emily_core.config import Config; c = Config.from_dict(_config_from_env({})); print('llm_console_trace_enabled =', c.llm_console_trace_enabled)"
  ```
  预期输出：`llm_console_trace_enabled = True`
- [ ] 不设环境变量时验证默认 False：
  ```powershell
  Remove-Item Env:EMILY_LLM_CONSOLE_TRACE_ENABLED
  uv run python -c "from emily_core.config import Config; c = Config.from_dict({}); print('default =', c.llm_console_trace_enabled)"
  ```
  预期输出：`default = False`

---

## M2：__init__.py 挂载 ConsoleLLMTracer

### M2.1 修改挂载点

修改 [__init__.py:142-148](emily-core/emily_core/__init__.py#L142-L148) 的 LLM trace callback 挂载段。

**当前代码**：

```python
                # ── 进化日志：接入 LLM trace callback ──
                try:
                    from .infrastructure.logging.llm_logger import LLMInteractionLogger
                    self._llm_client.set_trace_callback(LLMInteractionLogger.make_callback())
                    logger.info("LLM trace callback connected to LLMInteractionLogger")
                except Exception as cb_err:
                    logger.warning("Failed to connect LLM trace callback: %s", cb_err)
```

**改为**：

```python
                # ── 进化日志：接入 LLM trace callback ──
                # 注意：用 add_trace_callback（追加），不要用 set_trace_callback（会清空已有回调列表）
                try:
                    from .infrastructure.logging.llm_logger import LLMInteractionLogger
                    self._llm_client.add_trace_callback(LLMInteractionLogger.make_callback())
                    logger.info("LLM trace callback connected to LLMInteractionLogger")
                except Exception as cb_err:
                    logger.warning("Failed to connect LLM trace callback: %s", cb_err)

                # ── 控制台实时 trace（开发调试用，EMILY_LLM_CONSOLE_TRACE_ENABLED=1 开启）──
                if getattr(self.config, "llm_console_trace_enabled", False):
                    try:
                        from .infrastructure.llm.console_tracer import ConsoleLLMTracer
                        self._llm_client.add_trace_callback(ConsoleLLMTracer.make_callback())
                        logger.info("LLM console tracer ENABLED (stdout)")
                    except Exception as ct_err:
                        logger.warning("Failed to connect console tracer: %s", ct_err)
```

**关键点**：
- `set_trace_callback` → `add_trace_callback`：避免覆盖 `LLMInteractionLogger` 的 DB 日志链路。两个回调共存：DB 日志 + 控制台输出
- `getattr(self.config, "llm_console_trace_enabled", False)`：用 getattr 防御性读取（万一 Config 改动未生效也不崩）
- 无需 `import os`：开关走 `self.config`，不直接读环境变量

### M2.2 验收 M2

- [ ] `__init__.py` 的 `set_trace_callback` 已改为 `add_trace_callback`
- [ ] 追加的 ConsoleLLMTracer 挂载块含 `if getattr(self.config, "llm_console_trace_enabled", False)` 判断
- [ ] 语法检查：`uv run python -c "import ast; ast.parse(open('emily-core/emily_core/__init__.py', encoding='utf-8').read()); print('syntax ok')"`
- [ ] 导入检查（不启动容器）：`uv run python -c "from emily_core import EmilyCore; print('import ok')"`

---

## M3：环境变量 + 重启验证

### M3.1 docker-compose 加环境变量

在 [docker-compose-napcat.yml:37-54](docker-compose-napcat.yml#L37-L54) emily-core service 的 `environment` 段，`NAPCAT_WEBUI_TOKEN` 那行之后追加：

```yaml
      - EMILY_LLM_CONSOLE_TRACE_ENABLED=1   # LLM 通讯控制台实时追踪（开发调试用，生产注释掉）
```

### M3.2 重启 emily-core + 清缓存

```powershell
# 清 __pycache__（bind-mount 不自动刷新，改了代码必须清）
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +

# 重启 emily-core 使环境变量 + 代码生效
docker compose -f docker-compose-napcat.yml restart emily-core

# 等待启动
Start-Sleep -Seconds 8

# 确认 console tracer 已启用（日志应含 "LLM console tracer ENABLED (stdout)"）
docker logs --tail 30 emily-core 2>&1 | findstr "console tracer"
```

### M3.3 触发 LLM 调用并观察 trace

```powershell
# 查真实用户（实际列名是 level，不是 permission_level）
docker exec emily-postgres psql -U emily -d emily -c "SELECT id, username, level FROM users WHERE status = 'active' ORDER BY level DESC LIMIT 5;"

# 实时查看 emily-core 日志（新开一个终端窗口）
docker logs -f emily-core

# 另一个终端：用 emy-test 发一条会触发 LLM 的消息
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我创建事件：样板段放线完成" --sender "真实用户名"
```

**预期日志输出**（`docker logs -f emily-core` 中出现）：

```
┌── LLM #1 [14:23:01] ───────────────────────────────────────────────
│ Model: deepseek-chat | Type: chat_messages_json | Messages: 2 | Tools: 0
│ [system] 你是 Emily 意图识别器...
│ [user] 帮我创建事件：样板段放线完成
│ ... waiting for response ...
│ [RESPONSE #1] [14:23:02] latency=820ms, finish=stop
│ Tokens: prompt=1240, completion=86, total=1326
│ Content: {"tool": "create_event", "params": {...}}
└──────────────────────────────────────────────────────────────────
```

### M3.4 验收 M3

- [ ] `docker logs emily-core` 含 `LLM console tracer ENABLED (stdout)`（启动时）
- [ ] emy-test 发消息后，`docker logs -f emily-core` 出现 `┌── LLM #N` 格式的 trace 块
- [ ] trace 块含 `[system]` + `[user]` 请求内容
- [ ] trace 块含 `[RESPONSE #N]` + `Content:` 响应内容 + `Tokens:` 用量
- [ ] emy-test CLI 收到 emily-core 的正常回复（功能不破坏）
- [ ] **DB 日志链路未破坏**：`docker exec emily-postgres psql -U emily -d emily -c "SELECT count(*) FROM evolution_llm_interaction_logs WHERE created_at > now() - interval '5 minutes';"` 数量增加（LLMInteractionLogger 仍在写 DB）

---

## 踩坑预防

| 坑 | 现象 | 预防/解决 |
|----|------|----------|
| `__pycache__` 不刷新 | 改了代码但日志没出现 console tracer | M3.2 已含清缓存命令；bind-mount 不触发 Python 重编译，每次改代码必须清 |
| `set_trace_callback` 覆盖 DB 日志 | `evolution_llm_interaction_logs` 表不再写入 | M2.1 已改为 `add_trace_callback`（追加，不清空） |
| Config 字段名拼错 | `getattr` fallback 到 False，开关不生效 | 字段名必须与 `bootstrap.py:70` bool_fields 里的 `llm_console_trace_enabled` 完全一致 |
| env_map 漏映射 | 环境变量设了但 Config 读到 False | M1.2 必须在 env_map 加 `EMILY_LLM_CONSOLE_TRACE_ENABLED` 映射条目 |
| ConsoleLLMTracer 截断长 prompt | `max_content_length=2000` 默认截断，长 system prompt 看不全 | 可选优化：挂载前设 `ConsoleLLMTracer.max_content_length = 0`（不截断），见后续章节 |
| Windows PowerShell GBK 乱码 | docker logs 中文乱码 | `$env:PYTHONIOENCODING="utf-8"` |
| users 表列名陷阱 | `SELECT permission_level FROM users` 报错 | 实际列名是 `level`；CLAUDE.md 里用 `permission_level` 的命令已过期 |
| 生产环境误开 trace | 日志量暴增，长 prompt 撑爆日志卷 | 生产环境 docker-compose 注释掉 `EMILY_LLM_CONSOLE_TRACE_ENABLED` 那行 + 重启 |

---

## 可选优化（非本次范围，仅记录）

### 优化 1：不截断长内容

[console_tracer.py:31](emily-core/emily_core/infrastructure/llm/console_tracer.py#L31) `max_content_length: int = 2000` 会截断超长 prompt/响应。若要看全文，在 M2.1 挂载块里追加一行（不改 console_tracer.py）：

```python
                    from .infrastructure.llm.console_tracer import ConsoleLLMTracer
                    ConsoleLLMTracer.max_content_length = 0   # 0 = 不截断（可选）
                    self._llm_client.add_trace_callback(ConsoleLLMTracer.make_callback())
```

### 优化 2：独立 trace 文件（与业务日志分离）

若觉得 trace 输出混在 `docker logs` 里太吵，可写一个 `FileLLMTracer`（参照 `ConsoleLLMTracer` 结构），输出到 `emily-data/logs/llm_trace.log`（该目录已挂载到容器 `/app/logs`），`Get-Content -Wait` 或 `tail -f` 实时看。约 30 行代码。此为后续增强，不在本次计划内。

---

## 回滚方案

若需关闭 console trace：

1. 编辑 `docker-compose-napcat.yml`，注释或删除 `EMILY_LLM_CONSOLE_TRACE_ENABLED=1` 那行
2. `docker compose -f docker-compose-napcat.yml restart emily-core`
3. 验证：`docker logs emily-core` 不再含 `LLM console tracer ENABLED`，且发消息后无 `┌── LLM #N` trace 块

代码改动（Config + bootstrap + __init__）可保留——不设环境变量时 `llm_console_trace_enabled` 默认 False，无副作用。无需回滚代码。

---

## 最终验收检查表

- [ ] `config.py` 含 `llm_console_trace_enabled: bool = False` 字段
- [ ] `bootstrap.py` env_map 含 `EMILY_LLM_CONSOLE_TRACE_ENABLED` 映射
- [ ] `__init__.py` 用 `add_trace_callback`（非 `set_trace_callback`）
- [ ] `__init__.py` 追加 ConsoleLLMTracer 挂载块（含 config 判断）
- [ ] `docker-compose-napcat.yml` emily-core environment 含 `EMILY_LLM_CONSOLE_TRACE_ENABLED=1`
- [ ] 重启后 `docker logs emily-core` 含 `LLM console tracer ENABLED (stdout)`
- [ ] emy-test 发消息后日志出现完整 LLM trace 块（请求 + 响应 + token）
- [ ] emily-core 正常回复（功能不破坏）
- [ ] `evolution_llm_interaction_logs` 表仍在写入（DB 日志链路未破坏）
- [ ] 回滚验证：注释环境变量 + 重启后 trace 消失

---

## 旧计划文件处理建议

[LLM流量代理抓包_计划_V1.md](LLM流量代理抓包_计划_V1.md)（代理方案）已被本计划替代。建议：

- **选项 A（推荐）**：删除该文件——代理方案从未实施，无历史价值
- **选项 B**：移动到 `需求/已完成需求文件/` 并在文件顶部加注 `> 已废弃：改用应用层 trace 方案，见 LLM通讯实时追踪_计划_V1.md`

由项目负责人决定，不影响本计划执行。
