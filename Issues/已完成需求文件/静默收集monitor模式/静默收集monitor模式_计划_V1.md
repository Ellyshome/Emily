# 静默收集 Monitor 模式 — AI 执行计划

> **基于需求**：用户对话讨论（2026-07-26）
> **计划版本**：V1.0
> **目标**：新增 `monitor` 接管模式，使 Emily IM 账号在群聊中静默收集所有消息与文件，仅被 @ 提及时才进入响应模式，实现"默认静默、被动应答"的群聊行为。

---

## 你的角色

你作为 **Emily开发者资深架构师** + **实施计划编制专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：`DomainTakeoverService.decide()` 签名不变，`EmilyCore.handle_message()` 签名不变，`RouteDecision` 字段不删不改。仅在现有方法体新增分支，不在已有类中修改方法签名
2. **业务内核独立**：`emily_core` 不改动任何 `astrbot.*` 相关代码，不 import AstrBot 包
3. **分层不可跳**：改动严格限定在 Config → DomainTakeoverService → EmilyCore 链路，不跨层
4. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
5. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件。风格不一致视为失败

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `RouteDecision` | `emily-core/emily_core/adapters/standard/route_decision.py` | `takeover`, `should_reply`, `mode`, `reason` 字段 | `should_reply=False` 控制静默不回复 |
| `DomainTakeoverService` | `emily-core/emily_core/services/domain_takeover_service.py` | `decide(message) -> RouteDecision` | 新增 `monitor` 分支，其他分支不动 |
| `EmilyCore.handle_message` | `emily-core/emily_core/__init__.py` L917 | async 消息入口，含持久化→附件→路由→回复全链路 | 在 SessionPool 路由前插入 `should_reply` 检查 |
| `Config.takeover_mode` | `emily-core/emily_core/config.py` L16 | `str = "collaborate"` | 默认值改为 `"monitor"` |

### 架构决策

直接在现有 `DomainTakeoverService.decide()` 中新增 `monitor` 分支，而非新建类或策略模式。理由：(1) 改动量最小（一个 if 分支）；(2) 与现有 `observe/collaborate/managed` 三模式平级，符合现有设计模式；(3) `handle_message` 中仅需一个 early return 检查 `should_reply`，不需要新增参数或 Service。替代方案（新建 MonitorService 独立模块）被否决——功能太简单，不配独立模块。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| Config dataclass | `emily-core/emily_core/config.py` | `str = "xxx"` + 中文 docstring 风格 |
| Service | `emily-core/emily_core/services/domain_takeover_service.py` | 纯函数风格 `decide()`，logger.info/debug 分级 |
| Core 入口 | `emily-core/emily_core/__init__.py` L917-L1020 | async def + early return 模式 + logger.info |
| DTO (plugin 副本) | `data/plugins/emily_agent/adapters/standard/route_decision.py` | 与 Core 端保持字段一致，docstring 同步 |

---

## 模块依赖图

```
M1(RouteDecision DTO 文档更新) ──┐
                                 ├──→ M3(handle_message 静默检查)
M2(DomainTakeoverService)   ─────┘
                                 │
M4(Config 默认值)               (无依赖,可与 M1-M3 并行)
                                 │
M5(接口协议文档更新) ────────────┘ (依赖全部代码改动完成)
```

