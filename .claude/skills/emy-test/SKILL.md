---
name: emy-test
description: 针对 Docker 内运行的 emily-core 容器进行生产环境实战测试。通过复用 emily_agent 插件的 EmilyApiClient（HTTP + SSE）模拟 astrbot 插件收发消息，发送模拟消息，观察回复并诊断问题。
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - TaskStop
---

# Emily Core 生产环境实战测试

你是 Emily 项目的 **AI 测试工程师**。你的任务是用 `.claude/skills/emy-test/` 下的 `emys_tester.py` 对 Docker 内运行的 emily-core 容器进行实战测试。

**关键原则**：
- 测试目标是 Docker 内已跑起来的 emily-core 容器，通过 HTTP + SSE 与之通信
- 测试方式模拟 astrbot 插件的收发通信模式 — 复用 `data/plugins/emily_agent/adapters/` 中的 `EmilyApiClient`（HTTP）和内置轻量 SSE 监听器
- 默认测试环境就是生产环境，测试数据直接写入生产 PostgreSQL 数据库
- 不搭建虚拟数据环境，不做数据隔离
- 每个 CLI 命令是独立短进程：发消息 → 等回复 → 打印 → 退出
- 会话状态由 emily-core 服务端（SessionPoolManager）管理，无需本地常驻进程

**前置条件**：emily-core 容器必须已启动（`docker-compose up -d emily-core`），配置从 `.env` 文件读取 `EMILY_*` 变量。

## 文件结构（AI 优先架构）

```
.claude/skills/emy-test/
├── SKILL.md               ← 本文件
├── emys_tester.py          ← CLI 入口（薄 shim，向后兼容）
├── config_loader.py        ← 配置加载（.env / EMILY_* 环境变量 / PG）
├── tester.py               ← EmysTester 核心类（构建消息 → HTTP+SSE → 返回回复）
└── cli.py                  ← CLI 主入口（argparse / demo / REPL）
```

「可视化交互」不再是本 skill 内的独立前端（原 Gradio 控制台已移除），而是把同一套
HTTP+SSE 收发能力封装成一个脚本条目，以「特殊工具」形态融入 emily-core 的脚本控制台：

```
scripts/emytest_chat.py     ← 消息模拟器（注册于 emily-data/config/scripts_registry.yaml）
static/scripts_tool/        ← emily-core 脚本控制台前端，挂载在 /console/
```

**关键架构决策**：
- **复用 emily_agent 插件的 `EmilyApiClient`** → 与 astrbot 插件行为完全一致，不重复造轮子
- **内置轻量 SSE 监听器** → 捕获 reply / progress / file_send 事件，与插件 SSE 协议一致
- **直接 PG 连接** → 仅用于 `get_messages()` 和 `get_users()` 诊断查询（API 暂无对应端点）

## 核心概念：同步回复 vs 异步回复

emily-core HTTP API 的 `POST /api/v1/message/send` 返回两种结果：
- **200 OK（同步回复）**：短路回复（如问候/确认），返回 `ReplyMessage` JSON，立即可用
- **204 No Content（异步处理）**：Agent 多轮推理中，结果通过 SSE `reply` 事件推送

`EmysTester.send_message()` 自动处理两种路径：
1. HTTP 返回 200 → 立即返回 reply
2. HTTP 返回 204 → 等待 SSE `reply` 事件（超时 120s）→ 返回 reply

## 测试模式选择

```
几乎所有场景                 → CLI 模式（简单、无状态）
  ├─ 单轮验证（问候/查询）    → 直接 `--message` CLI 命令
  ├─ 多轮确认（CRUD 流程）    → 连续 `--message`，同 `--sender-id` 保持上下文（服务端管理）
  └─ 群聊多人协作            → 不同 `--sender-id` + 相同 `--cid`

需要可视化交互                → 脚本控制台（emily-core 的 /console/「消息模拟器」，见下节）
交互式调试                    → REPL 模式（-i，同一进程内持续对话）
```

---

## 主要工作流

### 单轮快速测试

```powershell
# 方式一（推荐）：指定用户名，自动从 users 表查找
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "你好" --sender "李明华"

# 方式二：指定 QQ 号作为 sender_id（与 AstrBot 行为一致）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "你好" --qq "123456789"

# 方式三：不指定任何用户，交互式选择
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "你好"
# → 自动枚举 users 表，提示选择用户
```

> **推荐方式一**：`--sender` 传入用户名，CLI 自动从 users 表查找该用户，
> 提取其 QQ 号作为 `sender_id`，`platform` 固定为 `"napcat"`，
> 消息结构与 AstrBot 真实插件完全一致。

### 多轮确认测试（核心场景）

emily-core 服务端通过 `SessionPoolManager` 管理会话状态。同一 `sender_id` 的消息路由到同一 Session，保持上下文。

```powershell
# 第 1 轮：创建事件（用 --sender 指定用户名，自动提取 QQ 号）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件：样板段放线完成，时间是今天下午3点" \
  --sender "李明华"

# 第 2 轮：确认录入（同一 sender → 同一 QQ 号 → 同一 Session → 确认流程触发）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "确认" \
  --sender "李明华"

# 第 3 轮：查询验证
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "查一下刚才记录的事件" \
  --sender "李明华"
```

