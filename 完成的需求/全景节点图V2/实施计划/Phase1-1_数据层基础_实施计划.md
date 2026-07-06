# 全景节点图V2 Phase 1-1: 数据层基础 — AI 执行计划

> **基于需求**：[全景节点图-完整需求文档V2.md](全景节点图-完整需求文档V2.md)
> **计划版本**：v1.0
> **目标**：在 Emily 代码库中建立全景节点图的数据层基础设施——5 张 PostgreSQL 表 + ORM 模型 + Repository 层

---

## 你的角色

你是 **Emily 开发者**。严格按以下步骤执行，逐步骤验证，验证不通过不进入下一步。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：除非计划明确标注"修改方法签名"，否则只能在已有类中新增方法，不修改现有方法
2. **分层不可跳**：数据层代码遵守 `Repository → DB` 分层，Repository 不可调用 Service/Application
3. **所有时间戳用 ISO8601 字符串 + `_utc_now()`**：与现有 30+ 张表一致，不用原生 datetime 列
4. **所有主键用 `String` + `_new_uuid()`**：与现有模式一致
5. **sync Repo 模式**：Repository 方法为 `@staticmethod`，内部用 `with get_session() as session:` 管理会话
6. **每步验证**：每个步骤的验证命令必须通过，否则停止并报告
7. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件。风格不一致视为失败

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `Base` | `emily_core/infrastructure/database/models.py` | DeclarativeBase 基类 | 新 ORM 类继承它 |
| `_new_uuid()` | `emily_core/infrastructure/database/models.py` | 生成 UUID 字符串主键 | 直接调用作为 default |
| `_utc_now()` | `emily_core/infrastructure/database/models.py` | 返回 UTC ISO8601 字符串 | 直接调用作为 default |
| `_beijing_now()` | `emily_core/infrastructure/database/models.py` | 返回北京时区 ISO8601 | 展示字段可用 |
| `_new_id(prefix)` | `emily_core/infrastructure/database/models.py` | 生成 `PREFIX-YYYYMMDD-hex8` | 用于 event_id 等业务编号 |
| `get_session()` | `emily_core/infrastructure/database/session.py` | 上下文管理器，auto commit/rollback | Repository 每个方法内调用 |
| `get_session_raw()` | `emily_core/infrastructure/database/session.py` | 裸 session，需手动 close | 需要跨 Repository 事务时使用 |
| `BEIJING_TZ` | `emily_core/infrastructure/database/models.py` | `timezone(timedelta(hours=8))` | 业务日期字段生成 |
| `Project` | `emily_core/infrastructure/database/models.py` | `projects` 表，含 `id` `code` `name` `lifecycle_stage` | FK 目标：`project_nodes.project_id` |
| `File` | `emily_core/infrastructure/database/models.py` | `files` 表，含 `id` `file_no` `version` `is_latest` `parent_file_id` | FK 目标：`project_nodes.startup_doc_id` / `node_deliverables.file_id` |
| `CompanyInfo` | `emily_core/infrastructure/database/models.py` | `company_info` 表，含 `id` `company_name` | FK 目标：`project_nodes.owner_dept_id` / `related_company_id` |

### 架构决策

1. **VARCHAR(100) 作为业务主键**：`node_id`、`deliverable_id` 等业务编号使用 VARCHAR(100)，而非 UUID。这是需求文档明确要求的——节点编号如 `SG-JG-01-2026` 是人类可读的业务编码。数据库内部主键仍使用 UUID（`id` 列）。
2. **不用数据库级外键约束**：参照现有 `File.project_id` 和 `Event.project_id` 的模式——FK 是逻辑关系而非数据库约束，在需求文档中作为注释标注。这避免了跨表 DDL 耦合。备选方案是声明式 FK，但现有代码中并非所有表都使用，为保持一致性选择逻辑 FK。
3. **时间戳统一用 ISO8601 字符串**：与现有全部 30+ 张表一致，需求文档已采纳此规范。
4. **5 张表 vs 需求文档 4 表**：需求文档在 §3.5 完整定义了 `node_accessible_files` 表（DBA 审核建议的 M:N 中间表方案），加上原有的 4 表共 5 表。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| ORM 模型 | `emily-core/emily_core/infrastructure/database/models.py` 中 `PlanTaskTemplate` 类（第 ~460 行） | `Column(String, primary_key=True, default=_new_uuid)` + `__table_args__` Index + relationship lazy="selectin" |
| ORM 模型（JSON 字段） | `emily-core/emily_core/infrastructure/database/models.py` 中 `File` 类 | JSON 数组存为 String + 默认 `"[]"` / `"{}"` |
| Repository | `emily-core/emily_core/repositories/plan_task_repo.py` 中 `PlanTaskInstanceRepo` | `@staticmethod` + `with get_session() as session:` + 可选 `session=None` 参数 + `_impl(sess)` 模式 |
| Repository 编号生成 | `emily-core/emily_core/repositories/plan_task_repo.py` 中 `generate_instance_no()` | `date_part = datetime.now(BEIJING_TZ).strftime("%Y%m%d")` + 同类编号最大序号+1 |
| Repository 异常类 | `emily-core/emily_core/repositories/plan_task_repo.py` 中 `InvalidStateTransitionError` | `class XxxError(ValueError):` 纯 pass body |

---

## Phase 1-1: 数据层基础（ORM 模型 + 数据库迁移 + Repository 层）

