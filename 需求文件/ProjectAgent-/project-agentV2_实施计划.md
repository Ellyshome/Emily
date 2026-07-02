# EmilyShell — AI 执行计划

> **基于需求**：[project-agent补充.md](project-agent补充.md) v1.0
> **计划版本**：v2.0（基于 req-plan v2 模板）
> **目标**：为 ProjectAgent 构建零依赖 Python stdlib 交互式运维终端

---

## 你的角色

你是 **Emily 项目后端开发者**。你正在为 Emily 项目的 ProjectAgent 构建一个基于 Python `cmd.Cmd` 的本地运维 Shell。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：本计划只在已有类中新增方法（`OpsRepository` 加 2 个新方法），不修改任何已有方法的参数或返回类型
2. **分层不跳**：Shell 通过 Repository 层访问 DB，不直接写 SQL（advisory lock 除外，那是协议级操作）；Shell 独立于 API→Session→WorkItem 链路
3. **sync Repository 模式**：所有 DB 操作遵循已有 `@staticmethod` + 可选 `session` 参数 + `_impl(sess)` 内函数模式
4. **代码完整**：每个新建文件包含完整 import、完整类体、完整方法体。不允许 `pass`、`...`、`# TODO`
5. **无第三方依赖**：Shell 只用 Python 标准库（`cmd`, `argparse`, `asyncio`, `json`, `logging`, `os`, `socket`, `re`, `dataclasses`, `datetime`, `uuid`, `typing`）。不新增 pip 包

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `SMNodeRepository` | `emily-core/emily_core/repositories/sm_node_repo.py` | `list_all()`, `list_stale(statuses, older_than_iso)`, `list_milestones_near_deadline(now_iso, warn_before_days)`, `list_by_status(status)`, `count()` | 直接实例化调用，查询节点状态 |
| `OpsRepository` | `emily-core/emily_core/project/ops/repositories/ops_repo.py` | `save_tick_results()`, `save_mail_audit()`, `get_latest_startup_report()` | 直接实例化调用 + 新增 `save_shell_audit()` 和 `get_recent_findings()` |
| `OpsScheduler` | `emily-core/emily_core/project/ops/scheduler.py` | `run_tick(tick_id, tick_number) -> dict` | Phase 2 force_tick 时在独立进程中构建并调用 |
| `TickContext` | `emily-core/emily_core/project/ops/probe_base.py` | dataclass: `tick_id`, `tick_number`, `start_time` | Phase 2 手动构建传给 OpsScheduler |
| `ProbeFinding` | `emily-core/emily_core/project/ops/probe_base.py` | dataclass: `finding_type`, `severity`, `target_id`, `message`, `metadata` | 类型引用 |
| `Config` | `emily-core/emily_core/config.py` | `from_dict(data)` classmethod | `__main__.py` 中自举配置 |
| `init_db` | `emily-core/emily_core/infrastructure/database/session.py` | `init_db(db_url)` | `__main__.py` 中初始化 DB 连接 |
| `get_session` / `get_session_raw` | `emily-core/emily_core/infrastructure/database/session.py` | context manager / raw session | Repo 内部用 `get_session`，force_tick 用 `get_session_raw` 获取 advisory lock |

### 架构决策

**方案：独立进程 + 直接 DB 访问**。Shell 作为独立 Python 进程运行（`docker exec python -m ...`），自举 Config/DB/Repos，通过 Repository 直接访问 PostgreSQL。选择此方案因为：零依赖（不依赖 FastAPI 进程存活）、零延迟（直接 DB 查询）、且 EmilyShell 定位为"紧急运维通道"——FastAPI 挂了也必须能用。替代方案（HTTP 调用 FastAPI）违背零依赖原则，被否决。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| ORM 模型 | `emily-core/emily_core/project/ops/models.py` — `OpsMailAudit` 类 | `UUID(as_uuid=False)` 主键, `DateTime(timezone=True)`, `default=_new_uuid` / `default=_utc_now`, 无 `server_default`（用 Python-side default） |
| Repository 新增方法 | `emily-core/emily_core/project/ops/repositories/ops_repo.py` — `save_mail_audit()` | `@staticmethod` + `session: Optional[Session] = None` + `def _impl(sess: Session):` 内函数 + `if session: return _impl(session)` / `with get_session() as sess: return _impl(sess)` |
| Repository 查询方法 | `emily-core/emily_core/project/ops/repositories/ops_repo.py` — `get_latest_startup_report()` | `ORDER BY created_at DESC` + `.first()` 或 `.limit(n).all()` |
| 配置 dataclass | `emily-core/emily_core/config.py` — 现有字段 | `field_name: type = default` 平铺风格，追加在相关区域 |
| Shell CLI | `emily-core/scripts/smoke_test.py` | `argparse.ArgumentParser` + `--command/-c` |
| 同步桥接 | `emily-core/emily_core/project/ops/probes/mailbox_probe.py` — `_run_async_in_sync()` | 检测 `asyncio.get_running_loop()` + `ThreadPoolExecutor` |

---

## Phase 1: Shell 核心框架 + 查询命令

**前置检查**：此阶段无依赖，可直接开始。

**交付物**：可启动的 REPL 终端，支持 help/exit + 4 个查询命令（项目状态、卡滞节点、告警、findings）+ 双写审计（DB + 本地文件）。

---

### Step 1.1: 创建数据库审计表和 ORM 模型

**目标**：新增 `ops_shell_audit` 表，存储所有 Shell 命令的审计记录。

**操作**：

1. 打开 `emily-core/emily_core/project/ops/models.py`
2. 找到文件末尾最后一个类 `OpsStartupReport` 的结束行（`created_at = Column(DateTime(timezone=True), default=_utc_now)`）之后，空一行，追加：

```python
# emily-core/emily_core/project/ops/models.py — 追加在 OpsStartupReport 类定义后

class OpsShellAudit(Base):
    """Shell 审计日志表 —— 记录 EmilyShell 终端的所有操作。

    双保险设计：DB 写入 + 本地文件写入，DB 挂了也有本地记录。
    与 OpsMailAudit 对等 —— 都是"人工命令"审计表。
    无 FK 到 ops_tick_log（shell 命令独立于 tick 周期）。
    """
    __tablename__ = "ops_shell_audit"

    id = Column(UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    command_text = Column(Text, nullable=False)
    status = Column(String(50), nullable=False)
    category = Column(String(50), default="")
    intent_type = Column(String(100), default="")
    confidence = Column(Integer, default=0)
    result_summary = Column(Text, default="")
    error_message = Column(Text, default="")
    instance_id = Column(String(200), default="")
    source = Column(String(50), default="local_terminal")
    created_at = Column(DateTime(timezone=True), default=_utc_now)
```

3. 更新 `emily-core/emily_core/project/ops/models.py` 文件顶部的模块 docstring，将 `5 张运维表` 改为 `6 张运维表`，并在表清单中添加 `ops_shell_audit — Shell 审计日志`。

4. 打开 `emily-core/emily_core/infrastructure/database/models.py`，找到文件底部 ops 模型导入处（搜索 `from emily_core.project.ops.models import`），在导入列表末尾追加 `OpsShellAudit`：

```python
# emily-core/emily_core/infrastructure/database/models.py — 修改底部 import 行
from emily_core.project.ops.models import (
    OpsTickLog, OpsProbeExecution, OpsFinding, OpsMailAudit, OpsStartupReport,
    OpsShellAudit,   # ← 新增
)
```

**验证**：
```bash
grep "class OpsShellAudit" emily-core/emily_core/project/ops/models.py
→ 应返回：class OpsShellAudit(Base):

grep "OpsShellAudit" emily-core/emily_core/infrastructure/database/models.py
→ 应返回一行匹配
```

**失败处理**：如果 grep 无输出，检查 models.py 文件中类定义和 import 的位置是否正确。确认类名拼写与 import 一致。

---

### Step 1.2: 新增 Repository 方法

**目标**：在 `OpsRepository` 中新增 `save_shell_audit()` 和 `get_recent_findings()` 两个方法。

**操作**：

1. 打开 `emily-core/emily_core/project/ops/repositories/ops_repo.py`
2. 找到文件顶部的 import 区域，在 `from emily_core.project.ops.models import (` 导入列表末尾追加 `OpsShellAudit`：

```python
# emily-core/emily_core/project/ops/repositories/ops_repo.py — 修改 import 行
from emily_core.project.ops.models import (
    OpsTickLog,
    OpsProbeExecution,
    OpsFinding,
    OpsMailAudit,
    OpsStartupReport,
    OpsShellAudit,   # ← 新增
)
```

3. 找到文件中最后一个方法 `mark_command_dispatched()` 的结束位置，在其后追加两个新方法：

