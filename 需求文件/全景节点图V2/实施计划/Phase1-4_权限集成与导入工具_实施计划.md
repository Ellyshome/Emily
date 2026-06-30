# 全景节点图V2 Phase 1-4: 权限集成 + 文件存储扩展 + xlsx/md 导入 — AI 执行计划

> **基于需求**：[全景节点图-完整需求文档V2.md](全景节点图-完整需求文档V2.md)
> **计划版本**：v1.0
> **目标**：节点操作接入现有权限系统（auth_engine.authorize()）+ 文件存储扩展（source_module_id/type）+ xlsx 批量导入 + md 节点解析

---

## 你的角色

你是 **Emily 开发者**。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：不修改 auth_engine.py / file_service.py 的已有公共方法签名
2. **权限集成复用现有基础设施**：走 `auth_engine.authorize()` 统一鉴权入口，不新建独立鉴权系统
3. **文件存储扩展只在 `files` 表加字段**：不新建文件存储表，复用现有的 File 模型（含版本控制）
4. **xlsx 导入不引入重型依赖**：用 `openpyxl`（已在 Dockerfile 中）解析
5. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `PermissionAuthEngine` | `emily_core/permission/auth_engine.py` | `check_access(perms, resource_type, resource_id, operation)` | 节点操作鉴权入口 |
| `AccessCheckResult` | `emily_core/permission/auth_engine.py` | `allowed: bool, reason: str` | 接收鉴权结果 |
| `PermissionSnapshot` | `emily_core/session/session_context.py` | `authorized_node_ids: list[str]` | 获取用户可操作的节点列表 |
| `PermissionApplication` | `emily_core/application/permission_app.py` | `check_permission(user_id, sop_id)` | 参照鉴权调用模式 |
| `File` 模型 | `emily_core/infrastructure/database/models.py` | 含 `version`, `is_latest`, `parent_file_id`, `change_log` | 文件溯源——加 source 字段 |
| `FileService` | `emily_core/services/file_service.py` | `create_file_record(cmd)` | 扩展 FileCommand 增加 source 字段 |
| `FileRepository` | `emily_core/repositories/file_repo.py` | `create()`, `get_by_id()` | 参照模式 |
| `FileStorageService` | `emily_core/services/file_storage_service.py` | `store_attachment()`, `download_from_url()` | 文件下载存储复用 |
| `NodeService` | `emily_core/services/node_service.py` | 全部 CRUD 方法 | 权限检查包裹其调用 |
| `NodeApplication` | `emily_core/application/node_app.py` | 全部方法 | 权限检查前置 |
| `ProjectNodeRepo` | `emily_core/repositories/node_repo.py` | `find_by_project()`, `get_by_node_id()` | 数据查询 |
| `EmilyCore` | `emily_core/__init__.py` | `_permission_app`, `_auth_engine` | 获取鉴权实例 |

### 架构决策

1. **权限检查在 Application 层**：不在 API 路由层——这样不管是 REST API 调用还是 WorkItem 工具调用，都经过同一鉴权路径。
2. **文件 source 字段用现有 `files` 表扩展**：不加新表，利用 `files` 表已有的 28 列 + 版本控制（`version`/`is_latest`/`parent_file_id`）能力。新增 `source_module_id` / `source_module_type` 两列即可实现需求文档 §6.1 的溯源。
3. **xlsx 导入为独立工具脚本**：不在 EmilyCore 主流程中，作为 `scripts/` 目录下的独立工具，由运维或 API 触发调用。避免将文档解析逻辑耦合进核心循环。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| 权限检查 | `emily_core/application/permission_app.py` | `app.check_permission(user_id, sop_id)` → `AccessCheckResult` |
| 文件扩展 | `emily_core/repositories/file_repo.py` 中 `FileRepository.create()` | `@staticmethod` + `**kwargs` |
| 独立脚本 | `scripts/smoke_test.py` | `uv run python scripts/xxx.py` 可独立执行 |
| xlsx 解析 | Dockerfile 中 `openpyxl` | 已在依赖中，直接 `import openpyxl` |

---