**前置检查**：此阶段无依赖，可直接开始。

**交付物**：5 张 PostgreSQL 表可被 `docker exec emily-postgres psql` 查询到，ORM 模型可在 Python 中成功 import，Repository 类提供完整的 CRUD + 查询方法。

---

### Step 1.1: 在 models.py 末尾追加 5 个 ORM 模型类

**目标**：在 `emily_core/infrastructure/database/models.py` 中定义全景节点图的全部 5 张表。

**操作**：

1. 打开 `emily-core/emily_core/infrastructure/database/models.py`
2. 确认文件末尾是 `PermissionReviewTask` 类定义（约第 1136-1151 行）
3. 在文件末尾追加以下代码：

```python
# ============================================================================
# 全景节点图 V2 — 5 张表（Phase 1-1）
# 基于需求文档 §3.2–§3.6
# ============================================================================


class ProjectNode(Base):
    """节点主表 —— 需求文档 §3.2 project_nodes。

    三态状态机：CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED。
    支持父子层级（parent_node_id 自引用），嵌套深度上限 3 层。
    多项目隔离：project_id 为必填。
    """
    __tablename__ = "project_nodes"

    # ── 必填业务字段（6个）──
    project_id = Column(String(100), nullable=False, comment="项目归属ID（FK→projects.id）")
    node_id = Column(String(100), nullable=False, comment="节点编号（业务主键），例：SG-JG-01-2026")
    node_name = Column(String(500), nullable=False, comment="节点名称")
    owner_dept_id = Column(String(100), nullable=False, default="项目总", comment="主责条线（FK→company_info.id）")
    related_company_id = Column(String(100), nullable=False, default="建设单位", comment="关联单位（FK→company_info.id）")
    deadline = Column(String(50), nullable=False, comment="截止时间（ISO8601）")

    # ── 选填业务字段（6个）──
    land_parcel_id = Column(String(100), default="", comment="关联地块ID")
    remark = Column(Text, default="", comment="备注")
    parent_node_id = Column(String(100), default="", comment="父节点ID（FK→project_nodes.node_id）")
    stage_id = Column(Integer, default=0, comment="所属阶段ID（对齐 projects.lifecycle_stage）")
    child_weight = Column(String, default="1.0000", comment="作为子节点时在父节点中的权重（DECIMAL(5,4)，存为字符串避免精度问题）")
    startup_doc_id = Column(String(100), default="", comment="启动文档记录ID（FK→files.id）")

    # ── 系统字段（10个）──
    creator_id = Column(String(100), nullable=False, comment="录入人ID")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="录入时间（ISO8601）")
    approver_id = Column(String(100), default="", comment="批准人ID")
    approved_at = Column(String(50), default="", comment="批准时间（ISO8601）")
    completed_at = Column(String(50), default="", comment="完成时间（ISO8601）")
    is_discarded = Column(Boolean, default=False, comment="是否被废弃")
    progress = Column(String, default="0.00", comment="整体进度（百分比 0.00-100.00，存为字符串避免精度问题）")
    status = Column(String(20), default="CONDITIONS_NOT_MET", comment="当前状态：CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED")
    sort_order = Column(Integer, default=0, comment="排序序号")
    updated_at = Column(String(50), nullable=False, default=_utc_now, onupdate=_utc_now, comment="最后更新时间（ISO8601）")

    # ── 主键 ──
    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_nodes_project", "project_id"),
        Index("idx_nodes_status", "status"),
        Index("idx_nodes_stage", "stage_id"),
        Index("idx_nodes_owner", "owner_dept_id"),
        Index("idx_nodes_parent", "parent_node_id"),
    )


class NodeDependency(Base):
    """前置依赖表 —— 需求文档 §3.3 node_dependencies。

    核心机制：依赖不锁定节点，锁定具体成果文件。
    下游节点只需依赖文件就绪即可启动，无需等待上游节点整体完成。
    权重支持阻塞场景（权重 999 的人工依赖）。
    """
    __tablename__ = "node_dependencies"

    node_id = Column(String(100), nullable=False, comment="本节点（下游节点，FK→project_nodes.node_id）")
    depends_on_deliverable_id = Column(String(100), nullable=False, comment="依赖的成果ID（FK→node_deliverables.deliverable_id）")
    depends_on_node_id = Column(String(100), nullable=False, comment="成果所属上游节点ID（冗余字段，FK→project_nodes.node_id）")
    dependency_type = Column(String(20), nullable=False, default="DELIVERABLE", comment="依赖类型：DELIVERABLE / TIME")
    weight = Column(String, nullable=False, default="1.0000", comment="权重（0.0000-1.0000，存为字符串）")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="创建时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        UniqueConstraint("node_id", "depends_on_deliverable_id", name="uq_dep_node_deliverable"),
        Index("idx_ndep_node", "node_id"),
        Index("idx_ndep_deliverable", "depends_on_deliverable_id"),
    )


class NodeDeliverable(Base):
    """产出成果表 —— 需求文档 §3.4 node_deliverables。

    每个节点可定义多个产出成果，每个成果有目标量和当前量。
    成果完成度 = current_amount / target_amount。
    必需成果全部 100% 完成 → 节点状态自动流转至「已完成」。
    """
    __tablename__ = "node_deliverables"

    deliverable_id = Column(String(100), nullable=False, comment="成果编号（业务主键）")
    node_id = Column(String(100), nullable=False, comment="所属节点ID（FK→project_nodes.node_id）")
    deliverable_name = Column(String(500), nullable=False, comment="成果名称")
    target_amount = Column(String, nullable=False, comment="目标量（DECIMAL(12,2)，存为字符串）")
    current_amount = Column(String, nullable=False, default="0.00", comment="当前量（DECIMAL(12,2)，存为字符串）")
    unit = Column(String(50), nullable=False, comment="量纲（份/吨/平方米...）")
    is_required = Column(Boolean, nullable=False, default=True, comment="是否必需成果")
    file_id = Column(String(100), default="", comment="关联文件ID（FK→files.id）")
    completed_at = Column(String(50), default="", comment="完成时间（ISO8601）")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="创建时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_ndel_node", "node_id"),
    )


class NodeAccessibleFile(Base):
    """节点可见文件中间表 —— 需求文档 §3.5 node_accessible_files。

    M:N 关系，替代 JSON 数组方案，支持索引查询和权限审计。
    文件只对与之关联的节点的企业用户可见。
    """
    __tablename__ = "node_accessible_files"

    node_id = Column(String(100), nullable=False, comment="节点ID（FK→project_nodes.node_id）")
    file_id = Column(String(100), nullable=False, comment="文件ID（FK→files.id）")
    added_by = Column(String(100), nullable=False, comment="授权人ID")
    added_at = Column(String(50), nullable=False, default=_utc_now, comment="授权时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        UniqueConstraint("node_id", "file_id", name="uq_naf_node_file"),
        Index("idx_naf_node", "node_id"),
        Index("idx_naf_file", "file_id"),
    )


class NodeEvent(Base):
    """事件总线持久化表 —— 需求文档 §3.6 node_events。

    所有节点操作与变更记录，只增不改（immutable）。
    软删除也记录为 DELETE 事件，原始记录永久保留。
    """
    __tablename__ = "node_events"

    event_id = Column(String(100), nullable=False, comment="事件唯一ID")
    node_id = Column(String(100), nullable=False, comment="关联节点ID（FK→project_nodes.node_id）")
    event_type = Column(String(50), nullable=False, comment="事件类型枚举")
    old_value = Column(Text, default="", comment="变更前值（JSON快照）")
    new_value = Column(Text, default="", comment="变更后值（JSON快照）")
    operator_id = Column(String(100), default="", comment="操作人ID（系统自动触发则为空）")
    remark = Column(Text, default="", comment="操作说明/备注")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="事件发生时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_nev_node", "node_id"),
        Index("idx_nev_type", "event_type"),
        Index("idx_nev_created", "created_at"),
    )
```

