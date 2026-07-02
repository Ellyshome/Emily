# EmilyShell - ProjectAgent 交互式终端接口设计文档

> **最后更新**：2024-06-27 | **版本**：v1.0

---

## 🎯 背景与定位

### 问题

ProjectAgent 作为项目级自主 Agent，目前的交互通道有：

| 通道 | 实时性 | 可靠性 | 依赖 | 适用场景 |
|------|--------|--------|------|---------|
| 邮箱轮询 | ⭐⭐（5分钟延迟） | ⭐⭐⭐ | IMAP 服务器 | 日常告警、非紧急命令 |
| IM 消息中转 | ⭐⭐⭐⭐ | ⭐⭐ | 第三方 IM 服务 | 用户日常交互 |

**缺少一个：零依赖、实时、本地、最高权限的紧急通道**

---

### 解决方案：EmilyShell

**EmilyShell = ProjectAgent 的交互式 Shell 接口**

本质是与 ProjectAgent 直接通讯的本地终端工具，跳过 SessionAgent/IM 中转，实现：

- ✅ **实时响应**（无 5 分钟轮询延迟）
- ✅ **零外部依赖**（不需要 IM 服务、邮件服务）
- ✅ **全权限访问**（可调用 ProjectAgent 内部所有接口）
- ✅ **双模式支持**（交互模式给人用，单命令模式给脚本用）
- ✅ **全审计留痕**（所有操作双保险记录）

---

## 📐 设计原则

| 原则 | 说明 |
|------|------|
| **零依赖** | 只用 Python 标准库，不引入任何第三方包 |
| **全权限** | 能进 Docker 就是管理员，不做二次权限校验 |
| **双模式** | 交互 REPL + 单命令执行，覆盖人机/脚本双场景 |
| **分层风险** | 命令分 4 类：查询/操作/调试/管理，风险高的需确认 |
| **审计完整** | 所有操作 DB + 本地日志双记录，缺一不可 |
| **确定性** | NLU 用关键词匹配，不用 LLM，零幻觉 |

---

## 🏗️ 整体架构

### 架构定位图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Emily 用户交互层                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │  IM 插件    │  │  Web 界面   │  │  ✅ EmilyShell (新增)   │  │
│  │  (QQ/微信)  │  │  (未来)     │  │  (本地终端，零依赖)     │  │
│  └──────┬──────┘  └──────┬──────┘  └────────────┬──────────┘  │
│         │                  │                      │             │
│         └──────────────────┼──────────────────────┘             │
│                            ↓                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    SessionAgent                            │  │
│  │                  (会话调度 + NLU)                          │  │
│  └─────────────────────────────┬─────────────────────────────┘  │
│                                │                                │
│  ┌─────────────────────────────┼─────────────────────────────┐  │
│  │                             ↓                             │  │
│  │                    WorkItemAgent                          │  │
│  │                  (任务执行引擎)                            │  │
│  └─────────────────────────────┬─────────────────────────────┘  │
│                                │                                │
│  ┌─────────────────────────────┼─────────────────────────────┐  │
│  │                             ↓                             │  │
│  │                   ProjectAgent                            │  │
│  │              ┌───────────────────────────┐                │  │
│  │              │   ops_scheduler (Tick)    │  ←─── EmilyShell │
│  │              │   运维调度器              │       直接调用   │
│  │              └───────────────────────────┘                │  │
│  │                                                             │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
```

**关键设计决策**：EmilyShell **跳过 SessionAgent，直接调用 ProjectAgent 内部接口**
- 不走消息队列路由
- 没有权限检查（本地终端 = 管理员）
- 可以访问内部状态（不经过 API 层）
- 零延迟、零依赖、100% 可靠

---

## 📁 目录结构

```
emily_core/project/
├── agent_shell/              ✅ 新增目录
│   ├── __init__.py
│   ├── shell.py              # REPL 主入口 + cmd.Cmd 封装
│   ├── nlu.py                # 自然语言理解（关键词匹配，不用 LLM）
│   │
│   ├── commands/             # 按分类组织命令
│   │   ├── __init__.py
│   │   ├── query.py          # 查询类命令（只读，安全）
│   │   ├── action.py         # 操作类命令（触发动作）
│   │   ├── debug.py          # 调试类命令（开发用）
│   │   └── admin.py          # 管理类命令（危险，需确认）
│   │
│   ├── formatter.py          # 终端输出格式化（表格/颜色等）
│   └── audit.py              # 审计日志（DB + 本地文件双写）
│
├── ops_scheduler/            # 已有：Tick 调度器
│   └── ...
│
└── project_agent.py          # 已有：ProjectAgent 主类
```

---

## 🔧 核心组件详细设计

### 1. Shell 主入口

```python
# agent_shell/shell.py