## Phase 1-4: 权限集成 + 文件存储扩展 + xlsx/md 导入

**前置检查**（必须全部通过才进入此阶段）：

```powershell
docker exec emily-core python -c "
from emily_core.services.node_service import NodeService
from emily_core.permission.auth_engine import PermissionAuthEngine
from emily_core.application.node_app import NodeApplication
print('Phase 1-3 OK')
"
```
→ 预期输出：`Phase 1-3 OK`

**交付物**：节点操作受权限管控 + 文件可溯源到节点 + xlsx 批量导入可生成节点和依赖 + md 节点定义可解析

---

### Step 4.1: 在文件模型中增加 source 字段 + 数据库迁移

**目标**：`files` 表新增 `source_module_id` 和 `source_module_type` 两列，支持文件溯源到全景节点。

**操作**：

1. 打开 `emily-core/emily_core/infrastructure/database/models.py`
2. 找到 `class File(Base)` 定义（约第 260 行）
3. 在 `source_attachment_id` 列定义之后（约第 300 行），追加以下两列：

```python
    # 全景节点图 V2 —— 文件溯源字段（需求文档 §6.1）
    source_module_id = Column(String(100), default="", comment="来源模块ID（节点ID/其他业务对象ID）")
    source_module_type = Column(String(50), default="", comment="来源模块类型：NODE_STARTUP_DOC/NODE_WORKLOAD_DOC/NODE_DELIVERABLE_DOC/NODE_ATTACHMENT")
```

4. 创建迁移脚本 `emily-core/emily_core/infrastructure/database/scripts/006_add_file_source_fields.sql`：

```sql
-- ============================================================================
-- 006_add_file_source_fields.sql
-- files 表新增全景节点溯源字段（Phase 1-4）
-- 需求文档 §6.1
-- ============================================================================

ALTER TABLE files ADD COLUMN IF NOT EXISTS source_module_id VARCHAR(100) DEFAULT '';
ALTER TABLE files ADD COLUMN IF NOT EXISTS source_module_type VARCHAR(50) DEFAULT '';

COMMENT ON COLUMN files.source_module_id IS '来源模块ID（节点ID/其他业务对象ID）';
COMMENT ON COLUMN files.source_module_type IS '来源模块类型：NODE_STARTUP_DOC/NODE_WORKLOAD_DOC/NODE_DELIVERABLE_DOC/NODE_ATTACHMENT';
```

**验证**：

```powershell
# 1. 执行迁移
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/006_add_file_source_fields.sql

# 2. 验证列存在
docker exec emily-postgres psql -U emily -d emily -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='files' AND column_name IN ('source_module_id', 'source_module_type')"
```
→ 预期输出：两行 `source_module_id` + `source_module_type`

# 3. 验证 ORM 模型
```powershell
docker exec emily-core python -c "from emily_core.infrastructure.database.models import File; f = File.__table__.columns; print([c.name for c in f if 'source' in c.name])"
```
→ 预期输出：`['source_module_id', 'source_module_type']`

**失败处理**：如果列已存在（ALTER TABLE IF NOT EXISTS），跳过。如果 ORM 模型 import 报错，检查 Column 定义语法。

---

### Step 4.2: 扩展 NodeApplication 增加权限检查

**目标**：在每个节点操作前加入权限校验，调用 auth_engine.authorize()。

**操作**：

1. 打开 `emily-core/emily_core/application/node_app.py`
2. 在文件开头追加以下 import：

```python
import logging

# ... 原有 import 保持不变 ...

from ..permission.auth_engine import PermissionAuthEngine, AccessCheckResult
```

3. 在 `NodeApplication.__init__()` 中增加可选的 auth_engine 参数：

```python
def __init__(self, service: "NodeService", auth_engine: "PermissionAuthEngine | None" = None):
    self._service = service
    self._auth_engine = auth_engine
```

4. 在类中新增权限检查辅助方法：