**验证**：

```powershell
# 验证文件语法正确——Python 可以 import 新模型
docker exec emily-core python -c "from emily_core.infrastructure.database.models import ProjectNode, NodeDependency, NodeDeliverable, NodeAccessibleFile, NodeEvent; print('5 models imported OK')"
```
→ 预期输出：`5 models imported OK`

**失败处理**：如果 import 失败，检查报错信息——通常是缩进问题或 Column 参数拼写错误。修正后重新验证。

---

### Step 1.2: 创建数据库迁移脚本

**目标**：生成 PostgreSQL DDL 迁移脚本，用于在容器内创建 5 张新表。

**操作**：

1. 检查迁移脚本目录是否存在：`emily-core/emily_core/infrastructure/database/scripts/`
2. 新建文件 `emily-core/emily_core/infrastructure/database/scripts/005_create_panorama_tables.sql`
3. 写入以下内容：

```sql
-- ============================================================================
-- 005_create_panorama_tables.sql
-- 全景节点图 V2 — 5 张表 DDL（Phase 1-1）
-- 需求文档 §3.2–§3.6
-- 执行方式：docker exec -i emily-postgres psql -U emily -d emily < this_file.sql
-- ============================================================================

BEGIN;

-- 1. 节点主表
CREATE TABLE IF NOT EXISTS project_nodes (
    id              VARCHAR(100) PRIMARY KEY,
    project_id      VARCHAR(100) NOT NULL,
    node_id         VARCHAR(100) NOT NULL,
    node_name       VARCHAR(500) NOT NULL,
    owner_dept_id   VARCHAR(100) NOT NULL DEFAULT '项目总',
    related_company_id VARCHAR(100) NOT NULL DEFAULT '建设单位',
    deadline        VARCHAR(50) NOT NULL,
    land_parcel_id  VARCHAR(100) DEFAULT '',
    remark          TEXT DEFAULT '',
    parent_node_id  VARCHAR(100) DEFAULT '',
    stage_id        INTEGER DEFAULT 0,
    child_weight    VARCHAR(10) DEFAULT '1.0000',
    startup_doc_id  VARCHAR(100) DEFAULT '',
    creator_id      VARCHAR(100) NOT NULL,
    created_at      VARCHAR(50) NOT NULL,
    approver_id     VARCHAR(100) DEFAULT '',
    approved_at     VARCHAR(50) DEFAULT '',
    completed_at    VARCHAR(50) DEFAULT '',
    is_discarded    BOOLEAN DEFAULT FALSE,
    progress        VARCHAR(10) DEFAULT '0.00',
    status          VARCHAR(20) DEFAULT 'CONDITIONS_NOT_MET',
    sort_order      INTEGER DEFAULT 0,
    updated_at      VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_project ON project_nodes(project_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON project_nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_stage ON project_nodes(stage_id);
CREATE INDEX IF NOT EXISTS idx_nodes_owner ON project_nodes(owner_dept_id);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON project_nodes(parent_node_id);

-- 2. 前置依赖表
CREATE TABLE IF NOT EXISTS node_dependencies (
    id                          VARCHAR(100) PRIMARY KEY,
    node_id                     VARCHAR(100) NOT NULL,
    depends_on_deliverable_id   VARCHAR(100) NOT NULL,
    depends_on_node_id          VARCHAR(100) NOT NULL,
    dependency_type             VARCHAR(20) NOT NULL DEFAULT 'DELIVERABLE',
    weight                      VARCHAR(10) NOT NULL DEFAULT '1.0000',
    created_at                  VARCHAR(50) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dep_node_deliverable ON node_dependencies(node_id, depends_on_deliverable_id);
CREATE INDEX IF NOT EXISTS idx_ndep_node ON node_dependencies(node_id);
CREATE INDEX IF NOT EXISTS idx_ndep_deliverable ON node_dependencies(depends_on_deliverable_id);

-- 3. 产出成果表
CREATE TABLE IF NOT EXISTS node_deliverables (
    id              VARCHAR(100) PRIMARY KEY,
    deliverable_id  VARCHAR(100) NOT NULL,
    node_id         VARCHAR(100) NOT NULL,
    deliverable_name VARCHAR(500) NOT NULL,
    target_amount   VARCHAR(20) NOT NULL,
    current_amount  VARCHAR(20) NOT NULL DEFAULT '0.00',
    unit            VARCHAR(50) NOT NULL,
    is_required     BOOLEAN NOT NULL DEFAULT TRUE,
    file_id         VARCHAR(100) DEFAULT '',
    completed_at    VARCHAR(50) DEFAULT '',
    created_at      VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ndel_node ON node_deliverables(node_id);

-- 4. 节点可见文件中间表
CREATE TABLE IF NOT EXISTS node_accessible_files (
    id          VARCHAR(100) PRIMARY KEY,
    node_id     VARCHAR(100) NOT NULL,
    file_id     VARCHAR(100) NOT NULL,
    added_by    VARCHAR(100) NOT NULL,
    added_at    VARCHAR(50) NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_naf_node_file ON node_accessible_files(node_id, file_id);
CREATE INDEX IF NOT EXISTS idx_naf_node ON node_accessible_files(node_id);
CREATE INDEX IF NOT EXISTS idx_naf_file ON node_accessible_files(file_id);

-- 5. 事件总线表
CREATE TABLE IF NOT EXISTS node_events (
    id          VARCHAR(100) PRIMARY KEY,
    event_id    VARCHAR(100) NOT NULL,
    node_id     VARCHAR(100) NOT NULL,
    event_type  VARCHAR(50) NOT NULL,
    old_value   TEXT DEFAULT '',
    new_value   TEXT DEFAULT '',
    operator_id VARCHAR(100) DEFAULT '',
    remark      TEXT DEFAULT '',
    created_at  VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nev_node ON node_events(node_id);
CREATE INDEX IF NOT EXISTS idx_nev_type ON node_events(event_type);
CREATE INDEX IF NOT EXISTS idx_nev_created ON node_events(created_at);

COMMIT;
```

