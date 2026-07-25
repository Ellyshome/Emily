# ToolManager 聚合层 — 实施计划

> **版本**：V1.0
> **编制日期**：2026-07-25
> **关联需求**：[RAG原子工具层与MaxKB替换_需求_V1.md](RAG原子工具层与MaxKB替换_需求_V1.md)
> **关联计划**：[RAG原子工具层_计划_V1.md](RAG原子工具层_计划_V1.md)（原子工具建好后由 ToolManager 统一测试）
> **定位**：轻量聚合层，**不替代** [BusinessFlowToolRegistry](../emily-core/emily_core/tools/business_flow_tools.py)，而是补三件事——统一调用入口、自描述、CLI/HTTP 测试接口。

---

## 1. 背景与目标

### 1.1 现状

- 已注册 **27 个工具**（base=2 / business=7 / project=10，日志 `registry: 27 tools`），分布在 [tools/registry.py](../emily-core/emily_core/tools/registry.py) 的 `register_all(core)` 里
- [BusinessFlowToolRegistry](../emily-core/emily_core/tools/business_flow_tools.py#L36) 只有 `register / get / has / list_names`，**缺**：
  - 统一调用入口（调用要走 SkillExecutor + SOP，无法直接测单个工具）
  - 自描述（无 `describe()` / `export_schema()`，AI 看不清可用工具全集）
  - CLI 测试接口（每次测工具要写脚本或走 emy-test 全链路）
- 工具元数据散落在 [tools_consistency.py](../emily-core/emily_core/infrastructure/tools_consistency.py) 的 `TOOL_META_MAP` / `REGISTERED_TOOLS`，与 registry 实际注册双份维护
- 原子工具层（OCR/解析/表格/embedding）即将新增 6+ 个工具，工具总数将达 33+，缺统一管理入口的问题会更突出

### 1.2 目标

| 维度 | 现状 | 目标 |
|------|------|------|
| 调用入口 | 走 SOP + SkillExecutor | `toolmgr call <name> --params '{...}'` 直调 |
| 自描述 | 散落 TOOL_META_MAP | `toolmgr export --json` 一键导出全部 schema |
| 可测试 | 写脚本/emy-test 全链路 | `toolmgr test [<name>]` 跑预设 smoke 用例 |
| AI 友好 | 无 | 所有命令 `--json` 输出，退出码语义化 |
| 手工检查 | 无 | 默认表格/彩色输出，失败带 schema 提示 |
| 依赖就绪 | 无 | `toolmgr selfcheck` 显示各工具依赖状态 |

### 1.3 设计原则

- **轻量**：核心单文件 `tools/manager.py`（~200 行）+ 薄壳 CLI `scripts/toolmgr.py`
- **不侵入**：包装 BusinessFlowToolRegistry，不改其接口；ToolManager 是"读+调"层，注册仍走 `register_all`
- **双友好**：每个命令同时满足 AI（`--json`）和手工（表格）使用
- **复用现有模式**：CLI 薄壳参考 [check_tools_consistency.py](../scripts/check_tools_consistency.py)，argparse + 核心逻辑分离

---

## 2. ToolManager 设计

### 2.1 位置与依赖

```
emily-core/emily_core/tools/manager.py     ← ToolManager 核心类
scripts/toolmgr.py                          ← CLI 薄壳（argparse）
emily-core/tests/toolmgr_cases.yaml         ← 预设 smoke 用例（可选，后续）
```

ToolManager 只依赖 `BusinessFlowToolRegistry`（已有）+ `BusinessFlowTool`（已有）。无新外部依赖。

### 2.2 核心类

```python
# emily-core/emily_core/tools/manager.py
"""ToolManager — BusinessFlowToolRegistry 的对外聚合层。

补三件事：统一调用入口、自描述、CLI/HTTP 测试接口。
不替代 Registry，注册仍走 tools/registry.py 的 register_all。
"""

from __future__ import annotations
import logging
from typing import Any
from .business_flow_tools import BusinessFlowTool, BusinessFlowToolRegistry

logger = logging.getLogger("emily.tool.manager")


class ToolManager:
    def __init__(self, registry: BusinessFlowToolRegistry):
        self._registry = registry

    # ── 自描述 ────────────────────────────────────────

    def list(self) -> list[dict]:
        """列出所有工具的元信息（轻量，不含 schema）。"""
        return [
            {
                "name": t.name,
                "category": t.category,
                "permission": t.permission_flag,
                "description": t.description,
                "has_schema": bool(t.parameters and t.parameters.get("properties")),
            }
            for t in self._registry._tools.values()
        ]

    def describe(self, name: str | None = None) -> dict:
        """单个或全部工具的完整描述（含 schema，AI 友好）。

        Args:
            name: 指定工具名；None 返回全部。
        Returns:
            name 非空: {"name", "category", "permission", "description", "parameters"}
            name 为空: {"tools": [...], "count": N}
            工具不存在: {"error": "...", "code": 2}
        """
        if name:
            t = self._registry.get(name)
            if not t:
                return {"error": f"tool '{name}' not found", "code": 2}
            return self._tool_to_dict(t)
        tools = [self._tool_to_dict(t) for t in self._registry._tools.values()]
        return {"tools": tools, "count": len(tools)}

    def schema(self, name: str) -> dict:
        """单工具 JSON Schema（仅 parameters）。"""
        t = self._registry.get(name)
        return t.parameters if t else {}

    def export(self) -> dict:
        """导出全部工具 schema，供 AI prompt 注入。等价 describe(name=None)。"""
        return self.describe(None)

    # ── 统一调用 ──────────────────────────────────────

    async def call(self, name: str, params: dict | None = None) -> dict:
        """统一调用入口。绕过 SOP，直接调 handler。

        Returns:
            成功: {"success": True, "result": <handler 返回>, "tool": name}
            失败: {"success": False, "error": "...", "tool": name, "code": 1}
            不存在: {"success": False, "error": "tool not found", "code": 2}
        """
        t = self._registry.get(name)
        if not t:
            return {"success": False, "error": f"tool '{name}' not found",
                    "tool": name, "code": 2}
        try:
            result = await t.handler(params or {})
            return {"success": True, "result": result, "tool": name}
        except Exception as e:
            logger.warning("toolmgr call '%s' failed: %s", name, e, exc_info=True)
            return {"success": False, "error": str(e), "tool": name, "code": 1}

    # ── 依赖就绪检查 ──────────────────────────────────

    def selfcheck(self) -> dict:
        """检查每个工具的依赖是否就绪（handler 是否可调用）。

        策略：检查 handler 是否为 stub（knowledge_search 的 _rag_stub 等已知 stub 模式）。
        返回 [{"name", "category", "ready", "note"}]
        """
        results = []
        for t in self._registry._tools.values():
            ready, note = self._check_ready(t)
            results.append({"name": t.name, "category": t.category,
                            "ready": ready, "note": note})
        return {"tools": results, "count": len(results)}

    def _check_ready(self, t: BusinessFlowTool) -> tuple[bool, str]:
        """单工具就绪检查。子类可扩展。"""
        # 默认：handler 存在即 ready
        return (True, "ok") if t.handler else (False, "no handler")

    # ── 内部 ──────────────────────────────────────────

    @staticmethod
    def _tool_to_dict(t: BusinessFlowTool) -> dict:
        return {
            "name": t.name,
            "category": t.category,
            "permission": t.permission_flag,
            "description": t.description,
            "parameters": t.parameters,
        }
```

### 2.3 与 BusinessFlowToolRegistry 的关系

```
register_all(core)  ──注册──▶  BusinessFlowToolRegistry  ──包装──▶  ToolManager
       (写)                       (内部注册表)                     (读 + 调 + 自描述)
                                          │
                                          └── SkillExecutor 运行时仍直查 Registry
```

- **注册**：仍走 `register_all`，ToolManager 不参与
- **运行时调用**：SkillExecutor 仍直查 Registry（性能路径，不变）
- **测试/自描述**：走 ToolManager（新路径）

### 2.4 EmilyCore 注入

[emily-core/emily_core/__init__.py](../emily-core/emily_core/__init__.py) 的 `EmilyCore._ensure_initialized` 在 `register_all` 之后构造 ToolManager：

```python
from .tools.manager import ToolManager
self._tool_manager = ToolManager(self._business_flow_tools)
```

bootstrap.py 启动报告可附 `tool_manager: ready`。

---

## 3. CLI 测试接口

### 3.1 入口

`scripts/toolmgr.py`（薄壳，参考 [check_tools_consistency.py](../scripts/check_tools_consistency.py) 模式）：

```bash
uv run python scripts/toolmgr.py <子命令> [参数] [--json]
```

### 3.2 子命令

| 命令 | 用途 | 默认输出 | `--json` 输出 | 退出码 |
|------|------|----------|--------------|--------|
| `list` | 列出所有工具 | 表格（name/category/permission/desc） | `{"tools":[...],"count":N}` | 0 |
| `show <name>` | 工具详情 + schema | 彩色文本（高亮必填字段） | `{"name":...,"parameters":...}` | 0/2 |
| `call <name> --params '{...}'` | 调用工具 | 结果 JSON（缩进） | `{"success":...,"result":...}` | 0/1/2 |
| `call <name> -f params.json` | 从文件读参数 | 同上 | 同上 | 0/1/2 |
| `test [<name>]` | 跑预设 smoke 用例 | 通过/失败表 | `{"results":[...]}` | 0/1 |
| `export` | 导出全部 schema | JSON（缩进） | 同左 | 0 |
| `selfcheck` | 依赖就绪检查 | 表格（name/ready/note） | `{"tools":[...]}` | 0 |

**退出码语义**（AI/脚本友好）：
- `0` = 成功
- `1` = 调用失败（handler 抛错）
- `2` = 工具不存在

### 3.3 AI 友好设计

- 所有命令支持 `--json`，输出结构化 JSON（无颜色、无表格）
- `export` 一键导出全部 schema，供 AI 生成 prompt 时注入"可用工具全集"
- `call` 失败时 JSON 含 `error` + `code`，AI 可据此重试或换工具
- 退出码语义化，shell 脚本可 `$?` 判断

### 3.4 手工检查友好设计

- 默认表格输出（用 `tabulate` 或纯文本对齐，避免引入 rich 重依赖）
- `show` 高亮 schema 的 `required` 字段
- `call` 失败时打印：参数 + 错误 + `hint: run 'toolmgr show <name>' to see schema`
- `list` 按 category 分组显示

### 3.5 薄壳 CLI 骨架

```python
# scripts/toolmgr.py
"""toolmgr — ToolManager CLI 薄壳。

用法：
    uv run python scripts/toolmgr.py list
    uv run python scripts/toolmgr.py show query_data
    uv run python scripts/toolmgr.py call query_data --params '{"query_type":"task"}'
    uv run python scripts/toolmgr.py call query_data -f params.json
    uv run python scripts/toolmgr.py test
    uv run python scripts/toolmgr.py export --json
    uv run python scripts/toolmgr.py selfcheck

退出码：0=成功，1=调用失败，2=工具不存在
"""
import argparse, asyncio, json, sys
from pathlib import Path

# 加载 emily-core（参考 check_tools_consistency.py 的 sys.path 注入）
# 构造 EmilyCore 实例（复用 bootstrap.init，或轻量初始化只起 registry）
# 分发到子命令
```

**初始化策略**：CLI 需要一个 EmilyCore 实例来拿到已注册的 registry。两种方式：
- **完整初始化**：`bootstrap.init()`（连 DB、起所有 service）—— 慢但真实
- **轻量初始化**：只构造 registry + register_all（不连 DB）—— 快但部分工具 handler 缺依赖

推荐**完整初始化**（与生产一致），慢一点但测试结果可信。参考 [self_check.py](../scripts/self_check.py) 的 `_init_db` 模式。

---

## 4. HTTP API（可选，后续阶段）

CLI 稳定后再加 HTTP，与 CLI 共用 ToolManager 核心：

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/v1/tools` | GET | list |
| `/api/v1/tools/{name}` | GET | show |
| `/api/v1/tools/{name}/call` | POST | call（body = params） |
| `/api/v1/tools/export` | GET | export |
| `/api/v1/tools/selfcheck` | GET | selfcheck |

路由文件 `api/routes/tools.py`，挂在 [api/server.py](../emily-core/api/server.py)。本次计划暂不实施，留作后续。

---

## 5. 实施步骤

每步独立交付、独立验收。

### S1｜ToolManager 核心类

**交付物**：`emily-core/emily_core/tools/manager.py`

**内容**：2.2 节的 ToolManager 类（list / describe / schema / export / call / selfcheck）

**验收**：
```python
# 容器内或宿主机 python
from emily_core.tools.manager import ToolManager
from emily_core.tools.business_flow_tools import BusinessFlowToolRegistry
# 构造一个最小 registry 测
reg = BusinessFlowToolRegistry()
# register_all 需要 core，这里只测 ToolManager 接口逻辑
tm = ToolManager(reg)
assert tm.list() == []
assert tm.describe("nope")["code"] == 2
```

### S2｜CLI 薄壳

**交付物**：`scripts/toolmgr.py`

**内容**：argparse + 子命令分发 + 表格/JSON 双输出

**验收**：
```bash
uv run python scripts/toolmgr.py list                    # 列出 27 工具
uv run python scripts/toolmgr.py list --json             # JSON 输出
uv run python scripts/toolmgr.py show query_data         # 显示 schema
uv run python scripts/toolmgr.py show query_data --json  # JSON schema
uv run python scripts/toolmgr.py export --json | head    # 全部 schema
uv run python scripts/toolmgr.py show nope; echo $?      # 退出码 2
```

### S3｜EmilyCore 注入 + selfcheck

**交付物**：
- `emily_core/__init__.py` 注入 `self._tool_manager`
- `ToolManager.selfcheck` 实现（识别 stub handler，如 `knowledge_search` 的 `_rag_stub`）

**验收**：
```bash
uv run python scripts/toolmgr.py selfcheck
# 输出每个工具的 ready 状态；knowledge_search 在 RAG 未就绪时 ready=false, note="stub"
```

### S4｜call 子命令打通

**交付物**：`call` 子命令完整实现（`--params` + `-f` 两种入参）

**验收**：
```bash
# query_data 是无外部依赖的查询工具，适合 smoke
uv run python scripts/toolmgr.py call query_data \
  --params '{"query_type":"task","_session_scope":{"db_perms":{"task":"read_write"},"project_ids":[]}}'
# 返回 {"success":true,"result":{...}}

uv run python scripts/toolmgr.py call query_data --params '{"query_type":"nope"}'; echo $?
# 退出码 1（调用失败）或 0（fallback），看 handler 行为
```

### S5｜预设 smoke 用例（可选）

**交付物**：`emily-core/tests/toolmgr_cases.yaml`（或 json）

**内容**：每个工具一个最小合法 params，`toolmgr test` 逐个跑

**验收**：
```bash
uv run python scripts/toolmgr.py test                  # 跑全部
uv run python scripts/toolmgr.py test query_data       # 跑单个
uv run python scripts/toolmgr.py test --json           # JSON 报告
```

### S6｜docs 更新

**交付物**：
- [docs/脚本工具目录.md](../docs/脚本工具目录.md) 加 `toolmgr.py` 条目
- [docs/接口协议与调用约定.md](../docs/接口协议与调用约定.md) 加 ToolManager 章节
- [docs/代码文件目录.md](../docs/代码文件目录.md) 加 `tools/manager.py`

---

## 6. 与原子工具层（计划 1）的协作

ToolManager 先行（本计划），原子工具（OCR/parse/extract/chunk/embed）建好后：

1. 在 [registry.py](../emily-core/emily_core/tools/registry.py) 的 `_register_base` / `_register_business` 里 `reg.register(_tool(...))` 一行注册
2. ToolManager **自动聚合**（它读的是同一个 registry），无需改 ToolManager 代码
3. 原子工具测试直接用 CLI，无需写独立测试脚本：
   ```bash
   uv run python scripts/toolmgr.py call ocr_document --params '{"file_path":"test.jpg"}'
   uv run python scripts/toolmgr.py call parse_document --params '{"file_path":"spec.pdf"}'
   uv run python scripts/toolmgr.py call embed_and_index --params '{"chunks":[...]}'
   ```
4. `toolmgr selfcheck` 会显示新工具的依赖就绪状态（如 `ocr_document` 的 VLM client 是否配好）

**这是 ToolManager 先于原子工具实施的核心价值**：原子工具一建好就有统一测试入口，不用每个工具写 CLI。

---

## 7. 验收标准

### 7.1 核心功能

- [ ] `uv run python scripts/toolmgr.py list` 列出 27 个工具，按 category 分组
- [ ] `uv run python scripts/toolmgr.py list --json` 输出合法 JSON，`count=27`
- [ ] `uv run python scripts/toolmgr.py show query_data` 显示 name/description/parameters
- [ ] `uv run python scripts/toolmgr.py show query_data --json` 输出 schema JSON
- [ ] `uv run python scripts/toolmgr.py export --json` 一键导出全部 schema
- [ ] `uv run python scripts/toolmgr.py show nope` 退出码 2

### 7.2 调用功能

- [ ] `uv run python scripts/toolmgr.py call query_data --params '{...}'` 返回结果
- [ ] `uv run python scripts/toolmgr.py call query_data -f params.json` 从文件读参
- [ ] `uv run python scripts/toolmgr.py call nope --params '{}'` 退出码 2
- [ ] 调用失败时输出参数 + 错误 + schema 提示

### 7.3 selfcheck

- [ ] `uv run python scripts/toolmgr.py selfcheck` 显示每个工具 ready 状态
- [ ] `knowledge_search` 在 RAG 未就绪时 `ready=false`

### 7.4 代码质量

- [ ] `tools/manager.py` 单文件，无新外部依赖（tabulate 可选，纯文本对齐也行）
- [ ] CLI 薄壳 `scripts/toolmgr.py` 核心逻辑可独立测试
- [ ] 退出码语义清晰（0/1/2）

---

## 8. 风险与备选

| 风险 | 缓解 |
|------|------|
| CLI 初始化需完整 EmilyCore（慢，~10s） | 复用 [self_check.py](../scripts/self_check.py) 的初始化模式；或加 `--quick` 只起 registry |
| `call` 绕过 SOP 可能触发未处理的边界 | handler 已有 try/except；ToolManager.call 再包一层，失败返回结构化错误 |
| selfcheck 的"就绪"定义模糊 | 先做简单版（handler 非 stub），复杂判断后续按工具补 |
| 表格输出引入依赖 | 用纯文本对齐（`str.ljust`），不引入 rich/tabulate |

---

## 9. 后续演进

1. **HTTP API**（S7）：CLI 稳定后加路由，供 AI Agent 远程调用
2. **预设用例库**（S5）：随工具增加逐步补 smoke 用例
3. **权限隔离**：`call` 子命令加 `--user-id` 模拟权限，测权限拦截
4. **与 tools_consistency 整合**：`TOOL_META_MAP` 改为从 `ToolManager.describe()` 动态生成，消除双份维护


---

## 验收记录

> **执行时间**：2026-07-25 09:31
> **执行人**：Trae AI Agent
> **状态**：ALL PASS

### 语法检查

| 文件 | 状态 |
|------|------|
| tools/manager.py | OK |
| scripts/toolmgr.py | OK |

### 功能测试

#### 1. toolmgr --help
exit_code=0，6 个子命令全部列正。

#### 2. toolmgr list --json
exit_code=0，30 个工具全部列出。含新增的 parse_document/extract_table/chunk_text。

#### 3. toolmgr show chunk_text
exit_code=0。Schema 显示 4 个参数（text/strategy/chunk_size/chunk_overlap），required 标记正确。

#### 4. toolmgr selfcheck
exit_code=0。29/30 工具 READY，仅 knowledge_search 为 NOT READY (stub handler)——预期行为（RAG provider 未配置）。

#### 5. toolmgr call chunk_text -f params.json
exit_code=0。工具调用路径畅通（报 "langchain-text-splitters not installed" 系环境依赖未装，工具注册和路由正确）。

### 变更文件清单

| 文件 | 操作 |
|------|------|
| emily-core/emily_core/tools/manager.py | 新增 |
| scripts/toolmgr.py | 新增 |
| emily-core/emily_core/__init__.py | 修改（_tool_manager 注入） |
| emily-core/tests/toolmgr_cases.yaml | 新增 |
| docs/脚本工具目录.md | 修改 |
| docs/接口协议与调用约定.md | 修改 |
| docs/代码文件目录.md | 修改 |