from __future__ import annotations
import asyncio
import cmd
import json
import logging
import socket
from datetime import datetime
from typing import Optional

from ..project_agent import ProjectAgent
from .nlu import NLUEngine, CommandIntent
from .audit import AuditLogger
from .formatter import ShellFormatter

logger = logging.getLogger("emily.agent_shell")


class EmilyShell(cmd.Cmd):
    """Emily ProjectAgent 交互式终端

    基于 Python 标准库 cmd 模块，原生支持：
    - 命令历史（上下箭头）
    - Tab 补全
    - 帮助系统
    """

    intro = """
███████╗███╗   ███╗██╗██╗  ██╗   ███████╗██╗  ██╗███████╗██╗     ██╗
██╔════╝████╗ ████║██║╚██╗██╔╝   ██╔════╝██║  ██║██╔════╝██║     ██║
█████╗  ██╔████╔██║██║ ╚███╔╝    ███████╗███████║█████╗  ██║     ██║
██╔══╝  ██║╚██╔╝██║██║ ██╔██╗    ╚════██║██╔══██║██╔══╝  ██║     ██║
███████╗██║ ╚═╝ ██║██║██╔╝ ██╗   ███████║██║  ██║███████╗███████╗███████╗
╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝

>>> Emily ProjectAgent Shell <<<
直接与 Emily 后台大脑对话。输入 help 查看可用命令。

实例 ID: {instance_id}
启动时间: {startup_time}
模式: 管理员权限

输入 help 查看可用命令，输入 exit 退出
"""

    prompt = "\n[agent] > "

    def __init__(self, agent: ProjectAgent):
        super().__init__()
        self._agent = agent
        self._instance_id = f"emily-core-{socket.gethostname()[-8:]}"
        self._startup_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._nlu = NLUEngine()
        self._audit = AuditLogger()
        self._fmt = ShellFormatter()

        self.intro = self.intro.format(
            instance_id=self._instance_id,
            startup_time=self._startup_time,
        )

    # ─── 命令执行入口 ───

    def precmd(self, line: str) -> str:
        """命令执行前钩子：记录审计日志"""
        if line.strip() and line.lower() not in ("exit", "quit", "q"):
            self._audit.log_command(line, "STARTED")
        return line

    def default(self, line: str):
        """默认命令处理器：自然语言理解"""

        # 退出命令
        if line.lower() in ("exit", "quit", "q"):
            print("👋 再见！")
            return True

        # 帮助命令
        if line.lower() in ("help", "?"):
            self._print_help()
            return

        # 1. NLU 解析
        intent = self._nlu.parse(line)

        # 2. 置信度太低，给出建议
        if intent.confidence < 0.3:
            print(self._fmt.box(
                "❓ 不太理解你的意思，试试这些说法：\n"
                "\n"
                "  🔍 查询类：\n"
                "     • 锦绣花园进度怎么样？\n"
                "     • 列出卡滞超过 14 天的节点\n"
                "     • 查看最近告警\n"
                "\n"
                "  ⚡ 操作类：\n"
                "     • 立即执行一次全量巡检\n"
                "     • 生成上周项目周报\n"
                "\n"
                "  🔧 调试类：\n"
                "     • 查看当前状态\n"
                "     • 导出当前配置\n"
                "\n"
                "  🛡️  管理类：\n"
                "     • 清理 30 天前的历史数据"
            ))
            self._audit.log_command(line, "REJECTED", reason="low confidence")
            return

        # 3. 置信度中等，给出候选
        if intent.confidence < 0.6:
            print(f"💡 我猜你是想：{intent.description}？")
            confirm = input("  确认执行吗？(yes/no) ").strip().lower()
            if confirm not in ("y", "yes"):
                print("❌ 已取消")
                return

        # 4. 执行命令
        try:
            result = asyncio.run(self._execute(intent))
            self._audit.log_command(line, "SUCCESS", result_summary=str(result)[:200])
        except Exception as e:
            print(f"\n❌ 执行失败：{e}")
            self._audit.log_command(line, "FAILED", error=str(e))

    # ─── 命令分发器 ───

    async def _execute(self, intent: CommandIntent):
        """执行命令意图"""

        # ==========================================
        # 🔍 查询类命令（只读，安全，无确认）
        # ==========================================
        if intent.category == "query":
            if intent.type == "project_status":
                await self._cmd_project_status(intent)
            elif intent.type == "list_stale":
                await self._cmd_list_stale(intent)
            elif intent.type == "list_alerts":
                await self._cmd_list_alerts(intent)
            elif intent.type == "export_report":
                await self._cmd_export_report(intent)

        # ==========================================
        # ⚡ 操作类命令（触发动作，无确认）
        # ==========================================
        elif intent.category == "action":
            if intent.type == "force_tick":
                await self._cmd_force_tick(intent)
            elif intent.type == "generate_weekly":
                await self._cmd_generate_weekly(intent)

        # ==========================================
        # 🔧 调试类命令（开发用，无确认）
        # ==========================================
        elif intent.category == "debug":
            if intent.type == "show_config":
                await self._cmd_show_config(intent)
            elif intent.type == "show_status":
                await self._cmd_show_status(intent)

        # ==========================================
        # 🛡️  管理类命令（危险操作，需二次确认）
        # ==========================================
        elif intent.category == "admin":
            confirm_msg = {
                "purge_data": "⚠️  这将删除历史数据，无法恢复！",
                "force_recalc": "⚠️  这将强制重新计算所有节点状态！",
                "emergency_stop": "⚠️  这将停止所有后台运维操作！",
            }.get(intent.type, "⚠️  确定执行吗？")

            confirm = input(f"\n{confirm_msg} (yes/no) ").strip().lower()
            if confirm not in ("y", "yes"):
                print("❌ 已取消")
                return

            if intent.type == "purge_data":
                await self._cmd_purge_data(intent)
            elif intent.type == "force_recalc":
                await self._cmd_force_recalc(intent)

    # ─── 具体命令实现 ───

    async def _cmd_project_status(self, intent: CommandIntent):
        """查询项目状态"""
        project_name = intent.extract_param("project_name", default="锦绣花园")

        print(f"\n📊 {project_name} 项目状态：\n")

        # 调用 ProjectAgent 内部方法
        status = await self._agent.get_project_status(project_name)

        print(f"  整体进度：{status['overall_progress']}%")
        print(f"  当前阶段：{status['current_stage']}")
        print(f"  完成节点：{status['nodes_completed']} 个")
        print(f"  进行中：{status['nodes_in_progress']} 个")
        print(f"  ⚠️  阻塞：{status['nodes_blocked']} 个")

        if status['milestones_near']:
            print(f"\n  📅 即将到期的里程碑：")
            for m in status['milestones_near'][:3]:
                print(f"     • {m['name']}：还有 {m['days_left']} 天")

    async def _cmd_list_stale(self, intent: CommandIntent):
        """列出卡滞节点"""
        threshold = intent.extract_param("threshold_days", default=14)

        print(f"\n📋 卡滞节点清单（超过 {threshold} 天未更新）：\n")

        nodes = await self._agent.get_stale_nodes(threshold)

        if not nodes:
            print("  ✅ 没有卡滞节点")
            return

        # 按严重程度排序
        nodes.sort(key=lambda n: n['days_stale'], reverse=True)

        headers = ["排名", "节点 ID", "节点名称", "负责人", "卡滞天数", "状态"]
        rows = []
        for i, n in enumerate(nodes, 1):
            rank = "🔴" if n['days_stale'] > 30 else "🟡"
            rows.append([
                f"{rank} #{i}",
                n['node_id'],
                n['node_name'],
                n['owner'],
                f"{n['days_stale']} 天",
                n['status'],
            ])

        print(self._fmt.table(headers, rows))

    async def _cmd_force_tick(self, intent: CommandIntent):
        """手动触发一轮 Tick"""
        print("\n🔄 正在执行全量巡检...\n")

        result = await self._agent.run_tick_now()

        print(f"  ✅ Tick 执行完成")
        print(f"  执行探针：{result['probes_total']} 个")
        print(f"  成功：{result['probes_success']} 个")
        print(f"  失败：{result['probes_failed']} 个")
        print(f"  发现问题：{result['total_findings']} 个")

        if result['findings']:
            print(f"\n  📝 问题清单：")
            for f in result['findings'][:5]:
                severity = "🔴" if f['severity'] == "CRITICAL" else "🟡"
                print(f"     {severity} {f['message']}")

    async def _cmd_generate_weekly(self, intent: CommandIntent):
        """生成周报"""
        week = intent.extract_param("week", default="last")

        print(f"\n📄 正在生成第 {week} 周项目周报...\n")

        report = await self._agent.generate_weekly_report(week)

        # 保存到本地
        report_path = f"logs/weekly_{datetime.now():%Y%m%d_%H%M%S}.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report['content'])

        print(f"  ✅ 周报已生成")
        print(f"  📄 报告已保存到：{report_path}")

        # 询问是否发送邮件
        send_email = input("\n  📧 要发送邮件给项目经理吗？(yes/no) ").strip().lower()
        if send_email in ("y", "yes"):
            await self._agent.send_report_email(report['id'])
            print("  ✅ 邮件已发送")

    def _print_help(self):
        """自定义帮助信息"""
        help_text = """
📖 EmilyShell 可用命令（支持自然语言输入，不用严格匹配）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 查询类命令（只读，无风险）

  项目相关：
    • 锦绣花园进度怎么样？
    • [项目名] 当前状态
    • 查看项目整体进度

  节点相关：
    • 列出卡滞节点
    • 卡滞超过 14 天的节点
    • 阻塞节点有哪些？

  告警相关：
    • 查看最近 10 条告警
    • 本周有什么预警？
    • 里程碑到期提醒

  报告相关：
    • 导出最近 7 天报告
    • 生成运维报告

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ 操作类命令（触发动作，无确认）

  • 立即执行一次全量巡检
  • 手动触发 Tick
  • 生成上周项目周报
  • 导出 6 月的项目数据

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 调试类命令（开发/排错用）

  • 查看当前系统状态
  • 显示当前配置
  • 导出运行时参数

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🛡️  管理类命令（危险操作，需二次确认）

  • 清理 30 天前的历史数据
  • 强制重新计算所有节点状态
  • 紧急停止所有运维操作

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 提示：
  • 命令支持模糊匹配，不用严格按上面写
  • 不确定的命令会询问确认
  • 所有操作都会记录审计日志
"""
        print(help_text)