**验证**：

```powershell
# 先在容器内执行迁移
docker exec -i emily-postgres psql -U emily -d emily < emily-core/emily_core/infrastructure/database/scripts/005_create_panorama_tables.sql

# 验证 5 张表都存在
docker exec emily-postgres psql -U emily -d emily -c "\dt project_nodes node_dependencies node_deliverables node_accessible_files node_events"
```
→ 预期输出：显示 5 行表名，每行包含 schema `public` 和表名

**失败处理**：如果 SQL 执行报错，检查是否有同名表已存在（`Base.metadata.create_all` 已自动建表）。如果表已存在且结构正确，跳过此步骤并记录。如果结构不匹配，需 DROP 后重建。

---

### Step 1.3: 确认 ORM 自动建表可用（备选路径）

**目标**：如果 Step 1.2 的 SQL 脚本已成功，则 `Base.metadata.create_all` 也会在下次 Emily 重启时自动建表。验证两条路径均可工作。

**操作**：

确认 `init_db()` 函数中的 `Base.metadata.create_all(bind=_engine)` 已包含新表。由于模型类直接继承 `Base`，无需额外操作。

**验证**：

```powershell
# 重启 emily-core 容器，查看日志确认建表成功
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 30 emily-core 2>&1 | grep -i "create\|table\|error"
```
→ 预期输出：无 ERROR 级别日志（CREATE TABLE IF NOT EXISTS 不会在已存在时报错）

**失败处理**：如果出现错误，通常是容器启动配置问题——检查 `docker-compose-napcat.yml` 中 emily-core 的环境变量是否包含正确的数据库连接。

---

### Step 1.4: 创建 NodeRepository（5 合 1 Repository 文件）

**目标**：为 5 张表创建 Repository 层，提供完整的 CRUD + 查询方法。

**操作**：

1. 新建文件 `emily-core/emily_core/repositories/node_repo.py`
2. 写入以下内容：