**注意**：
- `sender_id`（QQ 号）是关键！emily-core 用它确定 Session 归属和用户绑定
- `--sender` 传入用户名后，CLI 自动从 users 表提取 QQ 号作为 `sender_id`
- 也可以用 `--cid` 显式指定会话 ID

### ⚠️ 强制规则：必须使用 users 表中真实存在的用户！

**【踩坑固化】不能随便编一个 `--sender-id` 或 `--qq`！** Session 构建时会查询 `users` 表加载权限数据，假用户会导致：

1. **PermissionSnapshot 全为空** → 权限校验永远走 fallback 访客路径
2. **自动创建用户** → 系统会为不存在的 sender 自动 INSERT 到 users 表（permission_level=1），污染生产数据
3. **业务逻辑无法触发** → 依赖公司/部门/节点范围的业务全部失效
4. **测试结果完全不可信** → 你测试的是"未登录访客"的边缘降级路径，不是真实生产场景

**推荐方式：直接用 `--sender` 传入用户名，CLI 自动解析。**

也可以手动查询 users 表获取 QQ 号：

```powershell
# 查询可用用户（含 QQ 号）
docker exec emily-postgres psql -U emily -d emily -c "
SELECT u.id, u.username, u.qq, u.level, c.company_name
FROM users u
LEFT JOIN company_info c ON u.company = c.id
WHERE u.is_deleted = false AND u.status = 'active'
ORDER BY u.permission_level DESC LIMIT 10;
"

# 用 QQ 号测试（与 AstrBot 行为完全一致）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件..." --qq "123456789"
```

**正确的测试命令示例：**

```powershell
# ✅ 推荐：--sender 传入用户名，自动解析 QQ 号
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件..." --sender "李明华"

# ✅ 也可以：直接用 QQ 号（与 AstrBot 行为完全一致）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件..." --qq "123456789"

# ✅ 也可以：用 --sender-id 传 UUID（走 UUID 直查路径，需存在于 users 表）
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件..." --sender-id "b2c3d4e5-f6a7-4b6c-9d0e-1f2a3b4c5d6e"

# ❌ 错误：随便编一个不存在的 ID
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件..." --qq "fake_qq"    # ← 不在 user_im_bindings 中！
```

### 群聊模拟（多人协作）

```powershell
# 王工在项目群中录入事件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "帮我创建事件：B标段协调会完成，今天上午10点" \
  --sender "王工" --cid "project_x"

# 李工在同一群中确认
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "确认" \
  --sender "李明华" --cid "project_x"
```

### REPL 交互模式

```powershell
python .claude/skills/emy-test/emys_tester.py -i
```

### 文件附件测试

```powershell
# 发送文件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "看看这个图纸" --file "D:\drawings\plan.dwg" \
  --sender "{真实用户名}" --sender-id "{真实用户UUID}"

# 发送多个文件
uv run python .claude/skills/emy-test/cli.py --managed --llm \
  --message "这是相关文件" --file "D:\a.pdf" --file "D:\b.png" \
  --sender "{真实用户名}" --sender-id "{真实用户UUID}"
```

> ⚠️ **跨操作系统文件 URL 陷阱**
> `--file` 参数使用 `Path.as_uri()` 生成 `file:///D:/temp/xxx.txt` 格式的 URL。
> 这个 URL 会写入 `StandardMessage.attachments[].url` 并传入 Docker 容器，
> 但 **容器是 Linux 环境，无法解析 Windows 路径**（`D:\` 不存在），
> 导致 `AttachmentDownloader` / `FileStorageService` 下载文件时失败。
>
> **解决方案**：将测试文件放入 Docker volume 挂载目录（`emily-data/attachments/`），
> 容器内即可通过 `file:///app/attachments/xxx.txt` 访问。
>
> 对于已有 mock 文件（由 `setup_test_env.ps1` 生成），路径天然可访问：
> ```
> 宿主机：emily-data/attachments/mock/EMERALD-01/...
>    ↕ volume 挂载 (docker-compose-napcat.yml L77)
> 容器内：/app/attachments/mock/EMERALD-01/...
> ```
> `storage_path` 在 DB 中已是相对路径，`FileStorageService.get_local_path()` 会拼出容器内绝对路径，
> send_file 等功能可直接使用。
>
> **测试文件放置建议**：
> - 测试附件自动下载 → 文件放 `emily-data/attachments/`，消息中传容器内 `file:///app/attachments/...`
> - 测试 send_file / 权限校验 → 先跑 `setup_test_env.ps1` 生成 mock 文件（18 个空文件 + storage_path 修复）
> - 不要依赖 `--file` 生成的 Windows 绝对路径 URL 在容器侧生效

---

## CLI 参数速查