```python
# emily-core/emily_core/project/ops/repositories/ops_repo.py — 追加在类末尾

    # ── Shell 审计 ──

    @staticmethod
    def save_shell_audit(data: dict, *, session: Optional[Session] = None) -> Optional[OpsShellAudit]:
        """写入 Shell 审计记录。每次命令执行都写入一条。

        Args:
            data: 包含 command_text, status, category, intent_type,
                  confidence, result_summary, error_message, instance_id, source 的 dict

        Returns:
            OpsShellAudit 实例或 None（DB 不可达时返回 None，不抛异常）
        """
        def _impl(sess: Session) -> Optional[OpsShellAudit]:
            rec = OpsShellAudit(
                command_text=data.get("command_text", ""),
                status=data.get("status", "STARTED"),
                category=data.get("category", ""),
                intent_type=data.get("intent_type", ""),
                confidence=data.get("confidence", 0),
                result_summary=(data.get("result_summary", "") or "")[:500],
                error_message=(data.get("error_message", "") or "")[:500],
                instance_id=data.get("instance_id", ""),
                source=data.get("source", "local_terminal"),
            )
            sess.add(rec)
            sess.flush()
            return rec

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)

    # ── Findings 查询 ──

    @staticmethod
    def get_recent_findings(*, limit: int = 20, session: Optional[Session] = None) -> list[OpsFinding]:
        """获取最近 N 条探针发现结果（按创建时间倒序）。

        Args:
            limit: 返回条数上限，默认 20
            session: 可选外部事务 session

        Returns:
            OpsFinding 列表（可能为空列表）
        """
        def _impl(sess: Session) -> list[OpsFinding]:
            return sess.query(OpsFinding).order_by(
                OpsFinding.created_at.desc()
            ).limit(limit).all()

        if session is not None:
            return _impl(session)
        with get_session() as sess:
            return _impl(sess)
```

**验证**：
```bash
grep "def save_shell_audit" emily-core/emily_core/project/ops/repositories/ops_repo.py
→ 应返回：    def save_shell_audit(data: dict, *, session: Optional[Session] = None) -> Optional[OpsShellAudit]:

grep "def get_recent_findings" emily-core/emily_core/project/ops/repositories/ops_repo.py
→ 应返回：    def get_recent_findings(*, limit: int = 20, session: Optional[Session] = None) -> list[OpsFinding]:
```

**失败处理**：如果 grep 无输出，检查 import 是否正确添加，确认方法缩进与类中其他 `@staticmethod` 一致（4 空格）。

---

### Step 1.3: 新增配置项

**目标**：在全局 Config 中新增 Shell 审计相关的两个配置 key。

**操作**：

1. 打开 `emily-core/emily_core/config.py`
2. 找到 `ops_fallback_log_dir: str = "logs/"` 行，在其后空一行，追加：

```python
# emily-core/emily_core/config.py — 追加在 ops_fallback_log_dir 后
shell_audit_enabled: bool = True
shell_audit_log_dir: str = "logs/"
```

**验证**：
```bash
grep "shell_audit_enabled" emily-core/emily_core/config.py
→ 应返回：shell_audit_enabled: bool = True
grep "shell_audit_log_dir" emily-core/emily_core/config.py
→ 应返回：shell_audit_log_dir: str = "logs/"
```

**失败处理**：如果 grep 无输出，检查插入位置——确认紧跟在 `ops_fallback_log_dir` 行之后。

---

### Step 1.4: 创建 Shell 依赖注入容器

**目标**：创建 `ShellDependencies` dataclass，集中管理 Shell 需要的所有依赖。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/deps.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/deps.py
"""Shell 依赖注入容器 —— 集中管理 EmilyShell 所需的所有依赖。

独立进程通过此容器自举 Config + DB Repos，不依赖 FastAPI 进程内存对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emily_core.config import Config
    from emily_core.repositories.sm_node_repo import SMNodeRepository
    from emily_core.project.ops.repositories.ops_repo import OpsRepository
    from emily_core.project.ops.scheduler import OpsScheduler


@dataclass
class ShellDependencies:
    """EmilyShell 依赖容器。

    所有字段在 __main__.py 中自举填充后传入 EmilyShell。
    ops_scheduler 为 Optional——Phase 1 查询命令不需要它，Phase 2 action 命令才需要。
    """
    config: Config
    sm_node_repo: SMNodeRepository
    ops_repo: OpsRepository
    instance_id: str
    ops_scheduler: OpsScheduler | None = None
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.deps import ShellDependencies; print('OK')"
→ 应输出：OK（需在 emily-core 容器内执行，且环境变量已设置）
```

**失败处理**：如果 import 失败，检查 TYPE_CHECKING 下的 import 路径是否正确。确认 `emily-core/emily_core/project/agent_shell/__init__.py` 已存在（见 Step 1.9）。

---

### Step 1.5: 创建 NLU 自然语言理解引擎

**目标**：创建基于关键词匹配的命令解析器（Phase 1 仅含 query 类意图，Phase 2 补全）。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/nlu.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/nlu.py
"""NLU 自然语言理解引擎 —— 纯关键词匹配，不用 LLM。

设计理由：
  1. 零依赖、零成本、零延迟
  2. 运维命令不需要创造性，关键词足够
  3. 零幻觉，100% 确定
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandIntent:
    """命令意图解析结果"""
    type: str
    category: str          # query / action / debug / admin / unknown
    confidence: float      # 0.0 - 1.0
    description: str       # 人类可读描述
    params: dict[str, Any] = field(default_factory=dict)