```python
"""全景节点图 V2 Repository 层 —— 5 张表的 CRUD 操作。

包含：ProjectNodeRepo / NodeDependencyRepo / NodeDeliverableRepo /
      NodeAccessibleFileRepo / NodeEventRepo

基于需求文档 §3.2–§3.6。参照模式：plan_task_repo.py。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from ..infrastructure.database.models import (
    ProjectNode,
    NodeDependency,
    NodeDeliverable,
    NodeAccessibleFile,
    NodeEvent,
)
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.node_repo")

BEIJING_TZ = timezone(timedelta(hours=8))


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _to_decimal_str(value: float, precision: int = 4) -> str:
    """将 float 转为固定精度的字符串（用于 DECIMAL 列存储）。"""
    if precision == 2:
        return f"{value:.2f}"
    return f"{value:.4f}"


def _parse_decimal(value: str) -> float:
    """将 DECIMAL 字符串解析为 float。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# ProjectNodeRepo
# ══════════════════════════════════════════════════════════════════════════════

class ProjectNodeRepo:
    """节点主表 Repository。"""

    @staticmethod
    def create(**kwargs) -> ProjectNode:
        """创建节点。

        必填参数：project_id, node_id, node_name, creator_id, deadline
        可选参数：owner_dept_id, related_company_id, parent_node_id, stage_id,
                  child_weight, remark, land_parcel_id, startup_doc_id, sort_order
        """
        with get_session() as session:
            node = ProjectNode(**kwargs)
            session.add(node)
            session.flush()
            logger.info("ProjectNode created: %s (project=%s)", node.node_id, node.project_id)
            return node

    @staticmethod
    def get_by_id(node_uuid: str) -> ProjectNode | None:
        """按数据库主键 ID 查询。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(ProjectNode.id == node_uuid, ProjectNode.is_discarded == False)
                .first()
            )

    @staticmethod
    def get_by_node_id(node_id: str, project_id: str | None = None) -> ProjectNode | None:
        """按业务编号 node_id 查询，可选项目过滤。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            return q.first()

    @staticmethod
    def find_by_project(project_id: str, status: str | None = None, limit: int = 200) -> list[ProjectNode]:
        """查询项目下所有节点（可选按状态过滤）。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.project_id == project_id, ProjectNode.is_discarded == False)
            )
            if status:
                q = q.filter(ProjectNode.status == status)
            return q.order_by(ProjectNode.sort_order, ProjectNode.created_at).limit(limit).all()

    @staticmethod
    def find_by_parent(parent_node_id: str) -> list[ProjectNode]:
        """查询某父节点的所有子节点。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.parent_node_id == parent_node_id,
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.sort_order)
                .all()
            )

    @staticmethod
    def find_by_stage(project_id: str, stage_id: int) -> list[ProjectNode]:
        """查询某阶段的所有根节点（parent_node_id 为空）。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.stage_id == stage_id,
                    ProjectNode.parent_node_id == "",
                    ProjectNode.is_discarded == False,
                )
                .order_by(ProjectNode.sort_order)
                .all()
            )

    @staticmethod
    def find_by_owner(owner_dept_id: str, project_id: str | None = None) -> list[ProjectNode]:
        """按主责条线查询节点。"""
        with get_session() as session:
            q = (
                session.query(ProjectNode)
                .filter(ProjectNode.owner_dept_id == owner_dept_id, ProjectNode.is_discarded == False)
            )
            if project_id:
                q = q.filter(ProjectNode.project_id == project_id)
            return q.order_by(ProjectNode.created_at.desc()).limit(200).all()

    @staticmethod
    def update_fields(node_id: str, **kwargs) -> ProjectNode | None:
        """更新节点字段。自动设置 updated_at。

        可更新字段：node_name, deadline, owner_dept_id, related_company_id,
                    remark, stage_id, sort_order, land_parcel_id, startup_doc_id
        """
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            for key, value in kwargs.items():
                if hasattr(node, key):
                    setattr(node, key, value)
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode updated: %s fields=%s", node_id, list(kwargs.keys()))
            return node

    @staticmethod
    def update_status(node_id: str, new_status: str) -> ProjectNode | None:
        """更新节点状态（状态机专用）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.status = new_status
            node.updated_at = datetime.now(timezone.utc).isoformat()
            if new_status == "COMPLETED":
                node.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode status: %s -> %s", node_id, new_status)
            return node

    @staticmethod
    def update_progress(node_id: str, progress: float) -> ProjectNode | None:
        """更新节点进度（百分比 0.00-100.00）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.progress = _to_decimal_str(progress, precision=2)
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            return node

    @staticmethod
    def discard(node_id: str) -> ProjectNode | None:
        """废弃节点（软删除，不物理删除）。"""
        with get_session() as session:
            node = (
                session.query(ProjectNode)
                .filter(ProjectNode.node_id == node_id, ProjectNode.is_discarded == False)
                .first()
            )
            if node is None:
                return None
            node.is_discarded = True
            node.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("ProjectNode discarded: %s", node_id)
            return node

    @staticmethod
    def count_children(parent_node_id: str) -> int:
        """统计子节点数量（用于上限检查）。"""
        with get_session() as session:
            return (
                session.query(ProjectNode)
                .filter(
                    ProjectNode.parent_node_id == parent_node_id,
                    ProjectNode.is_discarded == False,
                )
                .count()
            )

    @staticmethod
    def get_ancestor_chain(node_id: str, max_depth: int = 3) -> list[ProjectNode]:
        """向上追溯祖先链（用于递归进度重算）。最多 3 层。"""
        ancestors = []
        current_id = node_id
        for _ in range(max_depth):
            with get_session() as session:
                node = (
                    session.query(ProjectNode)
                    .filter(
                        ProjectNode.node_id == current_id,
                        ProjectNode.is_discarded == False,
                    )
                    .first()
                )
            if node is None or not node.parent_node_id:
                break
            parent = ProjectNodeRepo.get_by_node_id(node.parent_node_id)
            if parent is None:
                break
            ancestors.append(parent)
            current_id = parent.node_id
        return ancestors


# ══════════════════════════════════════════════════════════════════════════════
# NodeDependencyRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeDependencyRepo:
    """前置依赖表 Repository。"""

    @staticmethod
    def create(**kwargs) -> NodeDependency:
        """创建依赖记录。

        必填：node_id, depends_on_deliverable_id, depends_on_node_id
        可选：dependency_type (默认 DELIVERABLE), weight (默认 1.0000)
        """
        with get_session() as session:
            dep = NodeDependency(**kwargs)
            session.add(dep)
            session.flush()
            logger.info(
                "NodeDependency created: %s depends on %s",
                dep.node_id, dep.depends_on_deliverable_id,
            )
            return dep

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeDependency]:
        """查询节点的所有前置依赖。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(NodeDependency.node_id == node_id)
                .all()
            )

    @staticmethod
    def find_downstream(depends_on_node_id: str) -> list[NodeDependency]:
        """反向查询：哪些节点依赖了某上游节点的成果。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(NodeDependency.depends_on_node_id == depends_on_node_id)
                .all()
            )

    @staticmethod
    def get_by_id(dep_id: str) -> NodeDependency | None:
        """按主键查询。"""
        with get_session() as session:
            return session.query(NodeDependency).filter(NodeDependency.id == dep_id).first()

    @staticmethod
    def delete(dep_id: str) -> bool:
        """删除依赖记录（物理删除，因为依赖是精确关系不是业务数据）。"""
        with get_session() as session:
            dep = session.query(NodeDependency).filter(NodeDependency.id == dep_id).first()
            if dep is None:
                return False
            session.delete(dep)
            session.commit()
            logger.info("NodeDependency deleted: %s", dep_id)
            return True

    @staticmethod
    def exists(node_id: str, depends_on_deliverable_id: str) -> bool:
        """检查依赖是否已存在（唯一约束检查）。"""
        with get_session() as session:
            return (
                session.query(NodeDependency)
                .filter(
                    NodeDependency.node_id == node_id,
                    NodeDependency.depends_on_deliverable_id == depends_on_deliverable_id,
                )
                .first()
                is not None
            )


# ══════════════════════════════════════════════════════════════════════════════
# NodeDeliverableRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeDeliverableRepo:
    """产出成果表 Repository。"""

    @staticmethod
    def generate_deliverable_id(node_id: str, seq: int) -> str:
        """生成成果编号：{node_id}-DELV-{seq:03d}。"""
        return f"{node_id}-DELV-{seq:03d}"

    @staticmethod
    def get_next_seq(node_id: str) -> int:
        """获取某节点下一个成果序号。"""
        with get_session() as session:
            existing = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.node_id == node_id)
                .all()
            )
            return len(existing) + 1

    @staticmethod
    def create(**kwargs) -> NodeDeliverable:
        """创建成果记录。

        必填：deliverable_id, node_id, deliverable_name, target_amount, unit
        可选：current_amount (默认 0.00), is_required (默认 True), file_id
        """
        with get_session() as session:
            deliv = NodeDeliverable(**kwargs)
            session.add(deliv)
            session.flush()
            logger.info("NodeDeliverable created: %s for node %s", deliv.deliverable_id, deliv.node_id)
            return deliv

    @staticmethod
    def get_by_deliverable_id(deliverable_id: str) -> NodeDeliverable | None:
        """按业务编号查询。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeDeliverable]:
        """查询节点的所有成果。"""
        with get_session() as session:
            return (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.node_id == node_id)
                .all()
            )

    @staticmethod
    def update_progress(deliverable_id: str, current_amount: str, file_id: str = "") -> NodeDeliverable | None:
        """更新成果当前量和关联文件。"""
        with get_session() as session:
            deliv = (
                session.query(NodeDeliverable)
                .filter(NodeDeliverable.deliverable_id == deliverable_id)
                .first()
            )
            if deliv is None:
                return None
            deliv.current_amount = current_amount
            if file_id:
                deliv.file_id = file_id
            # 检查是否达成目标量
            if _parse_decimal(current_amount) >= _parse_decimal(deliv.target_amount):
                deliv.completed_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            logger.info("NodeDeliverable progress: %s -> %s", deliverable_id, current_amount)
            return deliv

    @staticmethod
    def get_completion_ratio(node_id: str) -> float:
        """计算节点必需成果的完成度比例（0.0-1.0）。"""
        with get_session() as session:
            deliverables = (
                session.query(NodeDeliverable)
                .filter(
                    NodeDeliverable.node_id == node_id,
                    NodeDeliverable.is_required == True,
                )
                .all()
            )
            if not deliverables:
                return 1.0  # 无必需成果 = 视为已完成

            total_ratio = 0.0
            for d in deliverables:
                target = max(_parse_decimal(d.target_amount), 0.001)
                current = min(_parse_decimal(d.current_amount), target)
                total_ratio += current / target

            return total_ratio / len(deliverables)


# ══════════════════════════════════════════════════════════════════════════════
# NodeAccessibleFileRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeAccessibleFileRepo:
    """节点可见文件中间表 Repository。"""

    @staticmethod
    def create(**kwargs) -> NodeAccessibleFile:
        """添加节点可见文件。

        必填：node_id, file_id, added_by
        """
        with get_session() as session:
            naf = NodeAccessibleFile(**kwargs)
            session.add(naf)
            session.flush()
            logger.info("NodeAccessibleFile added: node=%s file=%s", naf.node_id, naf.file_id)
            return naf

    @staticmethod
    def find_by_node(node_id: str) -> list[NodeAccessibleFile]:
        """查询节点可访问的所有文件。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(NodeAccessibleFile.node_id == node_id)
                .all()
            )

    @staticmethod
    def find_by_file(file_id: str) -> list[NodeAccessibleFile]:
        """反向查询：某文件可被哪些节点访问。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(NodeAccessibleFile.file_id == file_id)
                .all()
            )

    @staticmethod
    def remove(node_id: str, file_id: str) -> bool:
        """移除节点可见文件。"""
        with get_session() as session:
            naf = (
                session.query(NodeAccessibleFile)
                .filter(
                    NodeAccessibleFile.node_id == node_id,
                    NodeAccessibleFile.file_id == file_id,
                )
                .first()
            )
            if naf is None:
                return False
            session.delete(naf)
            session.commit()
            logger.info("NodeAccessibleFile removed: node=%s file=%s", node_id, file_id)
            return True

    @staticmethod
    def batch_add(node_id: str, file_ids: list[str], added_by: str) -> int:
        """批量添加节点可见文件（同一事务）。"""
        count = 0
        with get_session() as session:
            for file_id in file_ids:
                # 跳过已存在的
                existing = (
                    session.query(NodeAccessibleFile)
                    .filter(
                        NodeAccessibleFile.node_id == node_id,
                        NodeAccessibleFile.file_id == file_id,
                    )
                    .first()
                )
                if existing:
                    continue
                naf = NodeAccessibleFile(
                    node_id=node_id,
                    file_id=file_id,
                    added_by=added_by,
                )
                session.add(naf)
                count += 1
            session.commit()
            logger.info("NodeAccessibleFile batch_add: node=%s count=%d", node_id, count)
            return count

    @staticmethod
    def exists(node_id: str, file_id: str) -> bool:
        """检查关联是否已存在。"""
        with get_session() as session:
            return (
                session.query(NodeAccessibleFile)
                .filter(
                    NodeAccessibleFile.node_id == node_id,
                    NodeAccessibleFile.file_id == file_id,
                )
                .first()
                is not None
            )


# ══════════════════════════════════════════════════════════════════════════════
# NodeEventRepo
# ══════════════════════════════════════════════════════════════════════════════

class NodeEventRepo:
    """事件总线持久化 Repository —— 只增不改（immutable）。"""

    @staticmethod
    def create(**kwargs) -> NodeEvent:
        """记录事件。

        必填：event_id, node_id, event_type
        可选：old_value, new_value, operator_id, remark
        """
        with get_session() as session:
            event = NodeEvent(**kwargs)
            session.add(event)
            session.flush()
            logger.info("NodeEvent created: %s type=%s node=%s", event.event_id, event.event_type, event.node_id)
            return event

    @staticmethod
    def find_by_node(node_id: str, event_type: str | None = None, limit: int = 100) -> list[NodeEvent]:
        """查询节点事件日志（按时间倒序）。"""
        with get_session() as session:
            q = session.query(NodeEvent).filter(NodeEvent.node_id == node_id)
            if event_type:
                q = q.filter(NodeEvent.event_type == event_type)
            return q.order_by(NodeEvent.created_at.desc()).limit(limit).all()

    @staticmethod
    def find_by_project(project_id: str, limit: int = 200) -> list[NodeEvent]:
        """查询项目下所有节点事件（JOIN project_nodes）。"""
        with get_session() as session:
            return (
                session.query(NodeEvent)
                .join(ProjectNode, NodeEvent.node_id == ProjectNode.node_id)
                .filter(ProjectNode.project_id == project_id)
                .order_by(NodeEvent.created_at.desc())
                .limit(limit)
                .all()
            )

    @staticmethod
    def find_by_operator(operator_id: str, limit: int = 100) -> list[NodeEvent]:
        """查询操作人的所有事件。"""
        with get_session() as session:
            return (
                session.query(NodeEvent)
                .filter(NodeEvent.operator_id == operator_id)
                .order_by(NodeEvent.created_at.desc())
                .limit(limit)
                .all()
            )
```