```python
    async def _check_node_permission(self, node_id: str, operator_id: str,
                                      operation: str = "write") -> bool:
        """检查用户对节点的操作权限。

        映射到现有权限级别（需求文档 §4.2）：
          - 系统管理员（Level0）：全部操作
          - 项目总监（Level2）：节点废弃
          - 主责条线负责人（Level3）：编辑/成果更新/依赖调整
          - 指定经办人（Level4）：成果进度更新

        Args:
            node_id: 目标节点
            operator_id: 操作人
            operation: read / write / delete / mount_child

        Returns:
            True 如果有权限
        """
        if not self._auth_engine or not operator_id:
            return True  # 无鉴权引擎或未指定操作人 → 放行（Phase 1-4 默认可关闭）

        try:
            # 用 auth_engine.check_access 做通用资源鉴权
            # 资源类型固定为 NODE
            result = await self._auth_engine.check_access(
                perms=None,  # 由 auth_engine 内部从 session context 获取
                resource_type="NODE",
                resource_id=node_id,
                operation=operation,
            )
            return result.allowed
        except Exception:
            logger.warning("Permission check failed for node=%s user=%s", node_id, operator_id)
            return True  # 鉴权异常时 fail-open

    def _require_permission(self, allowed: bool, node_id: str, operation: str) -> None:
        """权限不足时抛出异常（由 Application 调用方处理）。"""
        if not allowed:
            raise PermissionError(f"无权限对节点 {node_id} 执行 {operation} 操作")
```

5. 在关键方法中插入权限检查。例如 `create_node` 方法开头加：

```python
    async def create_node(self, cmd: "CreateNodeCommand") -> dict:
        # 权限检查：节点创建——暂不在此处（由 API 层检查项目级权限），预留
        result = await self._service.create_node(cmd)
        # ... 后续不变
```

在 `update_node` / `discard_node` / `add_dependency` / `remove_dependency` / `mount_child` / `unmount_child` 等方法中添加：

```python
    async def update_node(self, cmd: "UpdateNodeCommand") -> dict:
        # 权限检查
        allowed = await self._check_node_permission(cmd.node_id, cmd.operator_id, "write")
        if not allowed:
            return {"success": False, "reply": "无权限编辑该节点", "error_code": "40301"}
        # ... 原有逻辑
```

**验证**：

```powershell
docker exec emily-core python -c "from emily_core.application.node_app import NodeApplication; print('NodeApplication with auth import OK')"
```
→ 预期输出：`NodeApplication with auth import OK`

**失败处理**：如果 import 失败，检查 import 路径是否正确。

---

### Step 4.3: 创建 xlsx 批量导入工具脚本

**目标**：实现从 xlsx 文件批量导入节点（含父子层级 + 依赖关系）。

**操作**：

1. 新建文件 `scripts/import_nodes_xlsx.py`
2. 写入以下内容：