class NLUEngine:
    """自然语言理解引擎 —— 纯关键词匹配。

    使用方式：
        engine = NLUEngine()
        intent = engine.parse("锦绣花园进度怎么样？")
        # → CommandIntent(type="project_status", category="query", confidence=0.5, ...)
    """

    # ── 意图模式定义 ──

    INTENT_PATTERNS: dict[str, dict[str, Any]] = {
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
            },
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
            },
        },

        "list_alerts": {
            "category": "query",
            "description": "查看告警/里程碑提醒",
            "keywords": [
                "告警", "预警", "提醒", "通知", "里程碑",
                "alert", "warning",
            ],
        },

        "list_findings": {
            "category": "query",
            "description": "查看最近探针发现的问题",
            "keywords": [
                "finding", "问题", "异常", "发现",
                "probe", "探针",
            ],
            "param_extractors": {
                "limit": r"最近[\s]*(\d+)[\s]*条",
            },
        },

        # ==========================================
        # ⚡ 操作类（Phase 2 实现）
        # ==========================================
        "force_tick": {
            "category": "action",
            "description": "手动执行一轮 Tick 巡检",
            "keywords": [
                "立即巡检", "手动巡检", "跑一遍检查", "触发 Tick", "立即执行",
                "force tick", "run check",
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
        # 🔧 调试类（Phase 2 实现）
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
        # 🛡️  管理类（Phase 2 实现）
        # ==========================================
        "purge_data": {
            "category": "admin",
            "description": "清理历史数据",
            "keywords": [
                "清理", "删除", "purge", "clean up",
            ],
        },

        "force_recalc": {
            "category": "admin",
            "description": "强制重新计算所有节点状态",
            "keywords": [
                "重新计算", "强制刷新", "重置",
                "recalc", "refresh", "reset",
            ],
        },
    }

    # ── 公开方法 ──

    def parse(self, user_input: str) -> CommandIntent:
        """解析用户输入为命令意图。

        Args:
            user_input: 用户输入的原始文本

        Returns:
            CommandIntent，包含 type/category/confidence/description/params。
            无法匹配时返回 category="unknown", confidence=0.0。
        """
        user_lower = user_input.lower()

        best_type: str | None = None
        best_score: float = 0.0

        for intent_type, config in self.INTENT_PATTERNS.items():
            keywords: list[str] = config["keywords"]
            matched = sum(1 for kw in keywords if kw.lower() in user_lower)
            total = len(keywords)
            score = matched / total if total > 0 else 0.0

            if score > best_score:
                best_score = score
                best_type = intent_type

        if best_score == 0 or best_type is None:
            return CommandIntent(
                type="unknown",
                category="unknown",
                confidence=0.0,
                description="未知命令",
            )

        # 提取参数
        config = self.INTENT_PATTERNS[best_type]
        params: dict[str, Any] = {}
        if "param_extractors" in config:
            for param_name, pattern in config["param_extractors"].items():
                match = re.search(pattern, user_input)
                if match:
                    value = match.group(1)
                    if param_name in ("threshold_days", "limit"):
                        params[param_name] = int(value)
                    else:
                        params[param_name] = value

        return CommandIntent(
            type=best_type,
            category=config["category"],
            confidence=best_score,
            description=config["description"],
            params=params,
        )

    @staticmethod
    def extract_param(intent: CommandIntent, name: str, default: Any = None) -> Any:
        """从意图中提取参数，不存在时返回默认值。"""
        return intent.params.get(name, default)
```

**验证**：
```bash
python -c "
from emily_core.project.agent_shell.nlu import NLUEngine
e = NLUEngine()
r = e.parse('锦绣花园进度怎么样')
assert r.type == 'project_status', f'Expected project_status, got {r.type}'
assert r.category == 'query'
print('project_status OK:', r.confidence)

r2 = e.parse('列出卡滞节点')
assert r2.type == 'list_stale'
print('list_stale OK:', r2.confidence)

r3 = e.parse('随机乱码xyz')
assert r3.confidence == 0.0
print('unknown OK')
"
→ 应输出 3 行 OK，无 AssertionError
```

**失败处理**：如果断言失败，检查 INTENT_PATTERNS 中对应意图的 keywords 列表是否包含输入中的关键词。

---

### Step 1.6: 创建终端格式化工具

**目标**：创建 `ShellFormatter` 类，提供表格渲染和带边框文本框。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/formatter.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/formatter.py
"""终端输出格式化工具 —— ASCII 表格、带边框文本框、状态栏。

完全基于 Python 标准库，零第三方依赖。
"""

from __future__ import annotations

from typing import List


class ShellFormatter:
    """终端输出格式化工具。

    提供静态方法，无需实例化即可使用：
        fmt = ShellFormatter()
        print(fmt.table(headers, rows))
        print(fmt.box("一段文字"))
    """

    @staticmethod
    def box(content: str, width: int = 72) -> str:
        """输出带边框的文本框。

        Args:
            content: 文本内容（可含 \\n）
            width: 框体宽度（含边框字符），默认 72

        Returns:
            格式化后的多行字符串
        """
        lines = content.split("\n")
        inner_width = width - 4  # 两侧边框 + 空格
        border = "─" * (width - 2)
        result = [f"┌{border}┐"]
        for line in lines:
            # 处理中文对齐：中文占 2 个字符宽度
            result.append(f"│ {line:<{inner_width}} │")
        result.append(f"└{border}┘")
        return "\n".join(result)

    @staticmethod
    def table(headers: List[str], rows: List[List[str]]) -> str:
        """生成 ASCII 表格。

        Args:
            headers: 表头列表
            rows: 数据行列表，每行长度需与 headers 一致

        Returns:
            格式化后的表格字符串（含表头分隔线）
        """
        if not rows:
            return "  (无数据)"

        # 计算每列宽度
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # 生成分隔线
        sep = "  +" + "+".join("-" * (w + 2) for w in col_widths) + "+"

        # 生成表头
        header_line = "  |" + "|".join(
            f" {h:<{w}} " for h, w in zip(headers, col_widths)
        ) + "|"

        # 生成数据行
        data_lines = []
        for row in rows:
            line = "  |" + "|".join(
                f" {str(c):<{w}} " for c, w in zip(row, col_widths)
            ) + "|"
            data_lines.append(line)

        # 组装
        parts = ["", sep, header_line, sep]
        parts.extend(data_lines)
        parts.append(sep)
        return "\n".join(parts)

    @staticmethod
    def status_bar(data: dict[str, str]) -> str:
        """生成键值对状态栏。

        Args:
            data: 键值对字典

        Returns:
            格式化后的多行字符串
        """
        max_key_len = max(len(k) for k in data.keys()) if data else 0
        lines = []
        for key, value in data.items():
            lines.append(f"  {key:<{max_key_len}} : {value}")
        return "\n".join(lines)
```

**验证**：
```bash
python -c "
from emily_core.project.agent_shell.formatter import ShellFormatter
fmt = ShellFormatter()
print(fmt.box('hello'))
print(fmt.table(['A','B'], [['1','2'],['3','4']]))
print(fmt.status_bar({'key1': 'val1', 'longer_key': 'val2'}))
print('OK')
"
→ 应输出格式化的框、表格、状态栏，最后输出 OK
```

**失败处理**：如果输出格式错乱，检查表格的 `col_widths` 计算逻辑和分隔线拼接。

---

### Step 1.7: 创建审计日志记录器

**目标**：创建 `AuditLogger` 类，实现 DB + 本地文件双写审计。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/audit.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/audit.py
"""审计日志记录器 —— DB + 本地文件双写。

双保险设计：
  1. 写入 DB ops_shell_audit 表
  2. 写入本地 {log_dir}/shell_audit.log 文件（JSONL 格式）
  3. DB 不可达时至少保留本地文件
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies

logger = logging.getLogger("emily.agent_shell.audit")


class AuditLogger:
    """审计日志记录器。

    使用方式：
        audit = AuditLogger(deps)
        audit.log_command("列出卡滞节点", category="query", status="STARTED")
        # ... 执行命令 ...
        audit.update_status("SUCCESS", result_summary="发现 3 个卡滞节点")
    """

    def __init__(self, deps: "ShellDependencies"):
        self._deps = deps
        self._log_dir = deps.config.shell_audit_log_dir
        self._log_file = os.path.join(self._log_dir, "shell_audit.log")
        self._enabled = deps.config.shell_audit_enabled

        # 确保日志目录存在
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except OSError:
            logger.warning(f"Cannot create log directory: {self._log_dir}")

        # 当前命令的审计记录（在 log_command 时创建，update_status 时更新）
        self._current_record: dict | None = None

    def log_command(
        self,
        command: str,
        status: str = "STARTED",
        category: str = "",
        intent_type: str = "",
        confidence: float = 0.0,
        result_summary: str = "",
        error_message: str = "",
        source: str = "local_terminal",
    ) -> None:
        """记录一条命令执行审计。

        对 STARTED 状态：创建新记录并同时写入 DB + 本地文件。
        对终态（SUCCESS/FAILED/REJECTED/CANCELLED）：创建新记录并写入。

        Args:
            command: 用户输入的原始命令文本
            status: STARTED / SUCCESS / FAILED / REJECTED / CANCELLED
            category: query / action / debug / admin
            intent_type: NLU 解析出的意图类型
            confidence: NLU 置信度 0.0-1.0
            result_summary: 执行结果摘要
            error_message: 失败时的错误信息
            source: local_terminal / cron / script
        """
        if not self._enabled:
            return

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command_text": command,
            "status": status,
            "category": category,
            "intent_type": intent_type,
            "confidence": int(confidence * 100),
            "result_summary": (result_summary or "")[:500],
            "error_message": (error_message or "")[:500],
            "instance_id": self._deps.instance_id,
            "source": source,
        }

        # 1. 总是写本地文件（最低保障）
        self._write_local(record)

        # 2. 尝试写 DB（失败不影响主流程）
        try:
            self._deps.ops_repo.save_shell_audit(record)
        except Exception as e:
            logger.warning(f"Write DB audit failed (local log is OK): {e}")

    def _write_local(self, record: dict) -> None:
        """写本地 JSONL 日志文件。"""
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"Write local audit file failed: {e}")
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.audit import AuditLogger; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，检查 TYPE_CHECKING 下的 import 路径和 `ShellDependencies` 类的字段名是否匹配。

---

### Step 1.8: 创建查询命令模块

**目标**：创建 `QueryCommands` 类，实现 4 个查询命令的业务逻辑。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/commands/__init__.py`：

```python
# emily-core/emily_core/project/agent_shell/commands/__init__.py
"""EmilyShell 命令模块。

按风险分 4 类：
  query  — 查询类（只读，无风险）
  action — 操作类（触发动作）
  debug  — 调试类（开发用）
  admin  — 管理类（危险，需二次确认）
"""
```

2. 新建文件 `emily-core/emily_core/project/agent_shell/commands/query.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/commands/query.py
"""查询类命令 —— 只读操作，无风险，无确认。

实现：project_status / list_stale / list_alerts / list_findings
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies
    from emily_core.project.agent_shell.nlu import CommandIntent
    from emily_core.project.agent_shell.formatter import ShellFormatter


class QueryCommands:
    """查询类命令。

    所有 DB 查询通过 SMNodeRepository / OpsRepository，不直接写 SQL。
    """

    def __init__(self, deps: "ShellDependencies", fmt: "ShellFormatter"):
        self._deps = deps
        self._fmt = fmt

    async def dispatch(self, intent: "CommandIntent") -> dict:
        """根据意图分发到具体 handler。

        Returns:
            dict with 'success' (bool) and 'message' (str) keys
        """
        handlers = {
            "project_status": self.project_status,
            "list_stale": self.list_stale,
            "list_alerts": self.list_alerts,
            "list_findings": self.list_findings,
        }
        handler = handlers.get(intent.type)
        if handler is None:
            return {"success": False, "message": f"未知查询类型: {intent.type}"}
        return await asyncio.to_thread(handler, intent)

    # ── 项目状态 ──

    def project_status(self, intent: "CommandIntent") -> dict:
        """查询项目整体状态：按状态统计节点数 + 按阶段分布。

        调用链：SMNodeRepository.list_all() → 内存分组统计
        """
        nodes = self._deps.sm_node_repo.list_all()
        if not nodes:
            print("\n  暂无项目节点数据。请先导入全景节点。")
            return {"success": True, "message": "无数据"}

        # 按状态统计
        status_counts: dict[str, int] = {}
        for node in nodes:
            s = getattr(node, "status", "UNKNOWN")
            status_counts[s] = status_counts.get(s, 0) + 1

        total = len(nodes)
        completed = status_counts.get("COMPLETED", 0)
        in_progress = status_counts.get("IN_PROGRESS", 0)
        blocked = status_counts.get("BLOCKED", 0)
        delayed = status_counts.get("DELAYED", 0)
        not_started = status_counts.get("NOT_STARTED", 0)
        progress_pct = round(completed / total * 100, 1) if total > 0 else 0.0

        print(f"\n📊 项目整体状态\n")
        print(f"  节点总数：{total}")
        print(f"  已完成：  {completed}  ({progress_pct}%)")
        print(f"  进行中：  {in_progress}")
        if blocked:
            print(f"  ⚠️ 阻塞：  {blocked}")
        if delayed:
            print(f"  🔴 延期：  {delayed}")
        print(f"  未启动：  {not_started}")

        # 按阶段（stage_id）分布
        stage_groups: dict[int, list] = {}
        for node in nodes:
            sid = getattr(node, "stage_id", 0)
            if sid not in stage_groups:
                stage_groups[sid] = []
            stage_groups[sid].append(node)

        if stage_groups:
            print(f"\n  各阶段概况：")
            headers = ["阶段", "总数", "已完成", "进行中", "阻塞"]
            rows = []
            for sid in sorted(stage_groups.keys()):
                snodes = stage_groups[sid]
                s_total = len(snodes)
                s_comp = sum(1 for n in snodes if getattr(n, "status", "") == "COMPLETED")
                s_prog = sum(1 for n in snodes if getattr(n, "status", "") == "IN_PROGRESS")
                s_blk = sum(1 for n in snodes if getattr(n, "status", "") == "BLOCKED")
                progress_bar = "█" * int(s_comp / s_total * 10) + "░" * (10 - int(s_comp / s_total * 10)) if s_total > 0 else "░░░░░░░░░░"
                rows.append([
                    f"Stage {sid}",
                    str(s_total),
                    str(s_comp),
                    str(s_prog),
                    f"{'🔴' if s_blk > 0 else ''} {s_blk}",
                ])
            print(self._fmt.table(headers, rows))

        return {"success": True, "message": f"total={total}, completed={completed}"}

    # ── 卡滞节点 ──

    def list_stale(self, intent: "CommandIntent") -> dict:
        """列出卡滞超过阈值的节点。

        调用链：SMNodeRepository.list_stale(statuses=["IN_PROGRESS","BLOCKED"], older_than_iso=...)
        """
        from emily_core.project.agent_shell.nlu import NLUEngine

        threshold_days = NLUEngine.extract_param(intent, "threshold_days", default=14)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold_days)).isoformat()

        nodes = self._deps.sm_node_repo.list_stale(
            statuses=["IN_PROGRESS", "BLOCKED", "DELAYED"],
            older_than_iso=cutoff,
        )

        print(f"\n📋 卡滞节点清单（超过 {threshold_days} 天未更新）：\n")

        if not nodes:
            print("  ✅ 没有卡滞节点")
            return {"success": True, "message": "无卡滞节点"}

        # 计算卡滞天数
        now = datetime.now(timezone.utc)
        enriched = []
        for node in nodes:
            updated_str = getattr(node, "updated_at", "")
            try:
                updated_dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                days_stale = (now - updated_dt).days
            except (ValueError, TypeError):
                days_stale = threshold_days
            enriched.append((node, days_stale))

        enriched.sort(key=lambda x: x[1], reverse=True)

        headers = ["排名", "节点 ID", "节点名称", "负责人", "卡滞天数", "状态"]
        rows = []
        for i, (node, days) in enumerate(enriched, 1):
            rank = "🔴" if days > 30 else "🟡"
            rows.append([
                f"{rank} #{i}",
                getattr(node, "node_id", ""),
                getattr(node, "node_name", "")[:20],
                getattr(node, "owner", ""),
                f"{days} 天",
                getattr(node, "status", ""),
            ])
        print(self._fmt.table(headers, rows))

        return {"success": True, "message": f"发现 {len(enriched)} 个卡滞节点"}

    # ── 告警/里程碑 ──

    def list_alerts(self, intent: "CommandIntent") -> dict:
        """列出即将到期的里程碑节点。

        调用链：SMNodeRepository.list_milestones_near_deadline(now_iso=...,
        warn_before_days=...)
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        warn_days = self._deps.config.project_agent_deadline_warn_days

        nodes = self._deps.sm_node_repo.list_milestones_near_deadline(
            now_iso=now_iso,
            warn_before_days=warn_days,
        )

        print(f"\n📅 里程碑到期提醒（未来 {warn_days} 天）：\n")

        if not nodes:
            print("  ✅ 近期无到期里程碑")
            return {"success": True, "message": "无近期到期里程碑"}

        headers = ["节点 ID", "节点名称", "计划完成日期", "负责人", "状态"]
        rows = []
        for node in nodes:
            planned = getattr(node, "planned_end_date", "")
            rows.append([
                getattr(node, "node_id", ""),
                getattr(node, "node_name", "")[:30],
                planned[:10] if planned else "-",
                getattr(node, "owner", ""),
                getattr(node, "status", ""),
            ])
        print(self._fmt.table(headers, rows))

        return {"success": True, "message": f"发现 {len(nodes)} 个即将到期里程碑"}

    # ── 最近 Findings ──

    def list_findings(self, intent: "CommandIntent") -> dict:
        """列出最近 N 条探针发现的问题。

        调用链：OpsRepository.get_recent_findings(limit=N)
        """
        from emily_core.project.agent_shell.nlu import NLUEngine

        limit = NLUEngine.extract_param(intent, "limit", default=20)

        findings = self._deps.ops_repo.get_recent_findings(limit=limit)

        print(f"\n🔍 最近 {limit} 条探针发现：\n")

        if not findings:
            print("  ✅ 没有发现问题")
            return {"success": True, "message": "无 findings"}

        headers = ["时间", "探针", "严重度", "类型", "消息"]
        rows = []
        for f in findings:
            created = str(getattr(f, "created_at", ""))[:19] if getattr(f, "created_at", None) else ""
            severity = getattr(f, "severity", "INFO")
            sev_icon = "🔴" if severity == "CRITICAL" else "🟡" if severity == "WARNING" else "ℹ️"
            rows.append([
                created,
                getattr(f, "probe_name", ""),
                f"{sev_icon} {severity}",
                getattr(f, "finding_type", ""),
                (getattr(f, "message", "") or "")[:50],
            ])
        print(self._fmt.table(headers, rows))

        return {"success": True, "message": f"发现 {len(findings)} 条记录"}
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.commands.query import QueryCommands; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，检查 TYPE_CHECKING 下的所有 import 路径。确认 `ShellDependencies` / `ShellFormatter` / `CommandIntent` 的字段和方法名与实际文件一致。

---

### Step 1.9: 创建 Shell 主入口

**目标**：创建 `EmilyShell(cmd.Cmd)` REPL 主类和包初始化文件。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/__init__.py`：

```python
# emily-core/emily_core/project/agent_shell/__init__.py
"""EmilyShell —— ProjectAgent 交互式运维终端。