**验证**：

```powershell
# 验证 Python 可以 import 所有 Repo 类
docker exec emily-core python -c "from emily_core.repositories.node_repo import ProjectNodeRepo, NodeDependencyRepo, NodeDeliverableRepo, NodeAccessibleFileRepo, NodeEventRepo; print('5 repos imported OK')"
```
→ 预期输出：`5 repos imported OK`

```powershell
# 验证 Repo 方法可以调用（创建测试节点）
docker exec emily-core python -c "
from emily_core.repositories.node_repo import ProjectNodeRepo
# 测试创建
node = ProjectNodeRepo.create(
    project_id='test-proj-001',
    node_id='TEST-NODE-001',
    node_name='测试节点',
    creator_id='test-user',
    deadline='2026-12-31T18:00:00+08:00'
)
print(f'Created: {node.node_id}, status={node.status}, progress={node.progress}')

# 测试查询
found = ProjectNodeRepo.get_by_node_id('TEST-NODE-001')
print(f'Found: {found.node_name}, id={found.id}')

# 清理测试数据
from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import ProjectNode
with get_session() as s:
    s.query(ProjectNode).filter(ProjectNode.node_id == 'TEST-NODE-001').delete()
    s.commit()
print('Test data cleaned')
"
```
→ 预期输出：三行包含 `Created:` / `Found:` / `Test data cleaned`