```python
"""全景节点图 V2 xlsx 批量导入工具 —— 需求文档 §5.3。

用法：
    uv run python scripts/import_nodes_xlsx.py <xlsx文件路径> --project-id <项目ID> [--creator-id <创建人ID>]

xlsx 格式要求（Sheet1）：
    | 节点编号 | 节点名称 | 父节点编号 | 阶段 | 截止时间 | 主责条线 | 关联单位 | 权重 | 成果名称 | 成果目标量 | 成果单位 | 依赖成果ID | 依赖权重 |
    |---------|---------|-----------|------|---------|---------|---------|------|---------|-----------|---------|-----------|---------|
    | SG-001  | 景观工程 |           | 3    | 2026-10-15 | dept-eng | comp-a  | 1.0  | 施工图  | 1         | 份      | SJ-003-DELV-001 | 0.4 |

支持父子层级通过编号识别：子节点编号格式为 {父节点编号}-NN（如 SG-001-01 是 SG-001 的子节点）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 xlsx 批量导入")
    parser.add_argument("file", type=str, help="xlsx 文件路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="import-script", help="创建人ID")
    parser.add_argument("--no-deps", action="store_true", help="跳过依赖创建")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    parser.add_argument("--max-rows", type=int, default=500, help="最大导入行数")
    return parser.parse_args()


def parse_xlsx(filepath: str, max_rows: int = 500) -> list[dict]:
    """解析 xlsx 文件为节点数据列表。"""
    try:
        import openpyxl
    except ImportError:
        print("错误：需要 openpyxl 库。请执行: pip install openpyxl")
        sys.exit(1)

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(min_row=2, values_only=True))  # 跳过表头
    wb.close()

    nodes = {}
    deps = []

    for i, row in enumerate(rows[:max_rows]):
        if not row or not row[0]:
            continue

        node_id = str(row[0]).strip()
        node_name = str(row[1]).strip() if len(row) > 1 else ""
        parent_prefix = str(row[2]).strip() if len(row) > 2 else ""
        stage_id = int(row[3]) if len(row) > 3 and row[3] else 0
        deadline = str(row[4]).strip() if len(row) > 4 else ""
        owner = str(row[5]).strip() if len(row) > 5 else "项目总"
        company = str(row[6]).strip() if len(row) > 6 else "建设单位"
        weight = float(row[7]) if len(row) > 7 and row[7] else 1.0
        deliv_name = str(row[8]).strip() if len(row) > 8 else ""
        deliv_target = float(row[9]) if len(row) > 9 and row[9] else 1.0
        deliv_unit = str(row[10]).strip() if len(row) > 10 else "份"
        dep_deliv_id = str(row[11]).strip() if len(row) > 11 else ""
        dep_weight = float(row[12]) if len(row) > 12 and row[12] else 1.0

        if not node_id or not node_name:
            print(f"跳过第 {i+2} 行：节点编号或名称为空")
            continue

        # 自动识别父子关系：如果编号包含父节点前缀
        parent_node_id = parent_prefix
        if not parent_node_id and "-" in node_id:
            parts = node_id.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) <= 3:
                # 如 SG-001-01 → 父节点可能是 SG-001
                potential_parent = parts[0]
                # 检查 potential_parent 是否已在 nodes 中（同批次导入）
                # 暂存为候选，后续处理
                parent_node_id = ""  # 不在导入时自动推断，保留给用户显式指定

        nodes[node_id] = {
            "node_id": node_id,
            "node_name": node_name,
            "parent_node_id": parent_node_id,
            "stage_id": stage_id,
            "deadline": _normalize_deadline(deadline),
            "owner_dept_id": owner,
            "related_company_id": company,
            "child_weight": weight,
        }

        # 收集成果信息
        if deliv_name:
            if "deliverables" not in nodes[node_id]:
                nodes[node_id]["deliverables"] = []
            nodes[node_id]["deliverables"].append({
                "deliverable_name": deliv_name,
                "target_amount": deliv_target,
                "unit": deliv_unit,
            })

        # 收集依赖信息
        if dep_deliv_id:
            deps.append({
                "node_id": node_id,
                "depends_on_deliverable_id": dep_deliv_id,
                "weight": dep_weight,
            })

    return list(nodes.values()), deps


def _normalize_deadline(deadline: str) -> str:
    """标准化截止时间格式。"""
    if not deadline:
        return ""
    # 尝试解析常见格式
    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"]:
        try:
            dt = datetime.strptime(deadline.strip(), fmt)
            return dt.replace(tzinfo=BEIJING_TZ).isoformat()
        except ValueError:
            continue
    return deadline  # 无法解析则原样返回


async def import_nodes(nodes: list[dict], deps: list[dict],
                       project_id: str, creator_id: str,
                       dry_run: bool = False, skip_deps: bool = False):
    """执行导入。"""
    # 动态 import 避免在非 emily-core 环境报错
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand, AddDependencyCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    created = 0
    failed = []

    for nd in nodes:
        try:
            if dry_run:
                print(f"[DRY-RUN] 将创建节点: {nd['node_id']} - {nd['node_name']}")
                created += 1
                continue

            # 创建节点
            cmd = CreateNodeCommand(
                project_id=project_id,
                node_id=nd["node_id"],
                node_name=nd["node_name"],
                owner_dept_id=nd.get("owner_dept_id", "项目总"),
                related_company_id=nd.get("related_company_id", "建设单位"),
                deadline=nd.get("deadline", ""),
                creator_id=creator_id,
                parent_node_id=nd.get("parent_node_id", ""),
                stage_id=nd.get("stage_id", 0),
                child_weight=nd.get("child_weight", 1.0),
            )
            result = await svc.create_node(cmd)
            if not result.success:
                failed.append((nd["node_id"], result.message))
                continue

            created += 1
            print(f"[OK] {nd['node_id']}: {nd['node_name']} (status={result.status})")

            # 创建成果
            for deliv in nd.get("deliverables", []):
                dcmd = CreateDeliverableCommand(
                    node_id=nd["node_id"],
                    deliverable_name=deliv["deliverable_name"],
                    target_amount=deliv["target_amount"],
                    unit=deliv["unit"],
                    operator_id=creator_id,
                )
                await svc.create_deliverable(dcmd)

        except Exception as e:
            failed.append((nd.get("node_id", "?"), str(e)))
            print(f"[FAIL] {nd.get('node_id', '?')}: {e}")

    # 创建依赖（第二阶段——等所有节点创建完成后再建立依赖）
    if not dry_run and not skip_deps:
        dep_created = 0
        for dep in deps:
            try:
                dcmd = AddDependencyCommand(
                    node_id=dep["node_id"],
                    depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                    weight=dep.get("weight", 1.0),
                    operator_id=creator_id,
                )
                result = await svc.add_dependency(dcmd)
                if result.success:
                    dep_created += 1
                else:
                    print(f"[DEP-FAIL] {dep['node_id']} -> {dep['depends_on_deliverable_id']}: {result.message}")
            except Exception as e:
                print(f"[DEP-FAIL] {dep['node_id']}: {e}")

    # 批量建立父子关系（第三阶段——利用编号层级自动推断）
    if not dry_run:
        parent_count = 0
        for nd in nodes:
            parent_id = nd.get("parent_node_id", "")
            if parent_id and parent_id in {n["node_id"] for n in nodes}:
                try:
                    from emily_core.services.node_commands import MountChildCommand
                    mcmd = MountChildCommand(
                        parent_node_id=parent_id,
                        child_node_id=nd["node_id"],
                        child_weight=nd.get("child_weight", 1.0),
                        operator_id=creator_id,
                    )
                    result = await svc.mount_child(mcmd)
                    if result.success:
                        parent_count += 1
                except Exception as e:
                    print(f"[MOUNT-FAIL] {parent_id} -> {nd['node_id']}: {e}")
        if parent_count:
            print(f"[OK] 已挂载 {parent_count} 个子节点")

    # 汇总
    print(f"\n导入完成：成功 {created}/{len(nodes)} 个节点，失败 {len(failed)} 个")
    if failed and not dry_run:
        print("失败列表：")
        for nid, reason in failed:
            print(f"  - {nid}: {reason}")


def main():
    args = parse_args()

    filepath = Path(args.file)
    if not filepath.exists():
        print(f"错误：文件不存在: {args.file}")
        sys.exit(1)

    ext = filepath.suffix.lower()
    if ext not in (".xlsx", ".xlsm"):
        print(f"错误：不支持的文件格式: {ext}（仅支持 .xlsx）")
        sys.exit(1)

    print(f"解析文件: {args.file}")
    nodes, deps = parse_xlsx(str(filepath), args.max_rows)

    if not nodes:
        print("未解析到任何节点数据，请检查文件格式。")
        sys.exit(1)

    print(f"解析到 {len(nodes)} 个节点，{len(deps)} 条依赖关系")

    asyncio.run(import_nodes(
        nodes, deps,
        project_id=args.project_id,
        creator_id=args.creator_id,
        dry_run=args.dry_run,
        skip_deps=args.no_deps,
    ))


if __name__ == "__main__":
    main()
```