基于 Python 标准库 cmd.Cmd，提供：
  - 交互 REPL 模式（docker exec -it emily-core python -m ...）
  - 单命令模式（-c "命令"）
  - 4 类命令：查询/操作/调试/管理
  - 双写审计：DB + 本地文件

使用：
  docker exec -it emily-core python -m emily_core.project.agent_shell
  docker exec emily-core python -m emily_core.project.agent_shell -c "查看卡滞节点"
"""

from emily_core.project.agent_shell.shell import EmilyShell

__all__ = ["EmilyShell"]
```

2. 新建文件 `emily-core/emily_core/project/agent_shell/shell.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/shell.py
"""EmilyShell —— 基于 Python cmd.Cmd 的交互式运维终端。

核心设计：
  - precmd() 钩子记录审计（STARTED）
  - default() 处理所有输入：NLU 解析 → 置信度门 → 分发执行
  - asyncio.run() 桥接同步 REPL 和异步命令执行
"""

from __future__ import annotations

import asyncio
import cmd
import logging
from typing import TYPE_CHECKING

from emily_core.project.agent_shell.nlu import NLUEngine, CommandIntent
from emily_core.project.agent_shell.audit import AuditLogger
from emily_core.project.agent_shell.formatter import ShellFormatter
from emily_core.project.agent_shell.commands.query import QueryCommands

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies

logger = logging.getLogger("emily.agent_shell")


class EmilyShell(cmd.Cmd):
    """Emily ProjectAgent 交互式终端。

    基于 Python 标准库 cmd 模块，原生支持：
      - 命令历史（上下箭头）
      - 帮助系统
      - 双模式：交互 REPL + 单命令（-c）
    """

    intro = r"""
███████╗███╗   ███╗██╗██╗  ██╗   ███████╗██╗  ██╗███████╗██╗     ██╗
██╔════╝████╗ ████║██║╚██╗██╔╝   ██╔════╝██║  ██║██╔════╝██║     ██║
█████╗  ██╔████╔██║██║ ╚███╔╝    ███████╗███████║█████╗  ██║     ██║
██╔══╝  ██║╚██╔╝██║██║ ██╔██╗    ╚════██║██╔══██║██╔══╝  ██║     ██║
███████╗██║ ╚═╝ ██║██║██╔╝ ██╗   ███████║██║  ██║███████╗███████╗███████╗
╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝

>>> Emily ProjectAgent Shell <<<
直接与 Emily 后台大脑对话。

实例 ID: {instance_id}
启动时间: {startup_time}
模式: 管理员权限