M1 和 M2 无代码依赖但逻辑相关，建议先 M1→M2→M3 顺序执行；M4 独立可并行；M5 最后。

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心改动 |
|------|----------|----------|----------|
| M1 | `emily-core/emily_core/adapters/standard/route_decision.py` | 修改 | 更新 `mode` 和 `should_reply` 的 docstring |
| M1 | `data/plugins/emily_agent/adapters/standard/route_decision.py` | 修改 | 同上，插件端 DTO 副本同步更新 |
| M2 | `emily-core/emily_core/services/domain_takeover_service.py` | 修改 | 新增 `monitor` 分支（约 25 行） |
| M3 | `emily-core/emily_core/__init__.py` | 修改 | `handle_message` 中 SessionPool 路由前插入 `should_reply` 检查（约 7 行） |
| M4 | `emily-core/emily_core/config.py` | 修改 | 默认值 `"collaborate"` → `"monitor"`，更新注释 |
| M5 | `docs/接口协议与调用约定.md` | 修改 | `takeover_mode` 文档从 3 种模式扩展到 4 种 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/services/domain_takeover_service.py` | 修改 | 在 `managed` 分支前插入 `monitor` 分支；更新类 docstring |
| `emily-core/emily_core/__init__.py` | 扩展 | `handle_message` 方法内，附件下载后、SessionPool 路由前插入静默检查 |
| `emily-core/emily_core/config.py` | 修改 | `takeover_mode` 默认值 + docstring |
| `emily-core/emily_core/adapters/standard/route_decision.py` | 修改 | docstring 更新 |
| `data/plugins/emily_agent/adapters/standard/route_decision.py` | 修改 | docstring 更新（插件端 DTO 副本） |
| `docs/接口协议与调用约定.md` | 修改 | 接口协议文档更新 |
| `emily-core/emily_core/workitem/pipeline/interfaces/execution.py` | 不变 | — |
| `emily-core/emily_core/workitem/pipeline/context.py` | 不变 | — |
| `emily-core/emily_core/workitem/pipeline/interfaces/routing.py` | 不变 | — |
| `emily-core/emily_core/bootstrap.py` | 不变 | — |
| `data/plugins/emily_agent/main.py` | 不变 | — |
| `emily-data/config/core_config.json` | 不变 | 若已有显式配置则需手工改为 `"monitor"` |

---

## M1: RouteDecision DTO 文档更新

**依赖**：无

**职责**：更新 Core 端和插件端两份 `RouteDecision` 数据类的 docstring，反映新增的 `monitor` 模式。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Core 端 DTO 文档 | `emily-core/emily_core/adapters/standard/route_decision.py` |
| 2 | 插件端 DTO 副本同步 | `data/plugins/emily_agent/adapters/standard/route_decision.py` |

### 代码

#### `emily-core/emily_core/adapters/standard/route_decision.py` — 修改第 18 行和第 30 行

```python
# emily-core/emily_core/adapters/standard/route_decision.py

# 修改 mode 字段 docstring：
    mode: str = "collaborate"
    """接管模式: observe / collaborate / managed / monitor"""

# 修改 should_reply 字段 docstring：
    should_reply: bool = True
    """是否需要回复。observe 和 monitor(非@) 模式下为 False"""
```

#### `data/plugins/emily_agent/adapters/standard/route_decision.py` — 同上，完全一致的修改

```python
# data/plugins/emily_agent/adapters/standard/route_decision.py

# 修改 mode 字段 docstring：
    mode: str = "collaborate"
    """接管模式: observe / collaborate / managed / monitor"""

# 修改 should_reply 字段 docstring：
    should_reply: bool = True
    """是否需要回复。observe 和 monitor(非@) 模式下为 False"""
```

### 模块验收检测

```bash
# 验收 1：确认 Core 端 DTO docstring 包含 monitor
grep -n "monitor" emily-core/emily_core/adapters/standard/route_decision.py
→ 预期输出：第 18 行和第 30 行各出现 "monitor"

# 验收 2：确认插件端 DTO 副本同步
grep -n "monitor" data/plugins/emily_agent/adapters/standard/route_decision.py
→ 预期输出：同上，两处均含 "monitor"
```

**失败处理**：如果只在一处出现，检查并补改另一份文件。

---

## M2: DomainTakeoverService 新增 monitor 分支

**依赖**：M1（仅逻辑相关，无代码依赖）

**职责**：在 `DomainTakeoverService.decide()` 中新增 `monitor` 模式分支，实现"群聊全量接管但仅 @ 时回复"的判断逻辑。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | DomainTakeoverService | `emily-core/emily_core/services/domain_takeover_service.py` |

### 代码

#### `emily-core/emily_core/services/domain_takeover_service.py` — 3 处修改

**修改 1**：类 docstring（第 16-22 行），追加 monitor 模式说明

```python
# emily-core/emily_core/services/domain_takeover_service.py

    """判断当前消息是否应由 Emily 接管。

    M1 规则:
        - 群聊中 @了机器人 → takeover=true
        - 私聊消息 → takeover=true
        - 观察模式 → takeover=false, 不回复 (仅在此模式)
        - 监控模式 → 全量接管但仅 @ 时回复（静默收集群聊消息与文件）
        - 其他 → takeover=false
    """
```

**修改 2**：在 `managed` 分支之前（第 43 行前），插入 `monitor` 分支

```python
# emily-core/emily_core/services/domain_takeover_service.py

        # 监控模式：全量接管，但仅 @ 时回复（静默收集群聊消息与文件）
        if mode == "monitor":
            if message.conversation_type == "private":
                logger.info("takeover=true, reason=monitor_private")
                return RouteDecision(
                    takeover=True,
                    mode=mode,
                    should_reply=True,
                    reason="monitor_private",
                )
            if message.is_at_bot:
                logger.info("takeover=true, reason=monitor_at_bot")
                return RouteDecision(
                    takeover=True,
                    mode=mode,
                    should_reply=True,
                    reason="monitor_at_bot",
                )
            logger.info("takeover=true, reason=monitor_silent_collect")
            return RouteDecision(
                takeover=True,
                mode=mode,
                should_reply=False,
                reason="monitor_silent_collect",
            )