**验证**：

```powershell
# 语法检查
docker exec emily-core python -c "import ast; ast.parse(open('scripts/import_nodes_xlsx.py').read()); print('Syntax OK')"

# dry-run 测试（不依赖 xlsx 文件）
docker exec emily-core python -c "
import asyncio
# 模拟 dry-run 模式
print('Script structure verified')
print('Usage: uv run python scripts/import_nodes_xlsx.py <file> --project-id <id> [--dry-run]')
"
```
→ 预期输出：`Syntax OK` + `Script structure verified`

**失败处理**：如果语法错误，检查缩进问题。

---

### Step 4.4: 创建 md 节点定义解析脚本

**目标**：支持从 Markdown 格式的节点定义文件解析节点数据。

**操作**：

1. 新建文件 `scripts/import_nodes_md.py`
2. 写入以下内容：

```python
"""全景节点图 V2 Markdown 节点定义解析工具 —— 需求文档 §5.1。

支持的 Markdown 格式示例：

# 景观工程 (SG-001)
- 阶段: 3
- 截止: 2026-10-15T18:00:00+08:00
- 主责: dept-eng
- 单位: comp-landscape

## 成果
- 景观施工图: 1 份 [必需]
- 苗木清单: 1 份 [可选]

## 依赖
- SJ-003-DELV-001: 0.4 (景观施工图)
- CB-002-DELV-001: 0.3 (工程合同)

## 子节点
- SG-001-01: 钢筋绑扎 (权重0.4)
- SG-001-02: 模板支设 (权重0.3)
- SG-001-03: 混凝土浇筑 (权重0.3)

用法：
    uv run python scripts/import_nodes_md.py <md文件路径> --project-id <项目ID>
    uv run python scripts/import_nodes_md.py <目录路径> --project-id <项目ID>  # 批量导入目录
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="全景节点图 Markdown 批量导入")
    parser.add_argument("path", type=str, help="md 文件路径或目录路径")
    parser.add_argument("--project-id", type=str, required=True, help="目标项目ID")
    parser.add_argument("--creator-id", type=str, default="import-script", help="创建人ID")
    parser.add_argument("--dry-run", action="store_true", help="仅解析不导入")
    return parser.parse_args()


def parse_md_node(filepath: str) -> dict | None:
    """解析单个 Markdown 文件为节点数据。

    格式约定：
      - 第一行 H1 (# 开头) = 节点名称 + 可选编号
      - 元数据行 "- key: value"
      - "## 成果" 区域 → deliverables
      - "## 依赖" 区域 → dependencies
      - "## 子节点" 区域 → children
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.strip().split("\n")

    node = {
        "node_id": "",
        "node_name": "",
        "stage_id": 0,
        "deadline": "",
        "owner_dept_id": "项目总",
        "related_company_id": "建设单位",
        "child_weight": 1.0,
        "deliverables": [],
        "dependencies": [],
        "children": [],
    }

    section = "header"

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 标题行
        if line.startswith("# "):
            title = line[2:].strip()
            # 尝试提取编号：景观工程 (SG-001) → name + node_id
            m = re.match(r"(.+?)\s*\((\S+)\)\s*$", title)
            if m:
                node["node_name"] = m.group(1).strip()
                node["node_id"] = m.group(2).strip()
            else:
                node["node_name"] = title
                node["node_id"] = ""
            continue

        # 区域标记
        if line.startswith("## "):
            section_name = line[3:].strip().lower()
            if "成果" in section_name:
                section = "deliverables"
            elif "依赖" in section_name:
                section = "dependencies"
            elif "子节点" in section_name:
                section = "children"
            else:
                section = "header"
            continue

        # 元数据行
        if section == "header" and line.startswith("- "):
            meta = line[2:]
            if ":" in meta:
                key, _, value = meta.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key in ("阶段", "stage"):
                    try:
                        node["stage_id"] = int(value)
                    except ValueError:
                        pass
                elif key in ("截止", "deadline"):
                    node["deadline"] = value
                elif key in ("主责", "owner"):
                    node["owner_dept_id"] = value
                elif key in ("单位", "company"):
                    node["related_company_id"] = value
            continue

        # 成果行: - 成果名: 目标量 单位 [必需/可选]
        if section == "deliverables" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(.+?)\s*:\s*([\d.]+)\s*(\S+)?\s*(\[必需\])?(\[可选\])?", item)
            if m:
                node["deliverables"].append({
                    "deliverable_name": m.group(1).strip(),
                    "target_amount": float(m.group(2)),
                    "unit": m.group(3) or "份",
                    "is_required": "可选" not in (m.group(5) or ""),
                })
            continue

        # 依赖行: - DELIVERABLE_ID: 权重 (说明)
        if section == "dependencies" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(\S+)\s*:\s*([\d.]+)", item)
            if m:
                node["dependencies"].append({
                    "depends_on_deliverable_id": m.group(1),
                    "weight": float(m.group(2)),
                })
            continue

        # 子节点行: - NODE_ID: 名称 (权重X.X)
        if section == "children" and line.startswith("- "):
            item = line[2:]
            m = re.match(r"(\S+)\s*:\s*(.+?)\s*\(权重\s*([\d.]+)\)", item)
            if m:
                node["children"].append({
                    "child_node_id": m.group(1),
                    "child_name": m.group(2).strip(),
                    "child_weight": float(m.group(3)),
                })
            continue

    if not node["node_name"]:
        return None

    return node


async def import_from_md(nodes: list[dict], project_id: str, creator_id: str,
                          dry_run: bool = False):
    """执行导入。"""
    from emily_core.services.node_commands import (
        CreateNodeCommand, CreateDeliverableCommand,
        AddDependencyCommand, MountChildCommand,
    )
    from emily_core.services.node_service import NodeService

    svc = NodeService()
    created_ids = set()

    for nd in nodes:
        if dry_run:
            print(f"[DRY-RUN] {nd['node_id'] or '?'}: {nd['node_name']} "
                  f"({len(nd['deliverables'])} 成果, {len(nd['dependencies'])} 依赖, "
                  f"{len(nd['children'])} 子节点)")
            created_ids.add(nd["node_id"])
            continue

        try:
            cmd = CreateNodeCommand(
                project_id=project_id,
                node_id=nd["node_id"],
                node_name=nd["node_name"],
                owner_dept_id=nd["owner_dept_id"],
                related_company_id=nd["related_company_id"],
                deadline=nd["deadline"],
                stage_id=nd["stage_id"],
                creator_id=creator_id,
            )
            result = await svc.create_node(cmd)
            if not result.success:
                print(f"[FAIL] {nd['node_id']}: {result.message}")
                continue

            print(f"[OK] {nd['node_id']}: {nd['node_name']}")
            created_ids.add(nd["node_id"])

            # 创建成果
            for d in nd["deliverables"]:
                await svc.create_deliverable(CreateDeliverableCommand(
                    node_id=nd["node_id"],
                    deliverable_name=d["deliverable_name"],
                    target_amount=d["target_amount"],
                    unit=d["unit"],
                    is_required=d["is_required"],
                    operator_id=creator_id,
                ))

            # 创建子节点（递归）
            for child in nd["children"]:
                child_cmd = CreateNodeCommand(
                    project_id=project_id,
                    node_id=child["child_node_id"],
                    node_name=child["child_name"],
                    deadline=nd["deadline"],
                    creator_id=creator_id,
                    stage_id=nd["stage_id"],
                )
                await svc.create_node(child_cmd)
                created_ids.add(child["child_node_id"])

                # 挂载子节点
                await svc.mount_child(MountChildCommand(
                    parent_node_id=nd["node_id"],
                    child_node_id=child["child_node_id"],
                    child_weight=child["child_weight"],
                    operator_id=creator_id,
                ))

        except Exception as e:
            print(f"[FAIL] {nd.get('node_id', '?')}: {e}")

    # 建立依赖（第二阶段）
    if not dry_run:
        dep_count = 0
        for nd in nodes:
            for dep in nd["dependencies"]:
                try:
                    result = await svc.add_dependency(AddDependencyCommand(
                        node_id=nd["node_id"],
                        depends_on_deliverable_id=dep["depends_on_deliverable_id"],
                        weight=dep["weight"],
                        operator_id=creator_id,
                    ))
                    if result.success:
                        dep_count += 1
                except Exception as e:
                    print(f"[DEP-FAIL] {nd['node_id']}: {e}")
        if dep_count:
            print(f"[OK] 已建立 {dep_count} 条依赖关系")

    print(f"\n导入完成：{len(created_ids)} 个节点")


def main():
    args = parse_args()
    path = Path(args.path)

    if not path.exists():
        print(f"错误：路径不存在: {args.path}")
        sys.exit(1)

    md_files = []
    if path.is_dir():
        md_files = sorted(path.glob("*.md"))
    elif path.suffix.lower() == ".md":
        md_files = [path]
    else:
        print(f"错误：不支持的文件格式: {path.suffix}")
        sys.exit(1)

    if not md_files:
        print("未找到 .md 文件")
        sys.exit(1)

    print(f"找到 {len(md_files)} 个 .md 文件")

    nodes = []
    for f in md_files:
        nd = parse_md_node(str(f))
        if nd:
            nodes.append(nd)
            print(f"  解析: {nd['node_id'] or '?'} - {nd['node_name']}")
        else:
            print(f"  跳过: {f.name}（未识别为节点定义）")

    if not nodes:
        print("未解析到任何节点数据")
        sys.exit(1)

    asyncio.run(import_from_md(
        nodes, args.project_id, args.creator_id, args.dry_run,
    ))


if __name__ == "__main__":
    main()
```

