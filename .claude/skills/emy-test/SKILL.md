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
├── cli.py                  ← CLI 主入口（argparse / demo / REPL）
└── emy_web/
    └── app.py              ← Gradio Web UI（直连 emily-core，供人工手动测试）
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

需要可视化交互                → Web 模式（emy_web/app.py，直连 emily-core）
交互式调试                    → REPL 模式（-i，同一进程内持续对话）
```

---

## 主要工作流

### 单轮快速测试

```powershell
# 简单问候
python .claude/skills/emy-test/emys_tester.py --managed --message "你好" --sender "Alice" --sender-id "alice"

# 查询类
python .claude/skills/emy-test/emys_tester.py --managed --message "今天有什么任务？" --sender "张工" --sender-id "zhang"

# 不带 --managed 时私聊自动接管
python .claude/skills/emy-test/emys_tester.py --message "你好" --sender "Alice" --sender-id "alice"
```

### 多轮确认测试（核心场景）

emily-core 服务端通过 `SessionPoolManager` 管理会话状态。同一 `sender_id` 的消息路由到同一 Session，保持上下文。

```powershell
# 第 1 轮：创建事件
python .claude/skills/emy-test/emys_tester.py --managed --message "帮我创建事件：样板段放线完成，时间是今天下午3点" --sender "张工" --sender-id "zhang_gong"

# 第 2 轮：确认录入（同一 sender-id → 同一 Session → 确认流程触发）
python .claude/skills/emy-test/emys_tester.py --managed --message "确认" --sender "张工" --sender-id "zhang_gong"

# 第 3 轮：查询验证
python .claude/skills/emy-test/emys_tester.py --managed --message "查一下刚才记录的事件" --sender "张工" --sender-id "zhang_gong"
```

**注意**：
- `--sender-id` 是关键！emily-core 用它确定 Session 归属
- 也可以用 `--cid` 显式指定会话 ID

### 群聊模拟（多人协作）

```powershell
# 王工在项目群中录入事件
python .claude/skills/emy-test/emys_tester.py --managed --message "帮我创建事件：B标段协调会完成，今天上午10点" --sender "王工" --sender-id "wang_gong" --cid "project_x"

# 李工在同一群中确认
python .claude/skills/emy-test/emys_tester.py --managed --message "确认" --sender "李工" --sender-id "li_gong" --cid "project_x"
```

### REPL 交互模式

```powershell
python .claude/skills/emy-test/emys_tester.py -i
```

### 文件附件测试

```powershell
# 发送文件
python .claude/skills/emy-test/emys_tester.py --managed --message "看看这个图纸" --file "D:\drawings\plan.dwg" --sender "张工"

# 发送多个文件
python .claude/skills/emy-test/emys_tester.py --managed --message "这是相关文件" --file "D:\a.pdf" --file "D:\b.png" --sender "张工"
```

---

## CLI 参数速查

| 参数 | 作用 |
|------|------|
| `--message "..."` | 发送单条消息 |
| `--sender "张工"` | 发送者显示名称 |
| `--sender-id "zhang"` | 发送者稳定 ID（决定 Session 归属！） |
| `--cid "conv_01"` | 显式会话 ID（覆盖 sender-id 推导） |
| `--file "D:\path"` | 附件文件路径（可多次指定） |
| `--managed` | 接管所有消息 |
| `--llm` | 启用 LLM 模式（保留向后兼容） |
| `-i` / `--interactive` | 交互式 REPL 模式 |

### Web UI 对话导出

Web 控制台左侧边栏「📥 对话记录」区域支持将当前对话导出到本地目录：

1. 在 **保存目录** 输入框中填写目标路径（默认为系统下载目录）
2. 选择 **导出格式**：`markdown` / `json` / `txt`
3. 点击 **📥 下载对话记录** 按钮
4. 文件以 `emily_conversation_{timestamp}.{ext}` 命名保存

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