```

**修改 3**：兜底注释更新（第 72-78 行）

```python
# emily-core/emily_core/services/domain_takeover_service.py

        # 群聊未 @机器人 → 放行（collaborate / observe 等模式的兜底）
        logger.debug("takeover=false, reason=group_message_not_at_bot")
        return RouteDecision(
            takeover=False,
            mode=mode,
            should_reply=False,
            reason="group_message_not_at_bot",
        )
```

### 模块验收检测

```bash
# 验收 1：确认 monitor 分支存在且结构正确
grep -c "mode == \"monitor\"" emily-core/emily_core/services/domain_takeover_service.py
→ 预期输出：1（恰好一个 monitor 分支入口）

# 验收 2：确认三个子分支（私聊/@/静默）均存在
grep -c "should_reply" emily-core/emily_core/services/domain_takeover_service.py
→ 预期输出：5（observe 1处 + monitor 3处 + 兜底 1处）

# 验收 3：确认 monitor 分支在前，managed 在后（插入位置正确）
grep -n "monitor\|managed" emily-core/emily_core/services/domain_takeover_service.py
→ 预期输出：monitor 行号 < managed 行号

# 验收 4：Python 语法检查
uv run python -c "import ast; ast.parse(open('emily-core/emily_core/services/domain_takeover_service.py').read()); print('OK')"
→ 预期输出：OK
```

**失败处理**：验收 1/2 不通过 → 检查插入位置和代码完整性；验收 3 不通过 → 调整分支顺序；验收 4 不通过 → 检查语法。

---

## M3: EmilyCore.handle_message 静默收集检查

**依赖**：M2（依赖 `decision.should_reply` 字段）

**职责**：在 `handle_message` 中，消息持久化 + 附件下载完成后，检查 `decision.should_reply`。若为 False，跳过 SessionPool 路由和出站回复，静默返回 None。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | EmilyCore 消息入口 | `emily-core/emily_core/__init__.py` |

### 代码

#### `emily-core/emily_core/__init__.py` — 在 L998 处（附件下载后，SessionPool 路由前）插入

```python
# emily-core/emily_core/__init__.py

        # ── 静默收集：仅归档不响应，跳过流水线 ──
        if not decision.should_reply:
            logger.info(
                "Silent collect: msg persisted conv=%s sender=%s",
                message.conversation_id, message.sender_name,
            )
            return None

        # SessionPool 路由（携带 db_message_id —— 见 M2）
        reply = await self._session_pool.route(message, user_id=user_id, db_message_id=db_message_id)
```

### 模块验收检测

```bash
# 验收 1：确认静默检查插入在 SessionPool 路由之前
grep -n "Silent collect\|SessionPool 路由" emily-core/emily_core/__init__.py
→ 预期输出：静默检查行号 < SessionPool 行号

# 验收 2：确认静默检查在附件下载之后
grep -n "Silent collect\|attachment_downloader\|Scheduled attachment" emily-core/emily_core/__init__.py
→ 预期输出：附件下载行号 < 静默检查行号

# 验收 3：Python 语法检查
uv run python -c "import ast; ast.parse(open('emily-core/emily_core/__init__.py').read()); print('OK')"
→ 预期输出：OK
```

**失败处理**：验收 1/2 不通过 → 调整插入位置，确保在附件下载代码块之后、`self._session_pool.route()` 调用之前

---

## M4: Config 默认值修改

**依赖**：无（可并行）

**职责**：将 `takeover_mode` 默认值从 `"collaborate"` 改为 `"monitor"`，更新 docstring。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | Config | `emily-core/emily_core/config.py` |

### 代码

#### `emily-core/emily_core/config.py` — 修改第 16-17 行

```python
# emily-core/emily_core/config.py

    takeover_mode: str = "monitor"
    """接管模式: observe / collaborate / managed / monitor
    monitor: 群聊静默收集所有消息与文件，仅 @机器人 时回复；私聊正常响应"""
```

### 模块验收检测

```bash
# 验收 1：确认默认值为 monitor
grep 'takeover_mode.*=.*"monitor"' emily-core/emily_core/config.py
→ 预期输出：一行匹配，值为 "monitor"