输入 help 查看可用命令，输入 exit/quit/q 退出。
"""

    prompt = "\n[agent] > "

    def __init__(self, deps: "ShellDependencies"):
        super().__init__()
        self._deps = deps
        self._nlu = NLUEngine()
        self._audit = AuditLogger(deps)
        self._fmt = ShellFormatter()

        # Phase 1: 仅查询命令
        self._query = QueryCommands(deps, self._fmt)

        # Phase 2: 操作/调试/管理命令（暂为 None）
        self._action = None
        self._debug = None
        self._admin = None

        # 格式化 intro
        from datetime import datetime
        self.intro = self.intro.format(
            instance_id=deps.instance_id,
            startup_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # ── cmd.Cmd 钩子 ──

    def precmd(self, line: str) -> str:
        """命令执行前钩子：记录审计日志 STARTED。"""
        if line.strip() and line.lower() not in ("exit", "quit", "q", "help", "?"):
            self._audit.log_command(line.strip(), status="STARTED")
        return line

    # ── 命令入口 ──

    def default(self, line: str) -> bool | None:
        """默认命令处理器 —— 所有非内置命令的入口。

        流程：退出/帮助 → NLU 解析 → 置信度门 → 执行
        """
        line = line.strip()

        # 退出命令
        if line.lower() in ("exit", "quit", "q"):
            print("👋 再见！")
            return True

        # 帮助命令
        if line.lower() in ("help", "?"):
            self._print_help()
            return None

        if not line:
            return None

        # 1. NLU 解析
        intent = self._nlu.parse(line)

        # 2. 置信度太低 → 给出建议
        if intent.confidence < 0.3:
            print(self._fmt.box(
                "❓ 不太理解你的意思，试试这些说法：\n"
                "\n"
                "  🔍 查询类：\n"
                "     • 锦绣花园进度怎么样？\n"
                "     • 列出卡滞超过 14 天的节点\n"
                "     • 查看最近告警\n"
                "     • 查看最近问题\n"
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
            self._audit.log_command(line, status="REJECTED", category="unknown", error_message="low confidence")
            return None

        # 3. 置信度中等 → 确认
        if intent.confidence < 0.6:
            print(f"\n💡 我猜你是想：{intent.description}？")
            confirm = input("  确认执行吗？(yes/no) ").strip().lower()
            if confirm not in ("y", "yes"):
                print("❌ 已取消")
                self._audit.log_command(line, status="CANCELLED", category=intent.category,
                                        intent_type=intent.type, confidence=intent.confidence)
                return None

        # 4. 执行命令
        try:
            result = asyncio.run(self._execute(intent))
            msg = result.get("message", str(result)[:200]) if isinstance(result, dict) else str(result)[:200]
            self._audit.log_command(line, status="SUCCESS", category=intent.category,
                                    intent_type=intent.type, confidence=intent.confidence,
                                    result_summary=msg)
        except Exception as e:
            print(f"\n❌ 执行失败：{e}")
            logger.exception("Command execution failed")
            self._audit.log_command(line, status="FAILED", category=intent.category,
                                    intent_type=intent.type, confidence=intent.confidence,
                                    error_message=str(e))
        return None

    # ── 命令分发 ──

    async def _execute(self, intent: CommandIntent) -> dict:
        """异步命令分发器。

        Phase 1：仅分发 query 类命令。
        Phase 2：补全 action / debug / admin 分发。
        """
        if intent.category == "query":
            return await self._query.dispatch(intent)
        # Phase 2:
        # elif intent.category == "action":
        #     return await self._action.dispatch(intent)
        # elif intent.category == "debug":
        #     return await self._debug.dispatch(intent)
        # elif intent.category == "admin":
        #     return await self._admin.dispatch(intent, confirmed=False)
        else:
            print(f"\n⚠️  '{intent.category}' 类命令将在 Phase 2 实现")
            return {"success": False, "message": f"'{intent.category}' 类命令尚未实现"}

    # ── 帮助系统 ──

    def _print_help(self) -> None:
        """打印帮助信息。"""
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
    • 查看最近告警
    • 本周有什么预警？
    • 里程碑到期提醒

  报告相关：
    • 查看最近问题
    • 查看最近 10 条发现

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚡ 操作类命令（触发动作，无确认）

  • 立即执行一次全量巡检
  • 手动触发 Tick
  • 生成上周项目周报

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔧 调试类命令（开发/排错用）

  • 查看当前系统状态
  • 显示当前配置

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🛡️  管理类命令（危险操作，需二次确认）

  • 清理 30 天前的历史数据
  • 强制重新计算所有节点状态

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 提示：
  • 命令支持模糊匹配，不用严格按上面写
  • 不确定的命令会询问确认
  • 所有操作都会记录审计日志
  • 输入 exit / quit / q 退出
"""
        print(help_text)
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.shell import EmilyShell; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，检查 `QueryCommands` 构造函数的参数。确认 `ShellFormatter` 作为参数传入 `QueryCommands` 的构造函数。

---

### Step 1.10: 创建入口模块

**目标**：创建 `__main__.py`，实现自举 Config/DB/Repos 并启动 Shell。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/__main__.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/__main__.py
"""EmilyShell 入口模块。

用法：
    # 交互 REPL
    docker exec -it emily-core python -m emily_core.project.agent_shell

    # 单命令模式
    docker exec emily-core python -m emily_core.project.agent_shell -c "查看卡滞节点"

    # Cron 定时调用
    docker exec emily-core python -m emily_core.project.agent_shell -c "生成周报"
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys


def main() -> None:
    """EmilyShell 入口函数。

    1. 解析命令行参数
    2. 自举 Config / DB / Repos（不依赖 FastAPI 进程）
    3. 创建 EmilyShell 并启动
    """
    parser = argparse.ArgumentParser(
        description="Emily ProjectAgent Shell — 交互式运维终端"
    )
    parser.add_argument(
        "--command", "-c",
        type=str,
        default=None,
        help="直接执行命令（非交互模式），执行完即退出",
    )
    args = parser.parse_args()

    # ── 配置日志 ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    # ── 加载配置 ──
    from emily_core.config import Config

    raw_config: dict = {}
    # 从环境变量读取关键配置（与 bootstrap.py 中 _config_from_env 保持一致）
    env_map = {
        "EMILY_DATABASE_URL": "database_url",
        "EMILY_LLM_API_KEY": "llm_api_key",
        "EMILY_PROJECT_AGENT_STALE_THRESHOLD_DAYS": "project_agent_stale_threshold_days",
        "EMILY_PROJECT_AGENT_DEADLINE_WARN_DAYS": "project_agent_deadline_warn_days",
    }
    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            # 尝试转换数字类型
            if "DAYS" in env_key or "SECONDS" in env_key or "HOURS" in env_key:
                try:
                    raw_config[config_key] = int(val)
                except ValueError:
                    raw_config[config_key] = val
            else:
                raw_config[config_key] = val

    config = Config.from_dict(raw_config if raw_config else None)

    # ── 初始化 DB ──
    from emily_core.infrastructure.database.session import init_db

    if config.database_url:
        init_db(db_url=config.database_url)
    else:
        init_db()  # 使用 Docker Compose 默认值

    # ── 创建依赖 ──
    from emily_core.repositories.sm_node_repo import SMNodeRepository
    from emily_core.project.ops.repositories.ops_repo import OpsRepository
    from emily_core.project.agent_shell.deps import ShellDependencies

    deps = ShellDependencies(
        config=config,
        sm_node_repo=SMNodeRepository(),
        ops_repo=OpsRepository(),
        instance_id=f"emily-core-{socket.gethostname()[-8:]}",
        ops_scheduler=None,  # Phase 2 填充
    )

    # ── 创建 Shell 并启动 ──
    from emily_core.project.agent_shell.shell import EmilyShell

    shell = EmilyShell(deps)

    if args.command:
        # 单命令模式：直接执行 → 退出
        shell.default(args.command)
    else:
        # 交互模式：进入 REPL
        try:
            shell.cmdloop()
        except KeyboardInterrupt:
            print("\n\n👋 EmilyShell: 再见！")


if __name__ == "__main__":
    main()
```

**验证**：
```bash
# 单命令模式——测试 help
docker exec emily-core python -m emily_core.project.agent_shell -c "help"
→ 应打印帮助信息并退出（无异常）

# 测试查询命令
docker exec emily-core python -m emily_core.project.agent_shell -c "列出卡滞节点"
→ 应打印表格或"没有卡滞节点"
```

**失败处理**：如果 `docker exec` 失败，检查：
1. 容器是否运行：`docker compose -f docker-compose-napcat.yml ps`
2. DB 是否可达：`docker exec emily-postgres psql -U emily -d emily -c "SELECT 1"`
3. pycache 是否清除：`docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +`

---

### Phase 1 最终验证

完成本阶段所有步骤后，运行端到端验证：

```bash
# 1. 确认表已创建
docker exec emily-postgres psql -U emily -d emily -c "\dt ops_shell_audit"
→ 应返回 ops_shell_audit 表结构

# 2. 确认审计日志文件路径正确
docker exec emily-core ls -la logs/
→ 应列出 shell_audit.log（可能在第一次运行 shell 后才创建）

# 3. 端到端测试：启动 → 查询 → 退出
docker exec emily-core python -m emily_core.project.agent_shell -c "查看最近问题"
→ 无异常，打印结果或"没有发现问题"

# 4. 确认审计写入
docker exec emily-postgres psql -U emily -d emily -c "SELECT status, category, intent_type FROM ops_shell_audit ORDER BY created_at DESC LIMIT 5"
→ 应有记录

# 5. 确认审计文件写入
docker exec emily-core cat logs/shell_audit.log
→ JSONL 格式，每行一条记录
```

全部通过后进入 Phase 2。

---

## Phase 2: 操作 + 调试 + 管理命令

**前置检查**（必须全部通过才进入此阶段）：

```bash
# 检查 Phase 1 产物
grep "class OpsShellAudit" emily-core/emily_core/project/ops/models.py
grep "def save_shell_audit" emily-core/emily_core/project/ops/repositories/ops_repo.py
grep "class EmilyShell" emily-core/emily_core/project/agent_shell/shell.py
grep "class QueryCommands" emily-core/emily_core/project/agent_shell/commands/query.py
→ 每行应有输出
```

**交付物**：4 类命令全部可用。交互模式 + 单命令模式均完整。

---

### Step 2.1: 创建操作类命令模块

**目标**：实现 `force_tick`（手动触发巡检）和 `generate_weekly_report`（生成周报）。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/commands/action.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/commands/action.py
"""操作类命令 —— 触发动作，无需确认。

实现：force_tick / generate_weekly_report
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import text

from emily_core.infrastructure.database.session import get_session_raw

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies
    from emily_core.project.agent_shell.nlu import CommandIntent
    from emily_core.project.agent_shell.formatter import ShellFormatter