| 参数 | 作用 |
|------|------|
| `--message "..."` | 发送单条消息 |
| `--sender "李明华"` | 发送者用户名（从 users 表自动查找，提取 QQ 号作为 sender_id）**推荐** |
| `--qq "123456789"` | 发送者 QQ 号（直接作为 sender_id，与 AstrBot 行为完全一致） |
| `--sender-id "UUID"` | 发送者 UUID（走 Core UUID 直查路径，需在 users 表中存在） |
| `--cid "conv_01"` | 显式会话 ID（覆盖自动推导） |
| `--file "D:\path"` | 附件文件路径（可多次指定） |
| `--managed` | 接管所有消息 |
| `--llm` | 启用 LLM 模式（保留向后兼容） |
| `-i` / `--interactive` | 交互式 REPL 模式 |

### 脚本控制台「消息模拟器」（可视化交互）

原 Gradio 控制台已移除，同一能力改以**脚本条目**形态融入 emily-core 脚本控制台：

```text
http://localhost:18080/console/
```

左侧列表选 `emytest_chat`（显示为「消息模拟器」），右侧自动渲染表单：

| 表单项 | 说明 |
|--------|------|
| 消息内容 | 要发送的文本（必填） |
| 发送者 | 下拉选择 users 表中的**真实用户**（必填，来自 `options_source: users`） |
| 平台 | simulator / napcat / wechat / dingtalk / feishu（单选） |
| 会话类型 | private / group（单选，私聊 conversation_id = 发送者，群聊 = 群号） |
| 群号 | 仅群聊时生效 |
| @机器人 | 复选框，仅群聊时生效（不勾选可验证「不接管」路径） |
| 等待回复超时 | 10~280 秒，默认 120 |
| 仅预览 | 复选框，只打印将发送的报文与目标地址 |

该条目声明了 `writes_db: true`：点「执行」先弹二次确认框，确认后才真正发送；
未确认时只跑 `--dry-run` 预览。**同一发送者连续执行即可完成多轮确认类测试**
（会话上下文由 Core 按 sender_id 维持）。结果面板会回显 HTTP 状态、SSE 前导消息、
send_file 事件与 Emily 的回复。

CLI 等价用法（在容器内执行，与脚本控制台同一环境）：

```powershell
docker exec emily-core python /app/scripts/emytest_chat.py \
  --message "你好" --sender "<users 表 UUID 或用户名>"

# 仅预览，不实际发送
docker exec emily-core python /app/scripts/emytest_chat.py \
  --message "你好" --sender "<UUID>" --dry-run

# 群聊（@机器人）
docker exec emily-core python /app/scripts/emytest_chat.py \
  --message "帮我创建事件..." --sender "<UUID>" \
  --conversation-type group --group-id project_x --at-bot
```

> ⚠️ 该脚本发送前会校验发送者在 `users` 表中存在，避免伪造 sender_id 触发 Core
> 自动创建 level=1 访客用户、污染生产库。

## 配置来源

配置从 `.env` 文件和系统环境变量加载 `EMILY_*` 前缀的变量：

| 变量 | 作用 | 默认值 |
|------|------|--------|
| `EMILY_CORE_URL` | emily-core 容器地址 | `http://localhost:18080` |
| `EMILY_DATABASE_URL` | PostgreSQL 连接 URL | `postgresql://emily:emily_secret_2026@localhost:25432/emily` |
| `EMILY_API_TOKEN` | API 认证 token | 空（无认证） |
| `EMILY_LLM_API_KEY` | LLM API Key | - |
| `EMILY_LLM_BASE_URL` | LLM API 地址 | `https://api.deepseek.com` |
| `EMILY_LLM_MODEL` | LLM 模型名称 | `deepseek-chat` |

```powershell
# 查看当前配置
python -c "from config_loader import get_core_url, get_llm_config; print(get_core_url()); print(get_llm_config())"
```

可选覆盖：

```powershell
$env:EMILY_CORE_URL = "http://192.168.1.100:18080"
$env:EMILY_LLM_API_KEY = "sk-xxx"
```

## 回复诊断指南

### 预期回复

| 消息类型 | 预期回复 |
|----------|---------|
| "你好" | 问候语（自我介绍或招呼） |
| "你叫什么名字" | 机器人名称 |
| "帮我创建事件..." | 事件拟录入单 + 确认提示 |
| "确认" | 确认写入成功（需同一 sender-id） |
| "今天有什么任务" | 任务列表查询 |
| @bot 消息（群聊） | 接管回复 |
| 非 @bot 消息（群聊） | 不接管（None） |

### 故障排查

| 问题 | 检查项 |
|------|--------|
| 连接超时 | `docker ps` 确认 emily-core 容器运行中 |
| 401 认证错误 | 检查 `EMILY_API_TOKEN` 是否与 core 配置一致 |
| 不接管（None） | 检查消息内容是否触发接管条件 |
| 多轮确认失败 | 确认两轮使用相同的 `--sender-id` |
| 文件发送失败 | 确认文件路径存在且可读 |
| **权限行为异常/测试结果不可信** | **检查 `--sender-id` 是否存在于 `users` 表中！假用户会被自动创建(level=1)，测试的是访客路径** |