# 验收 2：确认 docstring 包含四种模式
grep -A1 "takeover_mode" emily-core/emily_core/config.py | grep "monitor"
→ 预期输出：docstring 中包含 monitor 说明
```

**失败处理**：检查配置值是否正确替换，docstring 是否正确

---

## M5: 接口协议文档更新

**依赖**：M1-M4 全部完成

**职责**：更新 `docs/接口协议与调用约定.md`，将 `takeover_mode` 说明从 3 种扩展到 4 种。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 接口协议文档 | `docs/接口协议与调用约定.md` |

### 代码

打开 `docs/接口协议与调用约定.md`，找到 `takeover_mode` 相关段落（当前描述 "observe / collaborate / managed" 三种模式），追加 `monitor` 模式说明：

```markdown
# docs/接口协议与调用约定.md

在 `takeover_mode` 配置说明段落中，将原本的 3 种模式扩展为 4 种：

| 模式 | 私聊 | 群聊 @bot | 群聊 非@bot | 说明 |
|------|------|-----------|-------------|------|
| `observe` | 不接管 | 不接管 | 不接管 | 完全静默，不处理任何消息 |
| `collaborate` | 接管+回复 | 接管+回复 | 不接管 | 仅响应主动 @ 的消息 |
| `monitor` | 接管+回复 | 接管+回复 | 接管不回复 | **新增** 静默收集全量消息与文件，仅 @ 时回复 |
| `managed` | 接管+回复 | 接管+回复 | 接管+回复 | 预留，接管并回复所有消息 |

### RouteDecision.should_reply

`should_reply` 字段控制接管后是否发送回复：
- `observe` 模式：始终 `False`
- `monitor` 模式：私聊和 @ 时为 `True`，其余群聊为 `False`
- `collaborate` / `managed` 模式：始终 `True`
```

### 模块验收检测

```bash
# 验收：确认文档包含 monitor 模式描述
grep -c "monitor" docs/接口协议与调用约定.md
→ 预期输出：>= 2（至少出现在模式表格和 should_reply 说明中）
```

**失败处理**：检查文档，确认新增内容准确描述了 monitor 模式的行为矩阵

---

## 组装验证

所有模块完成后，运行端到端组装验证：

```bash
# 验证 1：全链路 Python 语法检查（模拟 Core 启动）
uv run python -c "
from emily_core.config import Config
from emily_core.services.domain_takeover_service import DomainTakeoverService
from emily_core.adapters.standard.route_decision import RouteDecision
from emily_core.adapters.standard.message import StandardMessage

config = Config()
service = DomainTakeoverService(config)

# 测试：monitor 模式下群聊非 @ 消息
msg = StandardMessage(
    platform='qq',
    sender_id='test_user',
    sender_name='测试用户',
    conversation_id='group_123',
    conversation_type='group',
    is_at_bot=False,
    content='今天的天气不错',
)
decision = service.decide(msg)
assert decision.takeover == True, f'应接管但 takeover={decision.takeover}'
assert decision.should_reply == False, f'应静默但 should_reply={decision.should_reply}'
assert decision.mode == 'monitor', f'mode 应为 monitor 但={decision.mode}'

# 测试：monitor 模式下群聊 @ 消息
msg_at = StandardMessage(
    platform='qq',
    sender_id='test_user',
    sender_name='测试用户',
    conversation_id='group_123',
    conversation_type='group',
    is_at_bot=True,
    content='@Emily 今天有什么任务',
)
decision_at = service.decide(msg_at)
assert decision_at.takeover == True
assert decision_at.should_reply == True

# 测试：monitor 模式下私聊
msg_private = StandardMessage(
    platform='qq',
    sender_id='test_user',
    sender_name='测试用户',
    conversation_id='private_456',
    conversation_type='private',
    is_at_bot=False,
    content='你好',
)
decision_private = service.decide(msg_private)
assert decision_private.takeover == True
assert decision_private.should_reply == True

print('ALL ASSERTIONS PASSED')
"
→ 预期输出：ALL ASSERTIONS PASSED

# 验证 2：确认 Docker 内 Core 正常启动
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 20 emily-core 2>&1
→ 预期输出：无 Python traceback，health check 正常

# 验证 3：emy-test 生产实战 — 群聊非 @ 消息应静默
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "今天的天气不错" --conversation-type group --sender "真实用户名"
→ 预期输出：204 No Content（静默，无回复内容）

# 验证 4：emy-test 生产实战 — 私聊正常响应
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "你好" --sender "真实用户名"
→ 预期输出：200 OK，有回复内容
```

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "V1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