# ─── 入口函数 ───

def main():
    """EmilyShell 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Emily ProjectAgent Shell")
    parser.add_argument("--command", "-c", help="直接执行命令（非交互模式）")
    args = parser.parse_args()

    # 获取 ProjectAgent 单例
    agent = ProjectAgent.get_instance()

    shell = EmilyShell(agent)

    if args.command:
        # 单命令模式
        shell.default(args.command)
    else:
        # 交互模式
        try:
            shell.cmdloop()
        except KeyboardInterrupt:
            print("\n\n👋 再见！")


if __name__ == "__main__":
    main()
```

---

### 2. NLU 自然语言理解引擎

```python
# agent_shell/nlu.py

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any
import re


@dataclass
class CommandIntent:
    """命令意图解析结果"""
    type: str                      # 命令类型标识
    category: str                  # query / action / debug / admin
    confidence: float              # 置信度 0-1
    description: str               # 人类可读描述
    params: Dict[str, Any] = None  # 提取出的参数


class NLUEngine:
    """自然语言理解引擎

    设计决策：纯关键词匹配，不用 LLM
    理由：
      1. 零依赖、零成本、零延迟
      2. 运维命令不需要创造性，关键词足够
      3. 零幻觉，100% 确定
    """

    # 意图模式定义
    INTENT_PATTERNS = {
        # ==========================================
        # 🔍 查询类
        # ==========================================
        "project_status": {
            "category": "query",
            "description": "查询项目状态",
            "keywords": [
                "进度", "状态", "怎么样", "情况",
                "project status", "how is",
            ],
            "param_extractors": {
                "project_name": r"(锦绣花园|滨江商务区|城市综合体)[\s的]*",
            }
        },

        "list_stale": {
            "category": "query",
            "description": "列出卡滞节点",
            "keywords": [
                "卡滞", "卡住", "阻塞", "未更新", "停滞",
                "stale", "blocked", "stuck",
            ],
            "param_extractors": {
                "threshold_days": r"超过[\s]*(\d+)[\s]*天",
            }
        },

        "list_alerts": {
            "category": "query",
            "description": "查看告警列表",
            "keywords": [
                "告警", "预警", "提醒", "通知",
                "alert", "warning",
            ],
        },

        "export_report": {
            "category": "query",
            "description": "导出报告",
            "keywords": [
                "导出", "报告", "报表", "生成报告",
                "export", "report",
            ],
        },

        # ==========================================
        # ⚡ 操作类
        # ==========================================
        "force_tick": {
            "category": "action",
            "description": "手动执行一轮 Tick 巡检",
            "keywords": [
                "立即巡检", "手动巡检", "跑一遍检查", "触发 Tick",
                "force tick", "run check", "立即执行",
            ],
        },

        "generate_weekly": {
            "category": "action",
            "description": "生成项目周报",
            "keywords": [
                "周报", "每周报告", "weekly report",
            ],
        },

        # ==========================================
        # 🔧 调试类
        # ==========================================
        "show_config": {
            "category": "debug",
            "description": "显示当前配置",
            "keywords": [
                "显示配置", "查看配置", "导出配置",
                "show config", "current config",
            ],
        },

        "show_status": {
            "category": "debug",
            "description": "显示系统运行状态",
            "keywords": [
                "运行状态", "系统状态", "当前状态",
                "system status", "runtime",
            ],
        },

        # ==========================================
        # 🛡️  管理类
        # ==========================================
        "purge_data": {
            "category": "admin",
            "description": "清理历史数据",
            "keywords": [
                "清理", "删除", " purge", "clean up",
            ],
        },

        "force_recalc": {
            "category": "admin",
            "description": "强制重新计算",
            "keywords": [
                "重新计算", "强制刷新", "重置",
                "recalc", "refresh", "reset",
            ],
        },
    }

    def parse(self, user_input: str) -> CommandIntent:
        """解析用户输入为命令意图"""
        user_lower = user_input.lower()

        best_match = None
        best_score = 0.0

        for intent_type, config in self.INTENT_PATTERNS.items():
            # 计算关键词匹配得分
            matched = sum(
                1 for kw in config['keywords']
                if kw.lower() in user_lower
            )
            total = len(config['keywords'])
            score = matched / total if total > 0 else 0

            if score > best_score:
                best_score = score
                best_match = intent_type

        if best_score == 0:
            return CommandIntent(
                type="unknown",
                category="unknown",
                confidence=0.0,
                description="未知命令",
            )

        # 提取参数
        config = self.INTENT_PATTERNS[best_match]
        params = {}
        if 'param_extractors' in config:
            for param_name, pattern in config['param_extractors'].items():
                match = re.search(pattern, user_input)
                if match:
                    params[param_name] = match.group(1)

        return CommandIntent(
            type=best_match,
            category=config['category'],
            confidence=best_score,
            description=config['description'],
            params=params,
        )

    def extract_param(self, intent: CommandIntent, name: str, default=None):
        """从意图中提取参数"""
        if intent.params and name in intent.params:
            return intent.params[name]
        return default
```

---

### 3. 审计日志

```python
# agent_shell/audit.py

from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("emily.agent_shell.audit")


class AuditLogger:
    """审计日志记录器

    双保险设计：
      1. 写入 DB ops_audit 表
      2. 写入本地 logs/ops_audit.log 文件
      3. DB 挂了也至少有本地文件记录
    """

    def __init__(self):
        self._log_file = "logs/ops_audit.log"
        # 确保目录存在
        import os
        os.makedirs(os.path.dirname(self._log_file), exist_ok=True)

    def log_command(
        self,
        command: str,
        status: str,
        result_summary: str = "",
        error: str = "",
    ):
        """记录命令执行审计"""

        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "channel": "emily_shell",
            "command": command,
            "status": status,
            "result_summary": result_summary[:500] if result_summary else "",
            "error": error[:500] if error else "",
            "source": "local_terminal",
        }

        # 1. 总是写本地文件（最低保障）
        self._write_local(record)

        # 2. 尝试写 DB（失败不影响主流程）
        try:
            self._write_db(record)
        except Exception as e:
            logger.warning(f"Write DB audit failed, but local log is OK: {e}")

    def _write_local(self, record: dict):
        """写本地日志文件"""
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _write_db(self, record: dict):
        """写数据库"""
        # 此处调用 DB Repository
        # ops_audit 表结构：
        #   id, timestamp, channel, command, status, result_summary, error, source
        pass
```

---

### 4. 终端格式化工具

```python
# agent_shell/formatter.py

from __future__ import annotations
from typing import List


class ShellFormatter:
    """终端输出格式化工具"""

    @staticmethod
    def box(content: str, width: int = 80) -> str:
        """输出带边框的文本框"""
        lines = content.split('\n')
        border = "─" * (width - 2)
        result = [f"┌{border}┐"]
        for line in lines:
            result.append(f"│ {line:<{width - 4}} │")
        result.append(f"└{border}┘")
        return "\n".join(result)

    @staticmethod
    def table(headers: List[str], rows: List[List[str]]) -> str:
        """生成表格输出"""
        if not rows:
            return "  (无数据)"

        # 计算每列宽度
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # 生成分隔线
        sep = "  " + "+".join("-" * (w + 2) for w in col_widths)

        # 生成输出
        lines = ["", sep]

        # 表头
        header_line = "  " + "|".join(
            f" {h:<{w}} " for h, w in zip(headers, col_widths)
        )
        lines.append(header_line)
        lines.append(sep)

        # 数据行
        for row in rows:
            row_line = "  " + "|".join(
                f" {str(c):<{w}} " for c, w in zip(row, col_widths)
            )
            lines.append(row_line)

        lines.append(sep)
        return "\n".join(lines)
```

---

## 🎮 交互示例

```bash
# 启动 EmilyShell
docker exec -it emily-core python -m emily_core.project.agent_shell
```

### 场景 1：查询项目进度

```
[agent] > 锦绣花园现在进度怎么样了？

📊 锦绣花园住宅小区项目状态：

  整体进度：42% 🟡
  当前阶段：设计阶段 (Stage 2)
  完成节点：12 个
  进行中：5 个
  ⚠️  阻塞：3 个

  📅 即将到期的里程碑：
     • 施工图审查完成：还有 7 天
     • 施工许可证获取：还有 21 天
```

### 场景 2：列出卡滞节点

```
[agent] > 把卡滞超过 14 天的节点列出来，按严重程度排序

📋 卡滞节点清单（超过 14 天未更新）：

  ─────────────────────────────────────────────────────────────────
  排名   节点 ID  节点名称        负责人  卡滞天数   状态
  ─────────────────────────────────────────────────────────────────
  🔴 #1  3.5     施工许可办理    工程部    31 天   BLOCKED
  🟡 #2  1.2     方案设计评审    设计部    23 天   BLOCKED
  🟡 #3  2.1     施工图审查      设计部    16 天   IN_PROGRESS
  ─────────────────────────────────────────────────────────────────
```

### 场景 3：手动触发巡检

```
[agent] > 立即执行一次全量巡检

🔄 正在执行全量巡检...

  ✓ StaleDetector - 发现 3 个卡滞节点
  ✓ MilestoneChecker - 2 个即将到期
  ✓ HealthChecker - 项目健康度 68 分 🟡
  ✓ DataIntegrity - 全部通过

✅ Tick 执行完成
  执行探针：4 个
  成功：4 个
  失败：0 个
  发现问题：5 个

  📝 问题清单：
     🟡 节点 3.5 已卡滞 31 天
     🟡 节点 1.2 已卡滞 23 天
     🟡 里程碑 施工图审查完成 还有 7 天
     🟡 里程碑 施工许可证获取 还有 21 天
     🟡 项目健康度低于 70 分
```

### 场景 4：生成周报

```
[agent] > 生成上周的项目周报

📄 正在生成第 26 周项目周报...

  ✓ 汇总上周数据
  ✓ 生成进度图表
  ✓ 撰写风险分析

✅ 周报已生成
📄 报告已保存到：logs/weekly_20240627_113500.md

📧 要发送邮件给项目经理吗？(yes/no) yes
✅ 邮件已发送
```

---

## 📋 运维手册

### 三种启动方式

#### 方式 1：交互模式（推荐，给人用）

```bash
docker exec -it emily-core python -m emily_core.project.agent_shell
```

进入 REPL，可以连续输入命令。

#### 方式 2：单命令模式（脚本用）

```bash
# 直接执行，执行完退出
docker exec emily-core python -m emily_core.project.agent_shell -c "查看卡滞节点"

# 输出到文件
docker exec emily-core python -m emily_core.project.agent_shell -c "导出报告" > report.md
```

#### 方式 3：Cron 定时调用

```bash
# crontab -e
0 9 * * 1 docker exec emily-core python -m emily_core.project.agent_shell -c "生成上周周报" >> /var/log/emily_cron.log 2>&1
```

---

### 审计查询 SQL

```sql
-- 查看 EmilyShell 所有操作记录
SELECT
    timestamp,
    command,
    status,
    left(result_summary, 50) as result
FROM ops_audit
WHERE channel = 'emily_shell'
ORDER BY timestamp DESC
LIMIT 20;

-- 查看失败的操作
SELECT * FROM ops_audit
WHERE channel = 'emily_shell'
  AND status = 'FAILED'
ORDER BY timestamp DESC;
```

---

### 本地日志文件位置

| 日志类型 | 路径 |
|---------|------|
| 审计日志 | `logs/ops_audit.log` |
| Shell 运行日志 | `logs/emily_shell.log` |
| 导出的报告 | `logs/weekly_YYYYMMDD_HHMMSS.md` |

---

## 🚀 实施计划

| 阶段 | 内容 | 工作量 | 交付物 |
|------|------|--------|--------|
| **阶段一** | Shell 核心框架 + 基础查询命令 | 0.5 天 | shell.py + nlu.py 骨架 |
| **阶段二** | 4 类命令全部实现 + 审计日志 | 1 天 | commands/*.py + audit.py |
| **阶段三** | 帮助完善 + 测试 + 文档 | 0.5 天 | 格式化器 + 测试用例 |
| **总计** | | **2 人天** | |

---

## ✅ 测试矩阵

| 测试场景 | 验证点 |
|---------|--------|
| 启动 | 能正常进入 Shell，欢迎信息正确 |
| 退出 | exit/quit/q/CTRL+C 都能正常退出 |
| 帮助 | help/? 能显示帮助信息 |
| 查询命令 | 项目状态/卡滞节点/告警列表都能输出 |
| 操作命令 | 手动 Tick / 生成周报能执行 |
| 管理命令 | 危险命令会要求二次确认 |
| NLU 模糊匹配 | 不同说法能识别为同一命令 |
| 低置信度处理 | 无法识别时给出合理建议 |
| 单命令模式 | `-c "命令"` 能执行并退出 |
| 审计日志 | 所有操作 DB + 本地文件双记录 |
| 异常处理 | 命令执行失败不崩溃，显示错误信息 |

---

## 📌 与 ops_scheduler 的关系

```
ops_scheduler = 定时 + 触发（机械的调度器）
EmilyShell    = 人工 + 交互（人的操作接口）

关系：
  EmilyShell 可以手动调用 ops_scheduler.run_tick_now()
  两者是互补关系，不是替代关系
  ops_scheduler 在后台默默干活
  EmilyShell 在前台供人操作/调试/紧急干预
```

---

## 💡 设计亮点回顾

| 亮点 | 价值 |
|------|------|
| **零依赖** | 只用 Python 标准库，稳定到永久 |
| **双模式** | 交互模式给人用，单命令模式给脚本用 |
| **分层风险** | 查询/操作/调试/管理 4 类，危险需确认 |
| **双保险审计** | DB + 本地日志，DB 挂了也有记录 |
| **不用 LLM** | 零幻觉、零成本、零延迟，100% 确定 |
| **低侵入** | 独立模块，不影响 ProjectAgent 现有代码 |

---

**EmilyShell = ProjectAgent 的 SSH 终端**，把后台能力直接送到管理员指尖！ 🚀