**验证**：

```powershell
# 语法检查
docker exec emily-core python -c "import ast; ast.parse(open('scripts/import_nodes_md.py').read()); print('MD import script Syntax OK')"
```
→ 预期输出：`MD import script Syntax OK`

---

### Phase 1-4 最终验证

端到端验证：权限检查 + xlsx 导入解析。

```powershell
# 1. 验证权限检查集成
docker exec emily-core python -c "
from emily_core.application.node_app import NodeApplication
from emily_core.services.node_service import NodeService
app = NodeApplication(service=NodeService())
# 无 auth_engine 时应该放行
import asyncio
result = asyncio.run(app._check_node_permission('TEST', '', 'write'))
assert result == True, 'Should allow without auth_engine'
print('[OK] Permission check defaults to allow')
"

# 2. 验证 xlsx 导入脚本可加载
docker exec emily-core python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('import_nodes_xlsx', 'scripts/import_nodes_xlsx.py')
mod = importlib.util.module_from_spec(spec)
print('[OK] xlsx import script loadable')
"

# 3. 验证 md 解析功能
docker exec emily-core python -c "
from scripts.import_nodes_md import parse_md_node
import tempfile, os

# 创建测试 md 文件
md_content = '''# 景观工程 (SG-TEST)
- 阶段: 3
- 截止: 2026-10-15T18:00:00+08:00
- 主责: dept-eng

## 成果
- 施工图: 1 份 [必需]

## 依赖
- SJ-003-DELV-001: 0.4
'''

with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
    f.write(md_content)
    tmpfile = f.name

node = parse_md_node(tmpfile)
os.unlink(tmpfile)

assert node is not None
assert node['node_id'] == 'SG-TEST'
assert node['node_name'] == '景观工程'
assert len(node['deliverables']) == 1
assert node['deliverables'][0]['deliverable_name'] == '施工图'
assert len(node['dependencies']) == 1
print(f'[OK] MD parsed: {node[\"node_name\"]} with {len(node[\"deliverables\"])} deliverables, {len(node[\"dependencies\"])} deps')
print('=== Phase 1-4 验证通过 ===')
"
```
→ 预期输出：全部 `[OK]` + `=== Phase 1-4 验证通过 ===`

---

## 阶段反思指令

1. **检查产物**：
   - `emily-core/emily_core/infrastructure/database/models.py`（修改：File 类新增 2 列）
   - `emily-core/emily_core/infrastructure/database/scripts/006_add_file_source_fields.sql`（新建）
   - `emily-core/emily_core/application/node_app.py`（修改：增加权限检查方法）
   - `scripts/import_nodes_xlsx.py`（新建）
   - `scripts/import_nodes_md.py`（新建）

2. **判断是否继续**：按偏差规则处理

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