**失败处理**：如果报 `ImportError`，检查文件路径和类名。如果报 `OperationalError`（表不存在），先执行 Step 1.2 的迁移脚本。如果创建失败，检查字段默认值是否正确。

---

### Phase 1-1 最终验证

完成本阶段所有步骤后，运行端到端验证：

```powershell
# 端到端验证：ORM → DB → Repo 全链路
docker exec emily-core python -c "
from emily_core.infrastructure.database.models import (
    ProjectNode, NodeDependency, NodeDeliverable, NodeAccessibleFile, NodeEvent,
)
from emily_core.repositories.node_repo import (
    ProjectNodeRepo, NodeDependencyRepo, NodeDeliverableRepo,
    NodeAccessibleFileRepo, NodeEventRepo,
)
from emily_core.infrastructure.database.session import get_session

# 1. 创建节点
node = ProjectNodeRepo.create(
    project_id='e2e-test',
    node_id='E2E-001',
    node_name='E2E测试节点',
    owner_dept_id='dept-eng',
    related_company_id='comp-test',
    deadline='2026-12-31T18:00:00+08:00',
    creator_id='user-test',
    stage_id=1,
)
assert node.status == 'CONDITIONS_NOT_MET', f'Expected CONDITIONS_NOT_MET, got {node.status}'
assert node.progress == '0.00', f'Expected 0.00, got {node.progress}'
print(f'[OK] Step 1: Node created, status={node.status}')

# 2. 创建成果
seq = NodeDeliverableRepo.get_next_seq('E2E-001')
did = NodeDeliverableRepo.generate_deliverable_id('E2E-001', seq)
deliv = NodeDeliverableRepo.create(
    deliverable_id=did,
    node_id='E2E-001',
    deliverable_name='测试成果',
    target_amount='100.00',
    unit='份',
    is_required=True,
)
assert deliv.current_amount == '0.00', f'Expected 0.00, got {deliv.current_amount}'
print(f'[OK] Step 2: Deliverable created, id={did}')

# 3. 创建依赖
dep = NodeDependencyRepo.create(
    node_id='E2E-001',
    depends_on_deliverable_id=did,
    depends_on_node_id='E2E-001',
    weight='0.5000',
)
assert dep.dependency_type == 'DELIVERABLE'
print(f'[OK] Step 3: Dependency created')

# 4. 创建文件关联
naf = NodeAccessibleFileRepo.create(
    node_id='E2E-001',
    file_id='file-test-001',
    added_by='user-test',
)
assert NodeAccessibleFileRepo.exists('E2E-001', 'file-test-001')
print(f'[OK] Step 4: File access granted')

# 5. 记录事件
from emily_core.infrastructure.database.models import _new_id
evt = NodeEventRepo.create(
    event_id=_new_id('EVT'),
    node_id='E2E-001',
    event_type='node_created',
    new_value='{\"node_name\": \"E2E测试节点\"}',
    operator_id='user-test',
)
assert evt.event_type == 'node_created'
print(f'[OK] Step 5: Event recorded')

# 6. 验证查询链
found_node = ProjectNodeRepo.get_by_node_id('E2E-001')
found_deps = NodeDependencyRepo.find_by_node('E2E-001')
found_delivs = NodeDeliverableRepo.find_by_node('E2E-001')
found_files = NodeAccessibleFileRepo.find_by_node('E2E-001')
found_events = NodeEventRepo.find_by_node('E2E-001')
assert found_node is not None
assert len(found_deps) == 1
assert len(found_delivs) == 1
assert len(found_files) == 1
assert len(found_events) == 1
print(f'[OK] Step 6: All queries returned correct counts')

# 7. 清理
with get_session() as s:
    s.query(NodeEvent).filter(NodeEvent.node_id == 'E2E-001').delete()
    s.query(NodeAccessibleFile).filter(NodeAccessibleFile.node_id == 'E2E-001').delete()
    s.query(NodeDependency).filter(NodeDependency.node_id == 'E2E-001').delete()
    s.query(NodeDeliverable).filter(NodeDeliverable.node_id == 'E2E-001').delete()
    s.query(ProjectNode).filter(ProjectNode.node_id == 'E2E-001').delete()
    s.commit()
print('[OK] Step 7: Cleanup done')

print('=== Phase 1-1 全部验证通过 ===')
"
```
→ 预期输出：8 行 `[OK]` + 最终 `=== Phase 1-1 全部验证通过 ===`

全部通过后进入 Phase 1-2。

---

## 阶段反思指令

完成本阶段后，执行以下反思：

1. **检查产物**：列出本阶段所有新建/修改的文件路径
   - `emily-core/emily_core/infrastructure/database/models.py`（修改：追加 5 个类）
   - `emily-core/emily_core/infrastructure/database/scripts/005_create_panorama_tables.sql`（新建）
   - `emily-core/emily_core/repositories/node_repo.py`（新建）

2. **检查偏差**：是否有步骤与计划不符？记录差异

3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应 Phase，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