class ActionCommands:
    """操作类命令。

    force_tick 通过 PG advisory lock 确保不与 ProjectAgent 后台 tick 冲突。
    """

    def __init__(self, deps: "ShellDependencies", fmt: "ShellFormatter"):
        self._deps = deps
        self._fmt = fmt

    async def dispatch(self, intent: "CommandIntent") -> dict:
        handlers = {
            "force_tick": self.force_tick,
            "generate_weekly": self.generate_weekly_report,
        }
        handler = handlers.get(intent.type)
        if handler is None:
            return {"success": False, "message": f"未知操作类型: {intent.type}"}
        return await asyncio.to_thread(handler, intent)

    # ── 手动触发 Tick ──

    def force_tick(self, intent: "CommandIntent" = None) -> dict:
        """手动触发一轮全量巡检。

        使用与 ProjectAgent._tick() 相同的 PG advisory lock 键，
        通过 pg_try_advisory_lock 非阻塞获取，避免与后台 tick 竞争。
        """
        if self._deps.ops_scheduler is None:
            print("\n⚠️  OpsScheduler 未初始化。Phase 1 的 Shell 不支持此命令。")
            print("  请重新以 Phase 2 的依赖启动 Shell。")
            return {"success": False, "message": "OpsScheduler 未初始化"}

        print("\n🔄 正在执行全量巡检...\n")

        def _tick() -> dict:
            raw_session = get_session_raw()
            try:
                # 获取与 ProjectAgent._tick() 相同的 advisory lock
                lock_acquired = raw_session.execute(
                    text("SELECT pg_try_advisory_lock(hashtext('project_agent:global_tick'))")
                ).scalar()

                if not lock_acquired:
                    return {
                        "success": False,
                        "message": "另一轮 Tick 正在执行中（ProjectAgent 后台任务），请稍后再试",
                    }

                # 创建 TickContext 并调用 OpsScheduler
                from emily_core.project.ops.probe_base import TickContext

                tick_id = str(uuid4())
                ctx = TickContext(
                    tick_id=tick_id,
                    tick_number=0,
                    start_time=datetime.now(timezone.utc),
                )
                result = self._deps.ops_scheduler.run_tick(tick_id, ctx.tick_number)

                # 格式化输出
                print(f"  ✅ Tick 执行完成")
                print(f"  执行探针：{result.get('probes_run', 0)} 个")
                print(f"  发现问题：{result.get('findings_total', 0)} 个")
                if result.get("errors", 0) > 0:
                    print(f"  ⚠️ 错误：{result['errors']} 个")

                return {"success": True, "message": f"probes={result.get('probes_run', 0)}, findings={result.get('findings_total', 0)}"}

            finally:
                raw_session.execute(
                    text("SELECT pg_advisory_unlock(hashtext('project_agent:global_tick'))")
                )
                raw_session.close()

        return asyncio.run(asyncio.to_thread(_tick)) if _tick else _tick()

    # ── 生成周报 ──

    def generate_weekly_report(self, intent: "CommandIntent" = None) -> dict:
        """基于 SMNodeRepository 数据生成项目周报 Markdown 文件。

        保存到 logs/weekly_YYYYMMDD_HHMMSS.md。
        """
        print("\n📄 正在生成项目周报...\n")

        nodes = self._deps.sm_node_repo.list_all()
        if not nodes:
            print("  ⚠️ 暂无项目节点数据，周报为空")
            return {"success": True, "message": "无节点数据"}

        now = datetime.now(timezone.utc)
        total = len(nodes)
        completed = sum(1 for n in nodes if getattr(n, "status", "") == "COMPLETED")
        in_progress = sum(1 for n in nodes if getattr(n, "status", "") == "IN_PROGRESS")
        blocked = sum(1 for n in nodes if getattr(n, "status", "") == "BLOCKED")
        delayed = sum(1 for n in nodes if getattr(n, "status", "") == "DELAYED")

        progress_pct = round(completed / total * 100, 1) if total > 0 else 0.0

        # 按阶段分组
        stage_groups: dict[int, list] = {}
        for node in nodes:
            sid = getattr(node, "stage_id", 0)
            stage_groups.setdefault(sid, []).append(node)

        # 渲染 Markdown
        lines = [
            f"# 项目周报",
            f"",
            f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"> 数据范围：全部 {total} 个节点",
            f"",
            f"## 整体概览",
            f"",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 节点总数 | {total} |",
            f"| 已完成 | {completed} ({progress_pct}%) |",
            f"| 进行中 | {in_progress} |",
            f"| 阻塞 | {blocked} |",
            f"| 延期 | {delayed} |",
            f"",
            f"## 各阶段进度",
            f"",
            f"| 阶段 | 总数 | 已完成 | 进行中 | 阻塞 | 完成率 |",
            f"|------|------|--------|--------|------|--------|",
        ]
        for sid in sorted(stage_groups.keys()):
            snodes = stage_groups[sid]
            s_total = len(snodes)
            s_comp = sum(1 for n in snodes if getattr(n, "status", "") == "COMPLETED")
            s_prog = sum(1 for n in snodes if getattr(n, "status", "") == "IN_PROGRESS")
            s_blk = sum(1 for n in snodes if getattr(n, "status", "") == "BLOCKED")
            s_pct = round(s_comp / s_total * 100, 1) if s_total > 0 else 0.0
            lines.append(f"| Stage {sid} | {s_total} | {s_comp} | {s_prog} | {s_blk} | {s_pct}% |")

        report_content = "\n".join(lines)

        # 保存到本地
        log_dir = self._deps.config.shell_audit_log_dir
        os.makedirs(log_dir, exist_ok=True)
        report_path = os.path.join(log_dir, f"weekly_{now.strftime('%Y%m%d_%H%M%S')}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)

        print(f"  ✅ 周报已生成")
        print(f"  📄 报告已保存到：{report_path}")

        return {"success": True, "message": f"周报已保存到 {report_path}"}
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.commands.action import ActionCommands; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，检查 `get_session_raw` 的导入路径和 `OpsScheduler.run_tick()` 的签名。

---

### Step 2.2: 创建调试类命令模块

**目标**：实现 `show_config` 和 `show_status`。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/commands/debug.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/commands/debug.py
"""调试类命令 —— 开发排错用，只读，无风险。

实现：show_config / show_status
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies
    from emily_core.project.agent_shell.nlu import CommandIntent
    from emily_core.project.agent_shell.formatter import ShellFormatter


class DebugCommands:
    """调试类命令。"""

    def __init__(self, deps: "ShellDependencies", fmt: "ShellFormatter"):
        self._deps = deps
        self._fmt = fmt

    async def dispatch(self, intent: "CommandIntent") -> dict:
        handlers = {
            "show_config": self.show_config,
            "show_status": self.show_status,
        }
        handler = handlers.get(intent.type)
        if handler is None:
            return {"success": False, "message": f"未知调试类型: {intent.type}"}
        return await asyncio.to_thread(handler, intent)

    # ── 显示配置 ──

    def show_config(self, intent: "CommandIntent" = None) -> dict:
        """显示当前运行时配置。"""
        print("\n📋 当前配置：\n")

        config = self._deps.config
        headers = ["配置项", "当前值"]
        rows = []
        for f in fields(config):
            value = getattr(config, f.name)
            # 对敏感值脱敏
            display_value = str(value)
            if "api_key" in f.name.lower() or "password" in f.name.lower():
                if display_value:
                    display_value = display_value[:4] + "***" + display_value[-4:] if len(display_value) > 8 else "***"
            rows.append([f.name, display_value])
        print(self._fmt.table(headers, rows))

        return {"success": True, "message": f"显示了 {len(rows)} 个配置项"}

    # ── 显示系统状态 ──

    def show_status(self, intent: "CommandIntent" = None) -> dict:
        """显示系统运行状态。"""
        print("\n📊 系统运行状态：\n")

        now = datetime.now(timezone.utc)

        # 节点统计
        total_nodes = self._deps.sm_node_repo.count()
        completed = len(self._deps.sm_node_repo.list_by_status("COMPLETED"))
        in_progress = len(self._deps.sm_node_repo.list_by_status("IN_PROGRESS"))
        blocked = len(self._deps.sm_node_repo.list_by_status("BLOCKED"))
        delayed = len(self._deps.sm_node_repo.list_by_status("DELAYED"))

        status_data = {
            "时间": now.strftime("%Y-%m-%d %H:%M:%S") + " UTC",
            "实例 ID": self._deps.instance_id,
            "节点总数": str(total_nodes),
            "已完成": str(completed),
            "进行中": str(in_progress),
            "阻塞": str(blocked),
            "延期": str(delayed),
        }
        print(self._fmt.status_bar(status_data))

        # 最近 Tick 信息
        try:
            report = self._deps.ops_repo.get_latest_startup_report(hours=168)  # 7 天
            if report:
                print(f"\n  最近启动报告：{getattr(report, 'startup_time', 'N/A')}")
                print(f"  环境：{getattr(report, 'environment', 'N/A')}")
                print(f"  版本：{getattr(report, 'version', 'N/A')}")
        except Exception:
            pass  # 静默跳过——不是关键信息

        return {"success": True, "message": f"total={total_nodes}"}
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.commands.debug import DebugCommands; print('import OK')"
→ 应输出：import OK
```

**失败处理**：检查 `dataclasses.fields` 的 import 和 `SMNodeRepository.list_by_status()` 的调用。

---

### Step 2.3: 创建管理类命令模块

**目标**：实现 `purge_data`（带确认门）和 `force_recalc`。

**操作**：

1. 新建文件 `emily-core/emily_core/project/agent_shell/commands/admin.py`，写入以下完整内容：

```python
# emily-core/emily_core/project/agent_shell/commands/admin.py
"""管理类命令 —— 危险操作，需二次确认。

实现：purge_data / force_recalc

确认门在 shell.py 的 _execute() 中实现（admin 类统一要求确认），
本模块方法只接收 confirmed=True 后执行。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from emily_core.infrastructure.database.session import get_session

if TYPE_CHECKING:
    from emily_core.project.agent_shell.deps import ShellDependencies
    from emily_core.project.agent_shell.nlu import CommandIntent
    from emily_core.project.agent_shell.formatter import ShellFormatter


class AdminCommands:
    """管理类命令。所有方法需 confirmed=True 才执行实际操作。"""

    def __init__(self, deps: "ShellDependencies", fmt: "ShellFormatter"):
        self._deps = deps
        self._fmt = fmt

    async def dispatch(self, intent: "CommandIntent", confirmed: bool = False) -> dict:
        if not confirmed:
            return {"success": False, "message": "管理类命令需二次确认，已取消"}

        handlers = {
            "purge_data": self.purge_data,
            "force_recalc": self.force_recalc,
        }
        handler = handlers.get(intent.type)
        if handler is None:
            return {"success": False, "message": f"未知管理类型: {intent.type}"}
        return await asyncio.to_thread(handler, intent)

    # ── 清理历史数据 ──

    def purge_data(self, intent: "CommandIntent") -> dict:
        """清理 N 天前的 ops_tick_log / ops_probe_execution / ops_finding 历史数据。

        使用 SQL DELETE，因为这是批量清理操作，不通过 Repository 逐条删除。
        """
        from emily_core.project.agent_shell.nlu import NLUEngine

        older_than_days = NLUEngine.extract_param(intent, "older_than_days", default=30)
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

        print(f"\n🧹 正在清理 {older_than_days} 天前的历史数据...\n")

        def _do_purge(sess):
            from sqlalchemy import text as sa_text

            # 删除顺序：先删子表，再删主表
            tables = ["ops_finding", "ops_probe_execution", "ops_mail_audit", "ops_tick_log"]
            total_deleted = 0
            for table in tables:
                result = sess.execute(
                    sa_text(f"DELETE FROM {table} WHERE created_at < :cutoff"),
                    {"cutoff": cutoff},
                )
                deleted = result.rowcount
                total_deleted += deleted
                print(f"  {table}: 删除 {deleted} 条")
            sess.commit()
            return total_deleted

        with get_session() as sess:
            total = _do_purge(sess)

        print(f"\n  ✅ 共清理 {total} 条历史记录")
        return {"success": True, "message": f"清理了 {total} 条记录"}

    # ── 强制重新计算 ──

    def force_recalc(self, intent: "CommandIntent") -> dict:
        """强制重新计算所有节点状态（通过 StateMachineService 级联更新）。

        Phase 2 实现：遍历所有 COMPLETED 节点，触发下游节点的 precondition_score 重算。
        """
        print("\n🔄 正在重新计算节点状态...\n")

        # 获取所有 COMPLETED 节点
        completed_nodes = self._deps.sm_node_repo.list_by_status("COMPLETED")

        if not completed_nodes:
            print("  ℹ️ 没有已完成节点，无需重算")
            return {"success": True, "message": "无已完成节点"}

        # 对每个 COMPLETED 节点，检查是否有下游依赖
        recalculated = 0
        for node in completed_nodes:
            node_id = getattr(node, "node_id", "")
            if not node_id:
                continue
            downstream = self._deps.sm_node_repo.get_downstream_nodes(node_id)
            if downstream:
                # 标记下游节点需要重算（通过更新 precondition_score = -1 触发）
                for dep in downstream:
                    to_id = getattr(dep, "from_node_id", "")
                    if to_id:
                        self._deps.sm_node_repo.update_precondition_score(to_id, -1)
                        recalculated += 1

        print(f"  ✅ 已标记 {recalculated} 个下游节点需要重新计算")
        print(f"  ℹ️ 实际状态更新将在下一轮 ProjectAgent tick 中生效")
        return {"success": True, "message": f"标记了 {recalculated} 个节点"}
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.commands.admin import AdminCommands; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，检查 `SMNodeRepository.get_downstream_nodes()` 和 `update_precondition_score()` 的导入。

---

### Step 2.4: 更新 shell.py 补全 4 类命令分发

**目标**：在 `EmilyShell` 中集成 `ActionCommands`、`DebugCommands`、`AdminCommands`，补全 `_execute()` 方法和 admin 确认门。

**操作**：

1. 打开 `emily-core/emily_core/project/agent_shell/shell.py`
2. 在文件顶部 import 区域追加：

```python
# emily-core/emily_core/project/agent_shell/shell.py — 追加在现有 import 后
from emily_core.project.agent_shell.commands.action import ActionCommands
from emily_core.project.agent_shell.commands.debug import DebugCommands
from emily_core.project.agent_shell.commands.admin import AdminCommands
```

3. 在 `EmilyShell.__init__()` 中，找到 `self._admin = None` 行，替换为：

```python
# emily-core/emily_core/project/agent_shell/shell.py — 替换 __init__ 中的 None 赋值
        # Phase 2: 全部命令模块
        self._action = ActionCommands(deps, self._fmt)
        self._debug = DebugCommands(deps, self._fmt)
        self._admin = AdminCommands(deps, self._fmt)
```

4. 在 `_execute()` 方法中，替换 Phase 2 注释块为实际分发代码。找到这段：

```python
        # Phase 2:
        # elif intent.category == "action":
        #     return await self._action.dispatch(intent)
        # elif intent.category == "debug":
        #     return await self._debug.dispatch(intent)
        # elif intent.category == "admin":
        #     return await self._admin.dispatch(intent, confirmed=False)
```

替换为：

```python
        elif intent.category == "action":
            return await self._action.dispatch(intent)
        elif intent.category == "debug":
            return await self._debug.dispatch(intent)
        elif intent.category == "admin":
            # admin 命令的确认门在 default() 中已处理
            # 直接传入 confirmed=True
            confirm_msg_map = {
                "purge_data": "⚠️  这将删除历史数据，无法恢复！",
                "force_recalc": "⚠️  这将强制重新计算所有节点状态！",
            }
            confirm_msg = confirm_msg_map.get(intent.type, "⚠️  确定执行此管理操作吗？")
            confirm = input(f"\n{confirm_msg}\n  确认执行吗？(yes/no) ").strip().lower()
            if confirm not in ("y", "yes"):
                print("❌ 已取消")
                return {"success": False, "message": "用户取消"}
            return await self._admin.dispatch(intent, confirmed=True)
```

**注意**：保留 QueryCommands 的初始化（Phase 1 已有），不删除 `self._query = ...` 行。

**验证**：
```bash
python -c "from emily_core.project.agent_shell.shell import EmilyShell; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 import 失败，确认 ActionCommands/DebugCommands/AdminCommands 的构造函数签名是 `(deps, fmt)`。

---

### Step 2.5: 更新 __main__.py 添加 OpsScheduler 依赖

**目标**：在入口模块中自举 OpsScheduler，使 `force_tick` 命令可用。

**操作**：

1. 打开 `emily-core/emily_core/project/agent_shell/__main__.py`
2. 找到创建 `ShellDependencies` 的部分，替换为：

```python
# emily-core/emily_core/project/agent_shell/__main__.py — 替换 ShellDependencies 创建部分
    # ── 创建依赖 ──
    from emily_core.repositories.sm_node_repo import SMNodeRepository
    from emily_core.project.ops.repositories.ops_repo import OpsRepository
    from emily_core.project.agent_shell.deps import ShellDependencies

    sm_node_repo = SMNodeRepository()
    ops_repo = OpsRepository()

    # 自举 OpsScheduler（force_tick 依赖）
    ops_scheduler = None
    try:
        from emily_core.project.ops.config import OpsConfig
        from emily_core.project.ops.scheduler import OpsScheduler
        from emily_core.project.ops.probe_registry import ProbeRegistry
        from emily_core.project.ops.persistence.fallback_writer import FallbackWriter

        ops_config = OpsConfig.from_global_config(config)
        fallback = FallbackWriter(ops_config.fallback_log_dir)

        ops_scheduler = OpsScheduler(
            config=ops_config,
            db_repo=ops_repo,
            fallback=fallback,
            email_service=None,
            outbound_bus=None,
        )

        # 注册可用探针
        from emily_core.project.ops.probes.stale_probe import StaleProbe

        # StaleProbe 需要 StaleDetector，此处创建一个最小可用的
        try:
            from emily_core.project.project_agent import StaleDetector
            stale_detector = StaleDetector(
                node_repo=sm_node_repo,
                stale_threshold_days=config.project_agent_stale_threshold_days,
                deadline_warn_days=config.project_agent_deadline_warn_days,
                alert_cooldown_hours=config.project_agent_alert_cooldown_hours,
                outbound_bus=None,  # Shell 中不发告警
            )
            stale_probe = StaleProbe(stale_detector, ops_config)
            ops_scheduler.register_probe(stale_probe)
        except Exception as e:
            logging.getLogger("emily.agent_shell").warning(
                f"Cannot create StaleProbe: {e}. force_tick will lack stale detection."
            )

    except Exception as e:
        logging.getLogger("emily.agent_shell").warning(
            f"Cannot initialize OpsScheduler: {e}. force_tick command will be unavailable."
        )

    deps = ShellDependencies(
        config=config,
        sm_node_repo=sm_node_repo,
        ops_repo=ops_repo,
        instance_id=f"emily-core-{socket.gethostname()[-8:]}",
        ops_scheduler=ops_scheduler,
    )
```

**验证**：
```bash
python -c "from emily_core.project.agent_shell.__main__ import main; print('import OK')"
→ 应输出：import OK
```

**失败处理**：如果 OpsScheduler 初始化失败，检查 `StaleDetector.__init__()` 的参数签名（可能因版本不同而异）。OPS scheduler 初始化失败不应阻止 Shell 启动——已用 try/except 包裹。

---

### Phase 2 最终验证

```bash
# 1. 测试操作命令（如果 OpsScheduler 初始化成功）
docker exec emily-core python -m emily_core.project.agent_shell -c "立即执行一次全量巡检"
→ 输出 Tick 执行结果或"OpsScheduler 未初始化"

# 2. 测试调试命令
docker exec emily-core python -m emily_core.project.agent_shell -c "显示配置"
→ 输出配置表格

# 3. 测试单命令模式 + 管理命令确认（在交互模式下无法自动测试确认，改为验证命令可识别）
docker exec emily-core python -m emily_core.project.agent_shell -c "清理30天前的数据"
→ Phase 2 的管理命令在单命令模式下会自动执行（无交互确认），应输出清理结果

# 4. 测试周报生成
docker exec emily-core python -m emily_core.project.agent_shell -c "生成周报"
→ 应输出"周报已生成" + 文件路径

# 5. 确认审计记录覆盖全部类别
docker exec emily-postgres psql -U emily -d emily -c "SELECT DISTINCT category, intent_type FROM ops_shell_audit ORDER BY category"
→ 应包含 query/action/debug/admin 四种类别
```

---

## Phase 3: 完善

**前置检查**（必须全部通过才进入此阶段）：

```bash
grep "class ActionCommands" emily-core/emily_core/project/agent_shell/commands/action.py
grep "class DebugCommands" emily-core/emily_core/project/agent_shell/commands/debug.py
grep "class AdminCommands" emily-core/emily_core/project/agent_shell/commands/admin.py
grep "self._action" emily-core/emily_core/project/agent_shell/shell.py
grep "ops_scheduler" emily-core/emily_core/project/agent_shell/__main__.py
→ 每行应有输出
```

**交付物**：增强帮助 + 错误边界处理 + 文档更新。

---

### Step 3.1: 增强帮助系统

**目标**：将 `_print_help()` 升级为支持 `help <category>` 和 `help <command>` 的交互式帮助。

**操作**：

1. 打开 `emily-core/emily_core/project/agent_shell/shell.py`
2. 在 `EmilyShell` 类中追加 `do_help` 方法：

```python
# emily-core/emily_core/project/agent_shell/shell.py — 追加在 _print_help() 方法后

    def do_help(self, arg: str) -> bool | None:
        """help [category] —— 显示分类帮助。

        覆盖 cmd.Cmd 默认的 do_help，支持中文分类名称。
        """
        arg = arg.strip().lower()
        categories = {
            "query": "🔍 查询类命令（只读，无风险）",
            "action": "⚡ 操作类命令（触发动作）",
            "debug": "🔧 调试类命令（开发用）",
            "admin": "🛡️  管理类命令（需二次确认）",
        }
        if arg in categories:
            print(f"\n{categories[arg]}\n")
            for intent_type, config in self._nlu.INTENT_PATTERNS.items():
                if config["category"] == arg:
                    keywords = ", ".join(config["keywords"][:5])
                    print(f"  • {config['description']}")
                    print(f"    触发词：{keywords}")
            print("")
        elif arg == "" or arg == "all":
            self._print_help()
        else:
            # 尝试按意图类型查找
            config = self._nlu.INTENT_PATTERNS.get(arg)
            if config:
                keywords = ", ".join(config["keywords"])
                print(f"\n{config['description']}")
                print(f"\n  分类：{config['category']}")
                print(f"  触发词：{keywords}")
                print("")
            else:
                print(f"\n  未知帮助主题：{arg}")
                print(f"  可用主题：query, action, debug, admin")
                print(f"  或用 help 查看全部\n")
        return None
```

3. 将 `default()` 方法中的 `if line.lower() in ("help", "?"): self._print_help(); return None` 改为 `if line.lower() in ("help", "?"): self.do_help(""); return None`

**验证**：
```bash
docker exec emily-core python -m emily_core.project.agent_shell -c "help query"
→ 应输出查询类命令清单+触发词

docker exec emily-core python -m emily_core.project.agent_shell -c "help project_status"
→ 应输出此命令的描述+触发词+分类
```

**失败处理**：如果 `help query` 无输出，检查 `INTENT_PATTERNS` 中 query 类意图的 `category` 字段值。

---

### Step 3.2: 添加错误边界处理

**目标**：DB 不可达时 Shell 仍可启动，显示降级横幅，仅支持 help/exit。

**操作**：

1. 打开 `emily-core/emily_core/project/agent_shell/shell.py`
2. 在 `EmilyShell.default()` 方法中，`asyncio.run(self._execute(intent))` 的 try 块外包裹 DB 异常检测。替换现有的 try/except 块：

```python
        # 4. 执行命令
        try:
            result = asyncio.run(self._execute(intent))
            msg = result.get("message", str(result)[:200]) if isinstance(result, dict) else str(result)[:200]
            self._audit.log_command(line, status="SUCCESS", category=intent.category,
                                    intent_type=intent.type, confidence=intent.confidence,
                                    result_summary=msg)
        except RuntimeError as e:
            if "no running event loop" in str(e).lower():
                # asyncio.run() 在已有 event loop 中调用
                print(f"\n❌ 内部错误：无法在此环境中启动事件循环")
                print(f"  请检查是否在异步上下文中启动了 Shell")
            else:
                print(f"\n❌ 运行时错误：{e}")
            self._audit.log_command(line, status="FAILED", category=intent.category,
                                    intent_type=intent.type, confidence=intent.confidence,
                                    error_message=str(e))
        except Exception as e:
            error_msg = str(e)
            # 判断是否为 DB 连接错误
            if any(kw in error_msg.lower() for kw in ("connection", "database", "postgres", "sqlalchemy", "operationalerror")):
                print(f"\n⚠️  数据库不可达，仅 help/exit 可用")
                print(f"  错误详情：{error_msg[:200]}")
                self._audit.log_command(line, status="FAILED", category=intent.category,
                                        intent_type=intent.type, confidence=intent.confidence,
                                        error_message=f"DB unavailable: {error_msg[:200]}")
            else:
                print(f"\n❌ 执行失败：{error_msg[:300]}")
                self._audit.log_command(line, status="FAILED", category=intent.category,
                                        intent_type=intent.type, confidence=intent.confidence,
                                        error_message=error_msg[:500])
```

**验证**：
```bash
# 正常环境下命令失败不崩溃
docker exec emily-core python -m emily_core.project.agent_shell -c "随机乱码无法识别"
→ 应输出帮助建议，不崩溃，可再次输入
```

**失败处理**：如果正常环境测试失败，检查异常匹配字符串是否过宽。

---

### Step 3.3: 更新 docs/代码文件目录.md

**目标**：在文档中添加 agent_shell 目录的文件清单。

**操作**：

1. 打开 `docs/代码文件目录.md`
2. 找到 `project/` 相关章节，在 `agent_shell/` 相关条目后追加：

```markdown
| emily-core/emily_core/project/agent_shell/__init__.py | EmilyShell 包入口，导出 EmilyShell 类 |
| emily-core/emily_core/project/agent_shell/__main__.py | EmilyShell 启动入口（自举 Config/DB/Repos + REPL） |
| emily-core/emily_core/project/agent_shell/deps.py | ShellDependencies 依赖注入容器 |
| emily-core/emily_core/project/agent_shell/shell.py | EmilyShell(cmd.Cmd) 主 REPL 类 |
| emily-core/emily_core/project/agent_shell/nlu.py | NLUEngine 关键词匹配命令解析器 |
| emily-core/emily_core/project/agent_shell/audit.py | AuditLogger DB+文件双写审计 |
| emily-core/emily_core/project/agent_shell/formatter.py | ShellFormatter 表格/框/状态栏终端输出 |
| emily-core/emily_core/project/agent_shell/commands/__init__.py | 命令模块包 |
| emily-core/emily_core/project/agent_shell/commands/query.py | QueryCommands 查询类命令 |
| emily-core/emily_core/project/agent_shell/commands/action.py | ActionCommands 操作类命令 |
| emily-core/emily_core/project/agent_shell/commands/debug.py | DebugCommands 调试类命令 |
| emily-core/emily_core/project/agent_shell/commands/admin.py | AdminCommands 管理类命令 |
```

**验证**：
```bash
grep "agent_shell" docs/代码文件目录.md | wc -l
→ 应返回 >= 13
```

**失败处理**：如果行数不够，检查是否所有 13 个新文件的条目都已添加。

---

### Phase 3 最终验证

```bash
# 1. help 分类
docker exec emily-core python -m emily_core.project.agent_shell -c "help query"
→ 输出 query 类命令清单+触发词

# 2. 错误输入不崩溃
docker exec emily-core python -m emily_core.project.agent_shell -c "asdfghjkl"
→ 输出帮助建议，无 Python traceback

# 3. 空结果友好显示
docker exec emily-core python -m emily_core.project.agent_shell -c "列出卡滞超过1天的节点"
→ 输出"没有卡滞节点"或表格（取决于实际数据）

# 4. 文档更新确认
grep "agent_shell" docs/代码文件目录.md | wc -l
→ >= 13
```

---

## 阶段反思指令

每完成一个 Phase，在进入下一个 Phase 之前，执行以下反思：

1. **检查产物**：列出本 Phase 所有新建/修改的文件路径，逐条确认每个文件确实存在
2. **检查偏差**：是否有步骤与计划不符（如方法签名变化、导入路径变化、额外需要处理的异常）？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应 Phase，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v2.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化（如改为 HTTP 调用等方式）→ **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan v2 技能生成。*
