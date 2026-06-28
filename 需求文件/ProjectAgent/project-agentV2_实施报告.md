# EmilyShell 实施报告 v2.0

> **需求来源**：[project-agent补充.md](project-agent补充.md) v1.0  
> **实施日期**：2026-06-28（v1.0 NLU 方案） → 2026-06-28（v2.0 LLM 对话方案，架构回退重做）  
> **实施者**：AI (Claude Code)  

---

## 0. 架构变更说明

### v1.0（已废弃）
v1.0 基于 **NLU 关键词匹配 + 硬编码命令分发** 的方案。用户纠正：这不是需要的——需要的只是一个能与 ProjectAgent 直接对话的简单终端窗口，运维功能不应当硬编码成命令，而应做成 LLM 可自主调用的 AI 友好工具。

### v2.0（当前版本）
**LLM 对话 REPL + Function Calling 工具集**：
- Shell = 对话终端，用户输入自然语言 → DeepSeek API → 回复
- LLM 自主决定何时调用哪个运维工具（function calling）
- 单次 REPL 内对话记忆
- 运维功能 = AI 友好的静态工具函数，LLM 按需取用

---

## 1. 实施概要

EmilyShell v2.0：基于 Python `cmd.Cmd` 的 **LLM 对话终端**，支持 **DeepSeek function calling** 自主调用 6 个运维工具。**3 个新建文件 + 5 个修改文件（含回退 v1.0 审计变更）+ 4 个删除文件**。

## 2. 产物清单

### 2.1 新建文件 (3 个，v2.0 新写)

| 文件 | 行数 | 说明 |
|------|------|------|
| `emily-core/emily_core/project/agent_shell/shell.py` | ~200 | **ProjectAgentShell(cmd.Cmd)**：对话 REPL，LLM + function calling 循环，对话记忆 |
| `emily-core/emily_core/project/agent_shell/tools.py` | ~230 | **工具集**：6 个 OpenAI function-calling 定义 + ToolExecutor 分派器 |
| `emily-core/emily_core/project/agent_shell/__main__.py` | ~120 | 启动入口：Config/DB/LLMClient/Tools → REPL |

### 2.2 保留文件 (2 个，v1.0 遗留)

| 文件 | 状态 |
|------|------|
| `emily-core/emily_core/project/agent_shell/__init__.py` | 更新导出符号 |
| `emily-core/emily_core/project/agent_shell/formatter.py` | 保持不变 |

### 2.3 删除文件 (4 个，v1.0 产物，已移除)

| 文件 | 原因 |
|------|------|
| ~~nlu.py~~ | NLU 关键词匹配 → 改为 LLM 理解 |
| ~~deps.py~~ | ShellDependencies 容器 → 简化 |
| ~~audit.py~~ | Shell 审计日志 → 不再需要 |
| ~~commands/ (4 个文件)~~ | 硬编码命令 → 改为 LLM 工具 |

### 2.4 回退修改 (5 个文件)

| 文件 | 回退内容 |
|------|---------|
| `ops/models.py` | 移除 `OpsShellAudit` 类，docstring 6→5 张表 |
| `infrastructure/database/models.py` | 移除 `OpsShellAudit` import |
| `ops/repositories/ops_repo.py` | 移除 `save_shell_audit()` 方法，保留 `get_recent_findings()` |
| `config.py` | 移除 `shell_audit_enabled` / `shell_audit_log_dir` |
| `docs/代码文件目录.md` | 更新 agent_shell 条目（12→5） |

### 2.5 容器快捷别名

| 文件 | 说明 |
|------|------|
| `/usr/local/bin/emily`（容器内脚本） | `emily` 命令 → 等价于 `python -m emily_core.project.agent_shell` |

创建方式：`docker exec emily-core bash -c "echo '...' > /usr/local/bin/emily && chmod +x /usr/local/bin/emily"`

### 2.6 删除的 SQL 迁移

| 文件 | 原因 |
|------|------|
| ~~004_create_shell_audit_table.sql~~ | ops_shell_audit 表不再需要 |

---

## 3. 功能验证

### 3.1 基础对话

```
[agent] > 你好，介绍一下自己
你好！我是 Emily，你的企业项目管理 AI 助手，可以帮你查询项目状态、
排查卡滞节点、查看里程碑预警、生成周报等。✅
```

### 3.2 工具自主调用（LLM 决定何时调哪个工具）

| 用户输入 | LLM 自动调用的工具 | 结果 |
|---------|-------------------|------|
| "锦绣花园项目进度怎么样？" | `query_project_status` | ✅ LLM 调用工具 → 工具返回"暂无节点数据" → LLM 友好解释 |
| "显示系统信息" | `show_system_info` | ✅ LLM 调用工具 → 返回模型/DB/节点数 → LLM 格式化输出 |
| "生成一份项目周报" | `generate_weekly_report` | ✅ LLM 调用工具 → 工具检测到无数据 → LLM 解释原因 |

### 3.3 6 个工具

| 工具名 | 功能 | 状态 |
|-------|------|------|
| `query_project_status` | 查询项目整体状态（节点分布/阶段/里程碑） | ✅ |
| `list_stale_nodes` | 列出卡滞节点（可指定天数阈值） | ✅ |
| `list_milestone_alerts` | 列出即将到期里程碑（可指定预警天数） | ✅ |
| `list_recent_findings` | 查看最近 N 条探针发现问题 | ✅ |
| `generate_weekly_report` | 生成 Markdown 周报并保存到 logs/ | ✅ |
| `show_system_info` | 显示 LLM/DB/节点等运行信息 | ✅ |

### 3.4 `emily` 快捷别名

```
$ docker exec emily-core emily -c "你好，一句话自我介绍"
你好！我是 Emily，你的企业项目管理 AI 助手...✅
```

### 3.5 内置命令

| 命令 | 功能 | 状态 |
|------|------|------|
| `!help` / `!h` | 显示帮助 | ✅ |
| `!clear` / `!reset` | 清空对话记忆 | ✅ |
| `!history` / `!hist` | 查看对话历史 | ✅ |
| `exit` / `quit` / `q` | 退出 | ✅ |

---

## 4. 架构对比

| 维度 | v1.0（废弃） | v2.0（当前） |
|------|-------------|-------------|
| 意图理解 | NLU 关键词匹配 | LLM（DeepSeek）自主理解 |
| 命令分发 | 硬编码 if/elif 树 | LLM function calling |
| 运维操作 | 4 类 10 个硬编码命令 | 6 个 AI 友好工具，LLM 按需调用 |
| 对话记忆 | 无 | 单次 REPL 内完整上下文 |
| 审计 | DB + 本地文件双写 | 无（终端直接操作，无需审计） |
| 代码量 | ~1100 行（12 文件） | ~550 行（5 文件） |
| 依赖 | stdlib only | stdlib + 已有 LLMClient |

---

## 5. 使用方式

```bash
# 交互 REPL 模式（需要 -it）—— 推荐用别名
docker exec -it emily-core emily

# 等价完整命令
docker exec -it emily-core python -m emily_core.project.agent_shell

# 单命令模式
docker exec emily-core emily -c "项目进度怎么样？"
docker exec emily-core emily -c "列出卡滞节点"
docker exec emily-core emily -c "生成周报"

# 查看帮助
docker exec emily-core emily -c "!help"
```

---

*报告生成时间：2026-06-28T08:05 UTC*
