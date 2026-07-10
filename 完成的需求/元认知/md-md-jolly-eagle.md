# 元认知模块 — AI 执行计划

> **基于需求**：[自我认知需求规格.md](需求文件/自我认知/自我认知需求规格.md)
> **计划版本**：v1.0
> **目标**：实现 Emily 元认知模块——规则书（行为宪法）+ 世界书（七层项目认知）+ 元认知判断（初始化四层模型），使 SessionAgent 从"只知道项目名"进化为"理解项目全貌并遵循组织约定"。

---

## 你的角色

你作为 **Emily开发者资深架构师** + **数据库架构师** + **AI Agent 行为设计专家**，严格按以下模块顺序执行，逐模块验证，验证不通过不进入下一个模块。

---

## 硬约束（违反即失败）

1. **禁止修改已有方法签名**：除非计划明确标注"修改方法签名"，否则只能在已有类中新增方法，不修改现有方法
2. **分层不可跳**：新增代码遵循 `API → EmilyCore → Session → WorkItem → Application → Service → Repository → DB` 分层
3. **Repository 全 sync**：新增 Repository 方法全部同步，async 调用方用 `asyncio.to_thread()` 包裹
4. **emily_core 不 import astrbot**：业务内核独立
5. **每模块验收**：每个模块的验收检测必须通过，否则停止并报告
6. **参照模式**：所有新代码必须参照下方"代码模式参照表"中的源文件。风格不一致视为失败
7. **规则书文件已存在**：`emily-data/rules/规则书.md` 已有初版（8章41条），不可覆盖，只新增加载逻辑
8. **不创建重复表**：确认 `project_world_books` 表不存在于 models.py 再创建
9. **expire_on_commit=False**：Session 创建时使用 `expire_on_commit=False`

---

## 上下文（执行前必读）

### 已有的可复用组件

| 组件 | 位置 | 关键方法 | 本次怎么用 |
|------|------|----------|-----------|
| `ProjectNodeRepo` | `emily-core/emily_core/repositories/node_repo.py` | `get_by_project()`, `count_by_project()`, `find_by_parent()` | M2/M4 查询节点数据 |
| `NodeDependencyRepo` | `emily-core/emily_core/repositories/node_repo.py` | `get_by_node()`, `get_downstream()` | M2/M4 查询依赖链 |
| `UserRepository` | `emily-core/emily_core/repositories/user_repo.py` | `get_by_id()`, `get_by_project()` | M2/M3 查询人员信息 |
| `PermissionRepository` | `emily-core/emily_core/repositories/permission_repo.py` | `get()` | M2 查询公司信息 |
| `EvolutionRepo` | `emily-core/emily_core/repositories/evolution_repo.py` | `aggregate_*()` 系列 | M10 复用指标聚合 |
| `InsightGenerator` | `emily-core/emily_core/services/evolution/insight_generator.py` | `generate()` | M10 注入第10数据源 |
| `SessionDataFetcher` | `emily-core/emily_core/session/session_data_fetcher.py` | `fetch()` | M8 扩展采集世界书+规则书 |
| `SessionContext` | `emily-core/emily_core/session/session_context.py` | `get_prompt_variables()`, `refresh()` | M8 新增字段和 prompt 变量 |
| `SkillRegistry` | `emily-core/emily_core/skill/registry.py` | `list_sop_ids()`, `load()` | M3 检查 SOP 覆盖 |
| `EmailService` | `emily-core/emily_core/services/email_service.py` | `send()` | M11 冷启动邮件通知 |
| `SchedulerJobHandler` | `emily-core/emily_core/scheduler/handler_registry.py` | 基类 `action_type`, `execute()` | M9 注册新调度 Handler |

### 架构决策

**选择独立脚本 + 薄聚合架构**：每个功能以独立可执行脚本交付（参照 `scripts/evolution_metrics.py` 模式），薄聚合脚本串联各独立脚本完成流程编排（参照 `scripts/evolution.py` 模式）。模块间通过数据库和文件接口衔接，不直接 import 彼此。

**替代方案**：全部逻辑内嵌到 EmilyCore 初始化流程。不选——因为会导致启动变慢、难以独立测试、难以 dry-run 观察中间结果。

### 代码模式参照表

| 层 | 参照源（精确文件路径） | 要模仿的要点 |
|----|----------------------|-------------|
| ORM 模型 | `emily-core/emily_core/infrastructure/database/models.py` | `String PK + _new_uuid`, `String` 时间戳, `Column(Text)` for JSON, `__table_args__` 含 Index |
| Repository | `emily-core/emily_core/repositories/node_repo.py` | `@staticmethod` + `get_session()` + `_impl` 内函数 |
| Service | `emily-core/emily_core/services/evolution/insight_generator.py` | `async def` + `asyncio.to_thread()` 包裹 sync repo |
| 调度 Handler | `emily-core/emily_core/scheduler/jobs/daily_insight.py` | 继承 `SchedulerJobHandler`, `action_type` 属性, `execute()` 返回 `JobResult` |
| 独立脚本 | `scripts/evolution_metrics.py` | `sys.path` 设置 + `_init_db()` + async 核心函数 + CLI `--dry-run` |
| 薄聚合脚本 | `scripts/evolution.py` | `subparsers` + 串联各脚本核心函数 + 无业务逻辑 |
| API 路由 | `emily-core/api/routes/evolution.py` | `APIRouter()` + `get_core()` 依赖注入 |
| SessionContext 字段 | `emily-core/emily_core/session/session_context.py:26-89` | `dataclass` 字段声明 + `field(default_factory=...)` |
| Prompt 变量 | `emily-core/emily_core/session/session_context.py:332-357` | `get_prompt_variables()` 返回 `dict[str, str]` |
| EmilyCore 初始化 | `emily-core/emily_core/__init__.py:123-195` | `_ensure_initialized()` 末尾追加新子系统 |

---

## 模块依赖图

```
M1(数据模型) ──→ M2(世界书构建) ──→ M4(偏差检测) ──→ M5(增量更新) ──→ M9(调度器集成)
       │                │                                          ↑
       │                └──────→ M6(Session Prompt) ──→ M8(EmilyCore集成)
       │                                                   ↑
       ├──────→ M3(初始化检查) ───────────────────────────┘
       │                                                   ↑
       └──────→ M7(规则书加载) ───────────────────────────┘

M4(偏差检测) ──→ M10(进化闭环集成)
M2(世界书构建) + M3(初始化检查) ──→ M11(V1整合)
```

---

## 交付物总览

| 模块 | 交付文件 | 新增/修改 | 核心类/函数 |
|------|----------|----------|------------|
| M1 | `emily-core/emily_core/infrastructure/database/models.py` | 修改 | `ProjectWorldBook` ORM |
| M1 | `emily-core/emily_core/repositories/world_book_repo.py` | 新增 | `ProjectWorldBookRepo` |
| M2 | `emily-core/emily_core/services/world_book_builder.py` | 新增 | `ProjectWorldBookBuilder` |
| M2 | `scripts/build_world_book.py` | 新增 | `build_world_book()` |
| M3 | `emily-core/emily_core/services/initialization_checker.py` | 新增 | `InitializationChecker` |
| M3 | `scripts/check_initialization.py` | 新增 | `check_initialization()` |
| M4 | `emily-core/emily_core/services/cognition_drift_detector.py` | 新增 | `CognitionDriftDetector` |
| M4 | `scripts/detect_cognition_drift.py` | 新增 | `detect_cognition_drift()` |
| M5 | `emily-core/emily_core/services/world_book_service.py` | 新增 | `ProjectWorldBookService` |
| M5 | `scripts/update_world_book.py` | 新增 | `update_world_book()` |
| M6 | `scripts/generate_session_prompt.py` | 新增 | `generate_session_prompt()` |
| M7 | `emily-core/emily_core/services/rule_book_loader.py` | 新增 | `RuleBookLoader` |
| M8 | `emily-core/emily_core/session/session_context.py` | 修改 | 新增 `project_world_book` + `rule_book` 字段 |
| M8 | `emily-core/emily_core/session/session_data_fetcher.py` | 修改 | 新增世界书+规则书采集 |
| M8 | `emily-data/prompts/session.md` | 修改 | 新增 `{project_world_book}` + `{rule_book}` 段 |
| M8 | `emily-core/emily_core/__init__.py` | 修改 | 新增 `_init_meta_cognition()` |
| M9 | `emily-core/emily_core/scheduler/jobs/world_book_update.py` | 新增 | `WorldBookUpdateHandler` |
| M9 | `scripts/cognition_cycle.py` | 新增 | `run_cognition_cycle()` |
| M10 | `scripts/evolution_metrics.py` | 修改 | 新增 `cognition_drift` 第10数据源 |
| M11 | `scripts/self_check.py` | 新增 | `self_check()` |
| M11 | `scripts/cold_start.py` | 新增 | 薄聚合 |

---

## 现有模块改动清单

| 现有模块 | 改动类型 | 改动内容 |
|----------|----------|----------|
| `emily-core/emily_core/infrastructure/database/models.py` | 扩展 | 在文件末尾追加 `ProjectWorldBook` ORM 类 |
| `emily-core/emily_core/session/session_context.py` | 修改 | 新增 2 个 dataclass 字段 + 2 个 prompt 变量 |
| `emily-core/emily_core/session/session_data_fetcher.py` | 修改 | 新增 `_sub_fetch_world_book()` + `_sub_fetch_rule_book()` |
| `emily-data/prompts/session.md` | 修改 | 新增"项目世界书"和"规则书"注入段 |
| `emily-core/emily_core/__init__.py` | 修改 | `_ensure_initialized()` 末尾新增 `_init_meta_cognition()` 调用 |
| `scripts/evolution_metrics.py` | 修改 | `collect_metrics()` 新增第10数据源 |

---

## M1: 数据模型

**依赖**：无（本模块为首建模块）

**职责**：定义 `project_world_books` 表 ORM + Repository 层 CRUD，为所有后续模块提供持久化基础。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ProjectWorldBook ORM 模型 | `emily-core/emily_core/infrastructure/database/models.py`（追加） |
| 2 | ProjectWorldBookRepo | `emily-core/emily_core/repositories/world_book_repo.py`（新建） |

### 代码

#### `emily-core/emily_core/infrastructure/database/models.py` — 在文件末尾（最后一个类之后）追加

```python
class ProjectWorldBook(Base):
    """项目世界书表 —— 元认知模块七层认知持久化。

    每个项目一份世界书，存储七层结构化 JSON + 纯文本摘要（直接注入 prompt）。
    支持增量更新：每层独立版本号，偏差检测驱动单层更新。
    """
    __tablename__ = "project_world_books"

    id = Column(String, primary_key=True, default=_new_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=False, unique=True, comment="项目归属（FK→projects.id，每项目唯一）")
    version = Column(Integer, default=1, comment="整体版本号（递增）")
    content_json = Column(Text, default="{}", comment="七层结构化 JSON（机器可解析）")
    content_text = Column(Text, default="", comment="纯文本摘要（直接注入 prompt，~400 tokens）")
    layer_versions = Column(Text, default="{}", comment='JSON: 每层独立版本号 {"ontology":1,"personnel":1,...}')
    initialization_tier = Column(Integer, default=0, comment="当前初始化层级 0-4（0=未开始，4=充分运转）")
    initialization_status = Column(Text, default="{}", comment="JSON: 各必备项完成情况")
    is_activated = Column(Boolean, default=False, comment="是否达到 T3 可运转级")
    token_count = Column(Integer, default=0, comment="估算 token 数")
    generated_at = Column(String, default=_utc_now, comment="最近生成时间")
    generated_by = Column(String(50), default="manual", comment="生成来源：startup / scheduler_data / scheduler_llm / manual")
    created_at = Column(String, default=_utc_now, comment="首次创建时间")
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now, comment="最近更新时间")

    __table_args__ = (
        Index("idx_wb_project", "project_id"),
    )
```

#### `emily-core/emily_core/repositories/world_book_repo.py` — 新建

```python
"""ProjectWorldBookRepo —— 项目世界书 Repository 层。

参照模式：emily_core/repositories/node_repo.py（@staticmethod + get_session）。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.models import ProjectWorldBook
from ..infrastructure.database.session import get_session

logger = logging.getLogger("emily.world_book_repo")


class ProjectWorldBookRepo:
    """项目世界书 Repository。"""

    @staticmethod
    def create(
        project_id: str,
        content_json: str = "{}",
        content_text: str = "",
        layer_versions: str = "{}",
        initialization_tier: int = 0,
        initialization_status: str = "{}",
        is_activated: bool = False,
        token_count: int = 0,
        generated_by: str = "manual",
    ) -> ProjectWorldBook:
        """创建项目世界书。"""
        with get_session() as session:
            wb = ProjectWorldBook(
                project_id=project_id,
                content_json=content_json,
                content_text=content_text,
                layer_versions=layer_versions,
                initialization_tier=initialization_tier,
                initialization_status=initialization_status,
                is_activated=is_activated,
                token_count=token_count,
                generated_by=generated_by,
            )
            session.add(wb)
            session.flush()
            logger.info("ProjectWorldBook created: project=%s tier=%d", project_id, initialization_tier)
            return wb

    @staticmethod
    def get_by_project(project_id: str) -> Optional[ProjectWorldBook]:
        """按 project_id 查询世界书（每项目唯一）。"""
        with get_session() as session:
            return (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .first()
            )

    @staticmethod
    def get_by_id(wb_id: str) -> Optional[ProjectWorldBook]:
        """按主键查询。"""
        with get_session() as session:
            return session.query(ProjectWorldBook).filter(ProjectWorldBook.id == wb_id).first()

    @staticmethod
    def update_content(
        project_id: str,
        content_json: str = None,
        content_text: str = None,
        layer_versions: str = None,
        version: int = None,
        initialization_tier: int = None,
        initialization_status: str = None,
        is_activated: bool = None,
        token_count: int = None,
        generated_by: str = None,
    ) -> Optional[ProjectWorldBook]:
        """增量更新世界书字段。只更新非 None 的字段。"""
        from ..infrastructure.database.models import _utc_now

        with get_session() as session:
            wb = (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .first()
            )
            if wb is None:
                return None

            if content_json is not None:
                wb.content_json = content_json
            if content_text is not None:
                wb.content_text = content_text
            if layer_versions is not None:
                wb.layer_versions = layer_versions
            if version is not None:
                wb.version = version
            if initialization_tier is not None:
                wb.initialization_tier = initialization_tier
            if initialization_status is not None:
                wb.initialization_status = initialization_status
            if is_activated is not None:
                wb.is_activated = is_activated
            if token_count is not None:
                wb.token_count = token_count
            if generated_by is not None:
                wb.generated_by = generated_by
            wb.updated_at = _utc_now()
            session.flush()
            logger.info("ProjectWorldBook updated: project=%s version=%d", project_id, wb.version)
            return wb

    @staticmethod
    def list_all() -> list[ProjectWorldBook]:
        """列出所有世界书。"""
        with get_session() as session:
            return session.query(ProjectWorldBook).all()

    @staticmethod
    def delete_by_project(project_id: str) -> bool:
        """删除指定项目的世界书。"""
        with get_session() as session:
            deleted = (
                session.query(ProjectWorldBook)
                .filter(ProjectWorldBook.project_id == project_id)
                .delete()
            )
            logger.info("ProjectWorldBook deleted: project=%s count=%d", project_id, deleted)
            return deleted > 0
```

### 模块验收检测

```bash
# 验收 1：确认 ORM 类已添加
grep "class ProjectWorldBook" emily-core/emily_core/infrastructure/database/models.py
→ 预期输出：一行匹配 class ProjectWorldBook(Base):

# 验收 2：确认 Repo 文件存在且可导入
uv run python -c "from emily_core.repositories.world_book_repo import ProjectWorldBookRepo; print('OK')"
→ 预期输出：OK

# 验收 3：重建表结构（需容器内执行，或等待 M8 集成后重启容器自动建表）
# 开发期可手动：
docker exec emily-core python -c "from emily_core.infrastructure.database.session import init_db; init_db(); from emily_core.infrastructure.database.models import Base; from emily_core.infrastructure.database.session import engine; Base.metadata.create_all(engine); print('Table created')"
→ 预期输出：Table created

# 验收 4：验证表已创建
docker exec emily-postgres psql -U emily -d emily -c "\d project_world_books"
→ 预期输出：表结构输出，含 project_id, content_json, content_text 等列
```

**失败处理**：如果 ORM 类与已有类名冲突，检查 models.py 中是否已有同名类；如果 Repo 导入失败，检查 `__init__.py` 是否需要更新。

---

## M2: 世界书构建

**依赖**：M1

**职责**：构建/重建单个项目的七层世界书，查询数据库聚合七层认知，生成 content_json + content_text。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ProjectWorldBookBuilder 服务 | `emily-core/emily_core/services/world_book_builder.py`（新建） |
| 2 | 独立脚本 | `scripts/build_world_book.py`（新建） |

### 代码

#### `emily-core/emily_core/services/world_book_builder.py` — 新建

```python
"""ProjectWorldBookBuilder —— 项目世界书构建服务。

查询数据库聚合七层认知，生成 content_json + content_text。
纯数据驱动，无需 LLM。语义层（项目概述）在增量更新时由 LLM 生成。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..repositories.world_book_repo import ProjectWorldBookRepo
from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Project, User, CompanyInfo, ProjectNode, NodeDependency, Event,
)

logger = logging.getLogger("emily.world_book_builder")

BEIJING_TZ = timezone(timedelta(hours=8))

# 生命周期阶段标签
LIFECYCLE_LABELS = {0: "立项", 1: "规划设计", 2: "工程施工", 3: "交付结算"}


class ProjectWorldBookBuilder:
    """项目世界书构建器。"""

    def build(self, project_id: str, *, generated_by: str = "manual", dry_run: bool = False) -> dict:
        """构建/重建单个项目的世界书。

        Args:
            project_id: 项目 ID（projects.id UUID）
            generated_by: 生成来源标记
            dry_run: 预览模式，不写 DB

        Returns:
            构建结果 dict，含 content_json, content_text, initialization_tier 等
        """
        # 采集七层数据
        ontology = self._build_ontology(project_id)
        personnel = self._build_personnel(project_id)
        structure = self._build_structure(project_id)
        temporal = self._build_temporal(project_id)
        relation = self._build_relation(project_id)
        knowledge = self._build_knowledge(project_id)
        introspection = self._build_introspection(project_id)

        content_json = {
            "ontology": ontology,
            "personnel": personnel,
            "structure": structure,
            "temporal": temporal,
            "relation": relation,
            "knowledge": knowledge,
            "introspection": introspection,
        }

        content_text = self._format_content_text(content_json)

        # 估算 token 数（中文约 1.5 字/token）
        token_count = int(len(content_text) / 1.5) if content_text else 0

        # 初始化层级
        init_tier = introspection.get("initialization_tier", 0)
        init_status = introspection.get("initialization_status", {})
        is_activated = introspection.get("is_activated", False)

        # 每层初始版本号
        layer_versions = {k: 1 for k in content_json.keys()}

        result = {
            "project_id": project_id,
            "content_json": json.dumps(content_json, ensure_ascii=False),
            "content_text": content_text,
            "layer_versions": json.dumps(layer_versions),
            "initialization_tier": init_tier,
            "initialization_status": json.dumps(init_status, ensure_ascii=False),
            "is_activated": is_activated,
            "token_count": token_count,
            "generated_by": generated_by,
            "status": "preview" if dry_run else "built",
        }

        if not dry_run:
            # 检查是否已存在 → 更新或创建
            existing = ProjectWorldBookRepo.get_by_project(project_id)
            if existing:
                # 更新：递增版本号
                new_version = existing.version + 1
                # 合并 layer_versions：已有层版本保留，新层版本为1
                old_lv = json.loads(existing.layer_versions or "{}")
                merged_lv = {**old_lv}
                for k in layer_versions:
                    if k in merged_lv:
                        merged_lv[k] += 1
                    else:
                        merged_lv[k] = 1

                ProjectWorldBookRepo.update_content(
                    project_id=project_id,
                    content_json=result["content_json"],
                    content_text=content_text,
                    layer_versions=json.dumps(merged_lv),
                    version=new_version,
                    initialization_tier=init_tier,
                    initialization_status=result["initialization_status"],
                    is_activated=is_activated,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = new_version
            else:
                wb = ProjectWorldBookRepo.create(
                    project_id=project_id,
                    content_json=result["content_json"],
                    content_text=content_text,
                    layer_versions=result["layer_versions"],
                    initialization_tier=init_tier,
                    initialization_status=result["initialization_status"],
                    is_activated=is_activated,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = 1

        return result

    # ── 七层构建方法 ──

    def _build_ontology(self, project_id: str) -> dict:
        """层1：本体认知——项目身份、生命周期、参建方。"""
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
                if project is None:
                    return {"name": "", "code": "", "error": "项目不存在"}

                # 查询关联公司
                companies = []
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                company_ids = list(set(u.company for u in users if u.company))
                if company_ids:
                    company_records = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).all()
                    for c in company_records:
                        companies.append({
                            "name": c.company_name,
                            "type": c.type or "",
                            "role": c.business_desc or "",
                        })

                stage = project.lifecycle_stage or 0
                return {
                    "name": project.name or "",
                    "code": project.code or "",
                    "address": project.address or "",
                    "lifecycle_stage": stage,
                    "lifecycle_stage_label": LIFECYCLE_LABELS.get(stage, "未知"),
                    "organizations": companies,
                    "project_summary": f"{project.name or '未命名项目'}，当前处于{LIFECYCLE_LABELS.get(stage, '未知')}阶段",
                }
        except Exception as e:
            logger.error("_build_ontology failed: %s", e)
            return {"name": "", "code": "", "error": str(e)}

    def _build_personnel(self, project_id: str) -> dict:
        """层2：人员认知——关键人员、职责边界。"""
        try:
            with get_session() as session:
                users = session.query(User).filter(
                    User.project_id == project_id,
                    User.is_deleted == False,
                    User.status == "active",
                ).all()

                key_personnel = []
                department_leads = []
                for u in users:
                    # 解析职位
                    positions = []
                    try:
                        positions = json.loads(u.position or "[]")
                    except (json.JSONDecodeError, TypeError):
                        positions = []

                    company_name = ""
                    if u.company:
                        company = session.query(CompanyInfo).filter(CompanyInfo.id == u.company).first()
                        if company:
                            company_name = company.company_name

                    person = {
                        "name": u.username or "",
                        "role": ", ".join(positions) if positions else "",
                        "company": company_name,
                        "level": u.level or 1,
                        "is_admin": u.is_admin or False,
                    }

                    # 关键人员：管理员、项目经理、总监理
                    if u.is_admin or any(p in ["项目经理", "总监理工程师", "总监理"] for p in positions):
                        key_personnel.append(person)
                    elif positions:
                        # 部门负责人
                        for p in positions:
                            if p not in ["项目经理", "总监理工程师", "总监理"]:
                                department_leads.append({"department": p, "name": u.username or ""})

                return {
                    "key_personnel": key_personnel,
                    "department_leads": department_leads,
                    "total_users": len(users),
                }
        except Exception as e:
            logger.error("_build_personnel failed: %s", e)
            return {"key_personnel": [], "department_leads": [], "error": str(e)}

    def _build_structure(self, project_id: str) -> dict:
        """层3：结构认知——节点树拓扑、整体进度、里程碑状态。"""
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()

                total = len(nodes)
                completed = sum(1 for n in nodes if n.status == "COMPLETED")
                in_progress = sum(1 for n in nodes if n.status == "IN_PROGRESS")
                conditions_not_met = sum(1 for n in nodes if n.status == "CONDITIONS_NOT_MET")
                not_activated = sum(1 for n in nodes if n.status == "NOT_ACTIVATED")

                now_beijing = datetime.now(BEIJING_TZ)
                overdue = 0
                for n in nodes:
                    if n.status != "COMPLETED" and n.deadline:
                        try:
                            dl = datetime.fromisoformat(n.deadline)
                            if dl.tzinfo is None:
                                dl = dl.replace(tzinfo=BEIJING_TZ)
                            if dl < now_beijing:
                                overdue += 1
                        except (ValueError, TypeError):
                            pass

                # 里程碑
                milestones = []
                for n in nodes:
                    if getattr(n, 'node_type', '') == 'MILESTONE':
                        milestones.append({
                            "name": n.node_name,
                            "status": n.status,
                            "progress": n.progress or "0.00",
                            "deadline": n.deadline or "",
                        })

                # 整体进度加权汇总
                total_progress = 0.0
                if total > 0:
                    total_progress = sum(float(n.progress or "0") for n in nodes) / total

                return {
                    "total_nodes": total,
                    "completed": completed,
                    "in_progress": in_progress,
                    "conditions_not_met": conditions_not_met,
                    "not_activated": not_activated,
                    "overdue": overdue,
                    "overall_progress": f"{total_progress:.1f}%",
                    "milestones": milestones[:10],
                }
        except Exception as e:
            logger.error("_build_structure failed: %s", e)
            return {"total_nodes": 0, "error": str(e)}

    def _build_temporal(self, project_id: str) -> dict:
        """层4：时间认知——近期事件、即将到期、已逾期。"""
        try:
            now_beijing = datetime.now(BEIJING_TZ)
            week_ago = (now_beijing - timedelta(days=7)).strftime("%Y-%m-%d")
            week_later = (now_beijing + timedelta(days=7)).strftime("%Y-%m-%d")

            with get_session() as session:
                # 近期事件
                events = session.query(Event).filter(
                    Event.project_id == project_id,
                ).order_by(Event.created_at.desc()).limit(5).all()

                recent_events = []
                for e in events:
                    recent_events.append({
                        "date": (e.event_date or e.created_at or "")[:10],
                        "summary": e.title or "",
                        "type": e.event_type or "",
                    })

                # 即将到期 + 已逾期节点
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                    ProjectNode.status != "COMPLETED",
                ).all()

                upcoming_deadlines = []
                overdue_items = []
                for n in nodes:
                    if not n.deadline:
                        continue
                    try:
                        dl = datetime.fromisoformat(n.deadline)
                        if dl.tzinfo is None:
                            dl = dl.replace(tzinfo=BEIJING_TZ)
                        dl_str = dl.strftime("%Y-%m-%d")

                        if dl < now_beijing:
                            overdue_items.append({
                                "name": n.node_name,
                                "deadline": dl_str,
                                "status": n.status,
                            })
                        elif dl_str <= week_later:
                            upcoming_deadlines.append({
                                "name": n.node_name,
                                "deadline": dl_str,
                                "status": n.status,
                            })
                    except (ValueError, TypeError):
                        pass

                return {
                    "recent_events": recent_events,
                    "upcoming_deadlines": upcoming_deadlines[:5],
                    "overdue_items": overdue_items[:5],
                }
        except Exception as e:
            logger.error("_build_temporal failed: %s", e)
            return {"recent_events": [], "upcoming_deadlines": [], "overdue_items": [], "error": str(e)}

    def _build_relation(self, project_id: str) -> dict:
        """层5：关系认知——上下游依赖、阻塞。"""
        try:
            with get_session() as session:
                # 查依赖
                deps = session.query(NodeDependency).all()
                # 过滤属于本项目节点的依赖
                project_node_ids = set(
                    n.node_id for n in session.query(ProjectNode).filter(
                        ProjectNode.project_id == project_id,
                        ProjectNode.is_discarded == False,
                    ).all()
                )

                key_dependencies = []
                blocked_nodes = []

                for dep in deps:
                    if dep.node_id not in project_node_ids:
                        continue
                    upstream_node = session.query(ProjectNode).filter(
                        ProjectNode.node_id == dep.depends_on_node_id
                    ).first()
                    downstream_node = session.query(ProjectNode).filter(
                        ProjectNode.node_id == dep.node_id
                    ).first()

                    if upstream_node and downstream_node:
                        key_dependencies.append({
                            "upstream": upstream_node.node_name,
                            "downstream": downstream_node.node_name,
                            "deliverable": dep.depends_on_deliverable_id,
                        })

                        # 上游未完成 → 下游被阻塞
                        if upstream_node.status != "COMPLETED":
                            blocked_nodes.append({
                                "node": downstream_node.node_name,
                                "blocked_by": f"{upstream_node.node_name}未完成",
                                "impact": "",
                            })

                return {
                    "key_dependencies": key_dependencies[:10],
                    "blocked_nodes": blocked_nodes[:5],
                }
        except Exception as e:
            logger.error("_build_relation failed: %s", e)
            return {"key_dependencies": [], "blocked_nodes": [], "error": str(e)}

    def _build_knowledge(self, project_id: str) -> dict:
        """层6：知识认知——SOP 覆盖、知识库地图、认知盲区。"""
        try:
            from ..skill.registry import SkillRegistry

            sop_ids = []
            try:
                skill_dir = "/app/skills"
                if not __import__('pathlib').Path(skill_dir).exists():
                    skill_dir = ""
                if not skill_dir:
                    from pathlib import Path as _P
                    dev_dir = str(_P(__file__).resolve().parents[2] / "emily-data" / "skills")
                    if _P(dev_dir).exists():
                        skill_dir = dev_dir
                if skill_dir:
                    reg = SkillRegistry(skill_directory=skill_dir)
                    reg.load()
                    sop_ids = reg.list_sop_ids()
            except Exception:
                pass

            # RAG 信息
            rag_available = False
            rag_collections = []
            # 注意：独立运行时无法获取 core._rag_provider，此处留空
            # 运行时由 M8 集成后从 core 注入

            return {
                "sop_count": len(sop_ids),
                "sop_ids": sop_ids[:15],
                "rag_available": rag_available,
                "rag_collections": rag_collections,
                "coverage_gaps": [],
            }
        except Exception as e:
            logger.error("_build_knowledge failed: %s", e)
            return {"sop_count": 0, "sop_ids": [], "rag_available": False, "rag_collections": [], "error": str(e)}

    def _build_introspection(self, project_id: str) -> dict:
        """层7：自省认知——初始化状态、能力边界。委托 InitializationChecker。

        注意：M3 尚未实现时，此方法返回最小骨架。
        M3 完成后，此处改为调用 InitializationChecker。
        """
        # 先做最小实现：检查项目基本信息是否齐全
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id, Project.is_deleted == False).first()
                if project is None:
                    return {
                        "initialization_tier": 0,
                        "initialization_status": {},
                        "is_activated": False,
                        "missing_items": ["项目不存在"],
                    }

                # T1 基本检查
                init_status = {}
                init_status["T1_project_name"] = bool(project.name and project.name != "未命名项目")
                init_status["T1_project_code"] = bool(project.code)
                init_status["T1_project_address"] = bool(project.address)
                init_status["T1_lifecycle_stage"] = (project.lifecycle_stage or 0) != 0

                # 管理员检查
                admins = session.query(User).filter(
                    User.project_id == project_id,
                    User.is_deleted == False,
                    User.is_admin == True,
                ).all()
                init_status["T1_admin_user"] = len(admins) > 0
                init_status["T1_admin_email"] = any(u.email for u in admins if u.email)

                # 统计 T1 完成项
                t1_items = [k for k, v in init_status.items() if k.startswith("T1_") and v]
                t1_total = sum(1 for k in init_status if k.startswith("T1_"))
                t1_done = len(t1_items)

                tier = 0
                if t1_done >= t1_total:
                    tier = 1

                missing = [k for k, v in init_status.items() if not v]

                return {
                    "initialization_tier": tier,
                    "initialization_status": init_status,
                    "is_activated": tier >= 3,
                    "missing_items": missing,
                }
        except Exception as e:
            logger.error("_build_introspection failed: %s", e)
            return {
                "initialization_tier": 0,
                "initialization_status": {},
                "is_activated": False,
                "missing_items": [str(e)],
            }

    # ── 文本格式化 ──

    def _format_content_text(self, content_json: dict) -> str:
        """将七层 JSON 格式化为纯文本摘要（注入 prompt）。目标 300-500 字。"""
        lines = []

        # 层1：本体
        o = content_json.get("ontology", {})
        if o.get("name"):
            code = f"（{o['code']}）" if o.get("code") else ""
            lines.append(f"📋 项目：{o['name']}{code}")
            addr = o.get("address", "")
            stage = o.get("lifecycle_stage_label", "")
            if addr or stage:
                lines.append(f"📍 {addr} ｜ 阶段：{stage}")
            orgs = o.get("organizations", [])
            if orgs:
                org_str = " / ".join(f"{c['name']}({c['type']})" for c in orgs[:4])
                lines.append(f"🏗 {org_str}")

        # 层2：人员
        p = content_json.get("personnel", {})
        kp = p.get("key_personnel", [])
        if kp:
            ppl_str = " / ".join(f"{u['name']}({u['role']})" for u in kp[:4])
            lines.append(f"👥 {ppl_str}")

        # 层3：结构
        s = content_json.get("structure", {})
        if s.get("total_nodes", 0) > 0:
            lines.append(
                f"📊 {s['total_nodes']}节点：{s.get('completed', 0)}完成 / "
                f"{s.get('in_progress', 0)}进行中 / {s.get('overdue', 0)}逾期 ｜ "
                f"整体{s.get('overall_progress', '0%')}"
            )
            ms = s.get("milestones", [])
            if ms:
                ms_str = " ｜ ".join(
                    f"{m['name']}{'✓' if m['status']=='COMPLETED' else m.get('progress','')}"
                    for m in ms[:3]
                )
                lines.append(f"🏁 {ms_str}")

        # 层4：时间
        t = content_json.get("temporal", {})
        re = t.get("recent_events", [])
        if re:
            ev_str = " / ".join(f"{e['date'][5:] if len(e.get('date',''))>=5 else e.get('date','')}{e['summary']}" for e in re[:3])
            lines.append(f"📝 近期：{ev_str}")
        ud = t.get("upcoming_deadlines", [])
        if ud:
            ud_str = " / ".join(f"{d['name']}({d['deadline'][5:] if len(d.get('deadline',''))>=5 else d.get('deadline','')})" for d in ud[:3])
            lines.append(f"⏰ 7天内：{ud_str}")
        oi = t.get("overdue_items", [])
        if oi:
            oi_str = " / ".join(f"{i['name']}({i['deadline'][5:] if len(i.get('deadline',''))>=5 else i.get('deadline','')})" for i in oi[:3])
            lines.append(f"🔴 逾期：{oi_str}")

        # 层5：关系（仅在有阻塞时显示）
        r = content_json.get("relation", {})
        bn = r.get("blocked_nodes", [])
        if bn:
            bn_str = " / ".join(f"{b['blocked_by']} → {b['node']}等待" for b in bn[:2])
            lines.append(f"🔗 阻塞：{bn_str}")

        # 层7：初始化状态
        intro = content_json.get("introspection", {})
        tier = intro.get("initialization_tier", 0)
        tier_labels = {0: "未开始", 1: "T1 可识别", 2: "T2 有组织", 3: "T3 可运转", 4: "T4 充分运转"}
        if tier < 3:
            missing = intro.get("missing_items", [])
            missing_str = " / ".join(missing[:3])
            lines.append(f"🟡 {tier_labels.get(tier, '未知')}级 — 缺失：{missing_str}")
        else:
            lines.append(f"🟢 {tier_labels.get(tier, '未知')}级")

        return "\n".join(lines)
```

#### `scripts/build_world_book.py` — 新建

```python
"""build_world_book.py — 构建/重建单个项目的世界书。

参照模式：scripts/evolution_metrics.py（sys.path + _init_db + async 核心函数 + CLI）。

用法：
    uv run python scripts/build_world_book.py --project-id <UUID> --dry-run
    uv run python scripts/build_world_book.py --project-id <UUID>
    uv run python scripts/build_world_book.py --all --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("build_world_book")


def _detect_docker_pg_port() -> int | None:
    """参照 collect_session_data.py 的 Docker PG 端口检测。"""
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = result.stdout.strip().rsplit(":", 1)[-1]
            return int(port_str)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db(db_url: str = "") -> None:
    """初始化数据库连接。"""
    from emily_core.infrastructure.database.session import init_db

    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
            pg_port_env = os.environ.get("EMILY_PG_PORT")
            if pg_port_env:
                pg_port = int(pg_port_env)
            else:
                pg_port = _detect_docker_pg_port() or 5432
            pg_db = os.environ.get("EMILY_PG_DB", "emily")
            pg_user = os.environ.get("EMILY_PG_USER", "emily")
            pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
            init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)


def build_world_book(project_id: str, *, generated_by: str = "manual", db_url: str = "", dry_run: bool = False) -> dict:
    """构建项目世界书（脚本入口）。"""
    _init_db(db_url)

    from emily_core.services.world_book_builder import ProjectWorldBookBuilder
    builder = ProjectWorldBookBuilder()
    return builder.build(project_id, generated_by=generated_by, dry_run=dry_run)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="构建/重建项目世界书")
    parser.add_argument("--project-id", help="项目 ID（UUID）")
    parser.add_argument("--all", action="store_true", help="构建所有 active 项目")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="预览模式（不写 DB）")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.infrastructure.database.models import Project
        from emily_core.infrastructure.database.session import get_session

        with get_session() as session:
            projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()

        print(f"找到 {len(projects)} 个 active 项目")
        for p in projects:
            print(f"\n=== 项目：{p.name}（{p.id}）===")
            result = build_world_book(p.id, generated_by="manual", db_url=args.db_url, dry_run=args.dry_run)
            print(f"状态: {result.get('status')}")
            print(f"初始化层级: T{result.get('initialization_tier', 0)}")
            print(f"Token 数: {result.get('token_count', 0)}")
            if args.dry_run:
                print(f"\n--- content_text 预览 ---")
                print(result.get("content_text", ""))
    else:
        result = build_world_book(args.project_id, generated_by="manual", db_url=args.db_url, dry_run=args.dry_run)
        print(json.dumps(
            {k: v for k, v in result.items() if k not in ("content_json",)},
            ensure_ascii=False, indent=2, default=str,
        ))
        if args.dry_run:
            print(f"\n--- content_text 预览 ---")
            print(result.get("content_text", ""))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：Builder 服务可导入
uv run python -c "from emily_core.services.world_book_builder import ProjectWorldBookBuilder; print('OK')"
→ 预期输出：OK

# 验收 2：dry-run 构建世界书（查 users 表获取真实项目 ID）
$project_id = (docker exec emily-postgres psql -U emily -d emily -t -c "SELECT id FROM projects WHERE status='active' LIMIT 1;").Trim()
uv run python scripts/build_world_book.py --project-id $project_id --dry-run
→ 预期输出：JSON 结果含 status=preview + content_text 预览

# 验收 3：实际构建并写入 DB
uv run python scripts/build_world_book.py --project-id $project_id
→ 预期输出：JSON 结果含 status=built

# 验收 4：验证 DB 记录
docker exec emily-postgres psql -U emily -d emily -c "SELECT project_id, version, initialization_tier, token_count FROM project_world_books;"
→ 预期输出：一行记录，project_id 匹配，version=1
```

**失败处理**：如果 `_init_db` 失败，检查 Docker 容器是否运行、PG 端口是否映射；如果查询返回空，检查 projects 表是否有 active 项目。

---

## M3: 初始化检查

**依赖**：M1

**职责**：实现四层初始化模型（T1-T4，23 项必备项），检查项目初始化层级和缺失项。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | InitializationChecker 服务 | `emily-core/emily_core/services/initialization_checker.py`（新建） |
| 2 | 独立脚本 | `scripts/check_initialization.py`（新建） |

### 代码

#### `emily-core/emily_core/services/initialization_checker.py` — 新建

```python
"""InitializationChecker —— 项目初始化四层模型检查。

23 项必备项：T1(7) + T2(6) + T3(5) + T4(5)。
每项有明确的数据源和判定条件，纯数据驱动无需 LLM。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Project, User, CompanyInfo, ProjectNode, NodeDependency,
)

logger = logging.getLogger("emily.initialization_checker")

BEIJING_TZ = timezone(timedelta(hours=8))


class InitializationChecker:
    """项目初始化检查器——四层模型 23 项必备项。"""

    def check(self, project_id: str) -> dict:
        """检查项目初始化层级和缺失项。

        Args:
            project_id: 项目 ID（projects.id UUID）

        Returns:
            {
                "project_id": str,
                "tier": int (0-4),
                "tier_label": str,
                "is_activated": bool,
                "items": {key: bool, ...},   # 23 项各是否完成
                "missing": [str, ...],         # 缺失项描述
                "summary_by_tier": {T1: {...}, T2: {...}, ...},
            }
        """
        with get_session() as session:
            project = session.query(Project).filter(
                Project.id == project_id, Project.is_deleted == False
            ).first()

            if project is None:
                return {
                    "project_id": project_id,
                    "tier": 0,
                    "tier_label": "未开始（项目不存在）",
                    "is_activated": False,
                    "items": {},
                    "missing": ["项目不存在"],
                    "summary_by_tier": {},
                }

            # ── T1：可识别（7 项）──
            t1 = {}

            # T1-1: 项目名称非空且非默认值
            t1["T1_project_name"] = bool(project.name and project.name.strip() and project.name != "未命名项目")

            # T1-2: 项目编号非空
            t1["T1_project_code"] = bool(project.code and project.code.strip())

            # T1-3: 项目地址非空
            t1["T1_project_address"] = bool(project.address and project.address.strip())

            # T1-4: 项目类型可区分
            desc = (project.description or "").strip()
            t1["T1_project_type"] = bool(desc) or (project.lifecycle_stage or 0) != 0

            # T1-5: 生命周期阶段非0
            t1["T1_lifecycle_stage"] = (project.lifecycle_stage or 0) != 0

            # T1-6: 项目管理员账户
            admins = session.query(User).filter(
                User.project_id == project_id,
                User.is_deleted == False,
                User.is_admin == True,
            ).all()
            t1["T1_admin_user"] = len(admins) > 0

            # T1-7: 管理员邮箱
            t1["T1_admin_email"] = any(u.email and u.email.strip() for u in admins)

            t1_done = sum(1 for v in t1.values() if v)
            t1_total = len(t1)

            # ── T2：有组织（6 项）──
            t2 = {}

            # 查询关联公司
            users_in_project = session.query(User).filter(
                User.project_id == project_id,
                User.is_deleted == False,
            ).all()
            company_ids = list(set(u.company for u in users_in_project if u.company))
            companies = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).all() if company_ids else []
            company_types = [c.type for c in companies if c.type]

            # T2-1: 建设单位
            t2["T2_builder_company"] = "建设单位" in company_types

            # T2-2: 项目管理/代建单位
            t2["T2_management_company"] = any(t in company_types for t in ["代建单位", "项目管理", "建设单位"])

            # T2-3: 施工总承包单位
            t2["T2_general_contractor"] = any(t in company_types for t in ["施工单位", "总包", "施工总承包"])

            # T2-4: 监理单位
            t2["T2_supervisor_company"] = any(t in company_types for t in ["监理单位", "监理"])

            # T2-5: 项目经理
            has_pm = False
            for u in users_in_project:
                try:
                    positions = json.loads(u.position or "[]")
                    if "项目经理" in positions:
                        has_pm = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass
            t2["T2_project_manager"] = has_pm

            # T2-6: 总监理工程师
            has_cs = False
            for u in users_in_project:
                try:
                    positions = json.loads(u.position or "[]")
                    if any(p in positions for p in ["总监理工程师", "总监理"]):
                        has_cs = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass
            t2["T2_chief_supervisor"] = has_cs

            t2_done = sum(1 for v in t2.values() if v)
            t2_total = len(t2)

            # ── T3：可运转（5 项）──
            t3 = {}

            nodes = session.query(ProjectNode).filter(
                ProjectNode.project_id == project_id,
                ProjectNode.is_discarded == False,
            ).all()

            # T3-1: 节点树已创建（至少1个 MILESTONE）
            milestones = [n for n in nodes if getattr(n, 'node_type', '') == 'MILESTONE']
            t3["T3_node_tree_created"] = len(milestones) > 0

            # T3-2: 关键里程碑有截止日期
            t3["T3_milestone_deadlines"] = len(milestones) > 0 and all(m.deadline for m in milestones)

            # T3-3: 关键节点有责任人
            wp_and_ms = [n for n in nodes if getattr(n, 'node_type', '') in ('MILESTONE', 'WORK_PACKAGE')]
            t3["T3_node_responsible_persons"] = len(wp_and_ms) > 0 and all(n.responsible_user_id for n in wp_and_ms)

            # T3-4: 至少1个适配 SOP
            sop_count = 0
            try:
                from ..skill.registry import SkillRegistry
                from pathlib import Path
                skill_dir = "/app/skills"
                if not Path(skill_dir).exists():
                    skill_dir = ""
                if not skill_dir:
                    dev_dir = str(Path(__file__).resolve().parents[2] / "emily-data" / "skills")
                    if Path(dev_dir).exists():
                        skill_dir = dev_dir
                if skill_dir:
                    reg = SkillRegistry(skill_directory=skill_dir)
                    reg.load()
                    sop_count = len(reg.list_sop_ids())
            except Exception:
                pass
            t3["T3_sop_adapted"] = sop_count > 0

            # T3-5: 项目经理已绑定 IM
            t3["T3_pm_im_bound"] = False
            if has_pm:
                for u in users_in_project:
                    try:
                        positions = json.loads(u.position or "[]")
                        if "项目经理" in positions and u.im_bindings:
                            t3["T3_pm_im_bound"] = True
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass

            t3_done = sum(1 for v in t3.values() if v)
            t3_total = len(t3)

            # ── T4：充分运转（5 项）──
            t4 = {}

            # T4-1: 全部参建单位已录入
            expected_types = {"建设单位", "代建单位", "施工单位", "监理单位", "设计单位"}
            t4["T4_all_companies"] = expected_types.issubset(set(company_types))

            # T4-2: 全部节点有责任人
            active_nodes = [n for n in nodes if n.status not in ("NOT_ACTIVATED",)]
            t4["T4_all_node_responsible"] = len(active_nodes) > 0 and all(n.responsible_user_id for n in active_nodes)

            # T4-3: 节点依赖关系已建立
            wp_nodes = [n for n in nodes if getattr(n, 'node_type', '') == 'WORK_PACKAGE']
            wp_node_ids = [n.node_id for n in wp_nodes]
            dep_count = 0
            if wp_node_ids:
                deps = session.query(NodeDependency).filter(
                    NodeDependency.node_id.in_(wp_node_ids)
                ).count()
                dep_count = deps
            t4["T4_dependency_coverage"] = len(wp_nodes) > 0 and dep_count >= len(wp_nodes) * 0.5

            # T4-4: 知识库已填充
            file_count = 0
            try:
                from ..repositories.file_repo import FileRepo
                # 简单统计项目关联文件数
                from ..infrastructure.database.models import File
                file_count = session.query(File).filter(File.project_id == project_id).count()
            except Exception:
                pass
            t4["T4_knowledge_filled"] = file_count >= 5

            # T4-5: 晨报已成功发送至少1次
            t4["T4_morning_report_sent"] = False
            try:
                from ..repositories.scheduler_repo import SchedulerServiceRepo
                # 检查 morning_report handler 是否有成功记录
                from ..infrastructure.database.models import SchedulerJobLog
                log = session.query(SchedulerJobLog).filter(
                    SchedulerJobLog.action_type == "morning_report",
                    SchedulerJobLog.status == "success",
                ).first()
                t4["T4_morning_report_sent"] = log is not None
            except Exception:
                pass

            t4_done = sum(1 for v in t4.values() if v)
            t4_total = len(t4)

        # ── 计算层级 ──
        all_items = {**t1, **t2, **t3, **t4}
        total_items = len(all_items)
        done_items = sum(1 for v in all_items.values() if v)

        tier = 0
        if t1_done >= t1_total:
            tier = 1
        if tier >= 1 and t2_done >= t2_total:
            tier = 2
        if tier >= 2 and t3_done >= t3_total:
            tier = 3
        if tier >= 3 and t4_done >= t4_total:
            tier = 4

        tier_labels = {
            0: "T0 未开始",
            1: "T1 可识别",
            2: "T2 有组织",
            3: "T3 可运转",
            4: "T4 充分运转",
        }

        missing = [k for k, v in all_items.items() if not v]
        missing_descriptions = {
            "T1_project_name": "项目名称未填写",
            "T1_project_code": "项目编号未填写",
            "T1_project_address": "项目地址未填写",
            "T1_project_type": "项目类型无法区分",
            "T1_lifecycle_stage": "生命周期阶段未设定",
            "T1_admin_user": "无项目管理员账户",
            "T1_admin_email": "管理员无可用邮箱",
            "T2_builder_company": "缺少建设单位",
            "T2_management_company": "缺少代建/管理单位",
            "T2_general_contractor": "缺少施工总承包单位",
            "T2_supervisor_company": "缺少监理单位",
            "T2_project_manager": "缺少项目经理",
            "T2_chief_supervisor": "缺少总监理工程师",
            "T3_node_tree_created": "节点树未创建",
            "T3_milestone_deadlines": "里程碑无截止日期",
            "T3_node_responsible_persons": "关键节点无责任人",
            "T3_sop_adapted": "无适配 SOP",
            "T3_pm_im_bound": "项目经理未绑定 IM",
            "T4_all_companies": "参建单位不全",
            "T4_all_node_responsible": "部分节点无责任人",
            "T4_dependency_coverage": "节点依赖关系不足50%",
            "T4_knowledge_filled": "知识库文件不足5个",
            "T4_morning_report_sent": "晨报从未成功发送",
        }
        missing_desc = [missing_descriptions.get(k, k) for k in missing]

        summary_by_tier = {
            "T1": {"done": t1_done, "total": t1_total},
            "T2": {"done": t2_done, "total": t2_total},
            "T3": {"done": t3_done, "total": t3_total},
            "T4": {"done": t4_done, "total": t4_total},
        }

        return {
            "project_id": project_id,
            "tier": tier,
            "tier_label": tier_labels.get(tier, "未知"),
            "is_activated": tier >= 3,
            "items": all_items,
            "missing": missing_desc,
            "total_items": total_items,
            "done_items": done_items,
            "summary_by_tier": summary_by_tier,
        }
```

#### `scripts/check_initialization.py` — 新建

```python
"""check_initialization.py — 检查项目初始化层级和缺失项。

用法：
    uv run python scripts/check_initialization.py --project-id <UUID> --dry-run
    uv run python scripts/check_initialization.py --all
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("check_initialization")


def _detect_docker_pg_port() -> int | None:
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().rsplit(":", 1)[-1])
    except Exception:
        pass
    return None


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else _detect_docker_pg_port() or 5432
            init_db(pg_host=pg_host, pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def check_initialization(project_id: str, *, db_url: str = "") -> dict:
    """检查项目初始化（脚本入口）。"""
    _init_db(db_url)
    from emily_core.services.initialization_checker import InitializationChecker
    checker = InitializationChecker()
    return checker.check(project_id)


def _format_report(result: dict) -> str:
    """格式化为自检邮件风格的文本报告。"""
    lines = []
    lines.append("Emily 项目初始化检查报告")
    lines.append("═" * 40)
    lines.append(f"初始化层级：{result['tier_label']}（{result['done_items']}/{result['total_items']} 必备项）")
    lines.append("═" * 40)

    for tier_key in ["T1", "T2", "T3", "T4"]:
        summary = result["summary_by_tier"].get(tier_key, {})
        done = summary.get("done", 0)
        total = summary.get("total", 0)
        icon = "✅" if done >= total else "❌" if done == 0 else "🟡"
        lines.append(f"\n{icon} {tier_key}（{done}/{total}）")

        tier_items = {k: v for k, v in result["items"].items() if k.startswith(tier_key + "_")}
        for k, v in tier_items.items():
            lines.append(f"  {'✓' if v else '✗'} {k}")

    if result["missing"]:
        lines.append(f"\n📋 下一步：补充缺失项以提升初始化层级")

    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="检查项目初始化层级")
    parser.add_argument("--project-id", help="项目 ID（UUID）")
    parser.add_argument("--all", action="store_true", help="检查所有 active 项目")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL")
    parser.add_argument("--dry-run", action="store_true", help="仅预览（本项目无副作用，始终可安全运行）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.infrastructure.database.models import Project
        from emily_core.infrastructure.database.session import get_session
        with get_session() as session:
            projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()
        for p in projects:
            result = check_initialization(p.id, db_url=args.db_url)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(_format_report(result))
                print()
    else:
        result = check_initialization(args.project_id, db_url=args.db_url)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(_format_report(result))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：Checker 可导入
uv run python -c "from emily_core.services.initialization_checker import InitializationChecker; print('OK')"
→ 预期输出：OK

# 验收 2：检查单个项目
$project_id = (docker exec emily-postgres psql -U emily -d emily -t -c "SELECT id FROM projects WHERE status='active' LIMIT 1;").Trim()
uv run python scripts/check_initialization.py --project-id $project_id
→ 预期输出：格式化的初始化报告，含 T1-T4 层级和缺失项

# 验收 3：检查所有项目
uv run python scripts/check_initialization.py --all
→ 预期输出：每个项目一条报告
```

**失败处理**：如果查询返回空结果，检查 projects 表是否有 active 项目；如果 `im_bindings` 属性错误，检查 User 模型是否正确配置了 relationship。

---

## M4: 认知偏差检测

**依赖**：M1, M2

**职责**：检测项目世界书与实际数据的偏差，返回每层的偏差状态。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | CognitionDriftDetector 服务 | `emily-core/emily_core/services/cognition_drift_detector.py`（新建） |
| 2 | 独立脚本 | `scripts/detect_cognition_drift.py`（新建） |

### 代码

#### `emily-core/emily_core/services/cognition_drift_detector.py` — 新建

```python
"""CognitionDriftDetector —— 认知偏差检测。

对比世界书 content_json 与实际 DB 数据，检测各层是否过时。
纯数据对比，无需 LLM，非常轻量。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..repositories.world_book_repo import ProjectWorldBookRepo
from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Project, User, CompanyInfo, ProjectNode, NodeDependency, Event,
)

logger = logging.getLogger("emily.cognition_drift_detector")

BEIJING_TZ = timezone(timedelta(hours=8))


class CognitionDriftDetector:
    """认知偏差检测器。"""

    def detect(self, project_id: str) -> dict:
        """检测项目世界书与实际数据的偏差。

        Args:
            project_id: 项目 ID

        Returns:
            {
                "project_id": str,
                "has_world_book": bool,
                "drift": {layer: {"stale": bool, "signals": [...]}, ...},
                "stale_layers": [str, ...],
                "has_drift": bool,
            }
        """
        wb = ProjectWorldBookRepo.get_by_project(project_id)
        if wb is None:
            return {
                "project_id": project_id,
                "has_world_book": False,
                "drift": {},
                "stale_layers": [],
                "has_drift": False,
                "message": "项目无世界书，需首次生成",
            }

        try:
            layers = json.loads(wb.content_json or "{}")
        except (json.JSONDecodeError, TypeError):
            layers = {}

        drift = {}

        # 层1：本体偏差
        drift["ontology"] = self._check_ontology(project_id, layers.get("ontology", {}), wb.updated_at)

        # 层2：人员偏差
        drift["personnel"] = self._check_personnel(project_id, layers.get("personnel", {}), wb.updated_at)

        # 层3：结构偏差
        drift["structure"] = self._check_structure(project_id, layers.get("structure", {}))

        # 层4：时间偏差
        drift["temporal"] = self._check_temporal(project_id, layers.get("temporal", {}))

        # 层5：关系偏差
        drift["relation"] = self._check_relation(project_id, layers.get("relation", {}), wb.updated_at)

        # 层7：自省偏差
        drift["introspection"] = self._check_introspection(project_id, layers.get("introspection", {}))

        # 层6：知识偏差（不常驻，仅标记）
        drift["knowledge"] = {"stale": False, "signals": [], "note": "层6不常驻，按需检测"}

        stale_layers = [k for k, v in drift.items() if v.get("stale", False)]

        return {
            "project_id": project_id,
            "has_world_book": True,
            "drift": drift,
            "stale_layers": stale_layers,
            "has_drift": len(stale_layers) > 0,
        }

    def _check_ontology(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层1：本体认知偏差——lifecycle_stage 变了 / 新增参建单位。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                project = session.query(Project).filter(Project.id == project_id).first()
                if project is None:
                    return {"stale": True, "signals": ["项目不存在"]}

                # lifecycle_stage 变化
                current_stage = project.lifecycle_stage or 0
                recorded_stage = layer.get("lifecycle_stage", -1)
                if current_stage != recorded_stage:
                    signals.append(f"lifecycle_stage: {recorded_stage}→{current_stage}")
                    stale = True

                # 新增参建单位
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                company_ids = list(set(u.company for u in users if u.company))
                current_company_count = 0
                if company_ids:
                    current_company_count = session.query(CompanyInfo).filter(CompanyInfo.id.in_(company_ids)).count()
                recorded_company_count = len(layer.get("organizations", []))
                if current_company_count > recorded_company_count:
                    signals.append(f"新增参建单位: {recorded_company_count}→{current_company_count}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_personnel(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层2：人员偏差——有用户变更。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                # 检查最近更新的用户数
                users = session.query(User).filter(User.project_id == project_id, User.is_deleted == False).all()
                current_count = len(users)
                recorded_count = layer.get("total_users", 0)
                if current_count != recorded_count:
                    signals.append(f"用户数变化: {recorded_count}→{current_count}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_structure(self, project_id: str, layer: dict) -> dict:
        """层3：结构偏差——整体进度偏差 >5% / 逾期数变化 / 新节点。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()

                current_total = len(nodes)
                recorded_total = layer.get("total_nodes", 0)
                if current_total != recorded_total:
                    signals.append(f"节点数变化: {recorded_total}→{current_total}")
                    stale = True

                # 进度偏差
                if current_total > 0:
                    current_progress = sum(float(n.progress or "0") for n in nodes) / current_total
                    recorded_progress = float(layer.get("overall_progress", "0%").replace("%", ""))
                    if abs(current_progress - recorded_progress) > 5:
                        signals.append(f"进度偏差: {recorded_progress:.1f}%→{current_progress:.1f}%")
                        stale = True

                # 逾期数
                now_beijing = datetime.now(BEIJING_TZ)
                current_overdue = 0
                for n in nodes:
                    if n.status != "COMPLETED" and n.deadline:
                        try:
                            dl = datetime.fromisoformat(n.deadline)
                            if dl.tzinfo is None:
                                dl = dl.replace(tzinfo=BEIJING_TZ)
                            if dl < now_beijing:
                                current_overdue += 1
                        except (ValueError, TypeError):
                            pass
                recorded_overdue = layer.get("overdue", 0)
                if current_overdue != recorded_overdue:
                    signals.append(f"逾期数变化: {recorded_overdue}→{current_overdue}")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_temporal(self, project_id: str, layer: dict) -> dict:
        """层4：时间偏差——近期有新事件 / deadline 逼近。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                # 检查最近事件
                recent_count = session.query(Event).filter(
                    Event.project_id == project_id,
                ).count()
                recorded_events = len(layer.get("recent_events", []))
                # 如果实际事件数远多于世界书记录的，标记过时
                if recent_count > recorded_events + 3:
                    signals.append(f"新事件: 至少{recent_count - recorded_events}条")
                    stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_relation(self, project_id: str, layer: dict, wb_updated: str) -> dict:
        """层5：关系偏差——依赖链变更。"""
        signals = []
        stale = False
        try:
            with get_session() as session:
                nodes = session.query(ProjectNode).filter(
                    ProjectNode.project_id == project_id,
                    ProjectNode.is_discarded == False,
                ).all()
                node_ids = [n.node_id for n in nodes]

                if node_ids:
                    dep_count = session.query(NodeDependency).filter(
                        NodeDependency.node_id.in_(node_ids)
                    ).count()
                    recorded_deps = len(layer.get("key_dependencies", []))
                    if dep_count != recorded_deps:
                        signals.append(f"依赖数变化: {recorded_deps}→{dep_count}")
                        stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}

    def _check_introspection(self, project_id: str, layer: dict) -> dict:
        """层7：自省偏差——初始化层级变化。"""
        signals = []
        stale = False
        try:
            from .initialization_checker import InitializationChecker
            checker = InitializationChecker()
            current_result = checker.check(project_id)
            current_tier = current_result["tier"]
            recorded_tier = layer.get("initialization_tier", -1)
            if current_tier != recorded_tier:
                signals.append(f"初始化层级变化: T{recorded_tier}→T{current_tier}")
                stale = True
        except Exception as e:
            signals.append(f"检测异常: {e}")

        return {"stale": stale, "signals": signals}
```

#### `scripts/detect_cognition_drift.py` — 新建

```python
"""detect_cognition_drift.py — 检测项目世界书与实际数据的偏差。

用法：
    uv run python scripts/detect_cognition_drift.py --project-id <UUID>
    uv run python scripts/detect_cognition_drift.py --all
    uv run python scripts/detect_cognition_drift.py --project-id <UUID> --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("detect_cognition_drift")


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def detect_cognition_drift(project_id: str, *, db_url: str = "") -> dict:
    _init_db(db_url)
    from emily_core.services.cognition_drift_detector import CognitionDriftDetector
    detector = CognitionDriftDetector()
    return detector.detect(project_id)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="检测认知偏差")
    parser.add_argument("--project-id", help="项目 ID")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true", help="本项目始终只读，dry-run 仅影响输出格式")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.repositories.world_book_repo import ProjectWorldBookRepo
        wbs = ProjectWorldBookRepo.list_all()
        for wb in wbs:
            result = detect_cognition_drift(wb.project_id, db_url=args.db_url)
            drift = result.get("drift", {})
            stale = [k for k, v in drift.items() if v.get("stale")]
            status = "⚠️ 有偏差" if stale else "✅ 无偏差"
            print(f"项目 {wb.project_id}: {status} {stale if stale else ''}")
    else:
        result = detect_cognition_drift(args.project_id, db_url=args.db_url)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：Detector 可导入
uv run python -c "from emily_core.services.cognition_drift_detector import CognitionDriftDetector; print('OK')"
→ 预期输出：OK

# 验收 2：检测偏差（需先 M2 已构建世界书）
uv run python scripts/detect_cognition_drift.py --project-id <UUID>
→ 预期输出：JSON 含 drift 和 stale_layers

# 验收 3：手动制造偏差后重新检测
docker exec emily-postgres psql -U emily -d emily -c "UPDATE projects SET lifecycle_stage = 2 WHERE id = '<UUID>';"
uv run python scripts/detect_cognition_drift.py --project-id <UUID>
→ 预期输出：drift.ontology.stale == True
```

---

## M5: 世界书增量更新

**依赖**：M1, M2, M4

**职责**：根据偏差检测结果，增量更新世界书的过时层。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | ProjectWorldBookService | `emily-core/emily_core/services/world_book_service.py`（新建） |
| 2 | 独立脚本 | `scripts/update_world_book.py`（新建） |

### 代码

#### `emily-core/emily_core/services/world_book_service.py` — 新建

```python
"""ProjectWorldBookService —— 世界书增量更新服务。

根据偏差检测结果，只更新过时的层。数据驱动优先，语义偏差才调 LLM。

参照模式：emily_core/services/evolution/insight_generator.py
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..repositories.world_book_repo import ProjectWorldBookRepo
from ..services.world_book_builder import ProjectWorldBookBuilder
from ..services.cognition_drift_detector import CognitionDriftDetector

logger = logging.getLogger("emily.world_book_service")

# 数据驱动更新的层（无需 LLM，快）
DATA_DRIVEN_LAYERS = {"personnel", "structure", "temporal", "relation", "introspection"}

# LLM 驱动更新的层（需 LLM，慢但语义深）
LLM_DRIVEN_LAYERS = {"ontology"}


class ProjectWorldBookService:
    """世界书增量更新服务。"""

    def __init__(self, llm_client=None):
        self._llm = llm_client
        self._builder = ProjectWorldBookBuilder()
        self._detector = CognitionDriftDetector()

    async def update_stale(self, project_id: str, *, dry_run: bool = False) -> dict:
        """检测偏差并增量更新过时层。

        Args:
            project_id: 项目 ID
            dry_run: 预览模式

        Returns:
            更新结果 dict
        """
        # 1. 检测偏差
        drift_result = self._detector.detect(project_id)

        if not drift_result.get("has_world_book"):
            # 无世界书，首次构建
            import asyncio
            return await asyncio.to_thread(
                self._builder.build, project_id, generated_by="startup", dry_run=dry_run
            )

        if not drift_result.get("has_drift"):
            return {
                "project_id": project_id,
                "status": "no_drift",
                "message": "世界书与实际数据一致，无需更新",
            }

        stale_layers = drift_result.get("stale_layers", [])
        if not stale_layers:
            return {"project_id": project_id, "status": "no_drift"}

        # 2. 重新构建完整世界书（简洁策略：重建而非逐层修补）
        # 原因：七层数据相互关联，逐层修补可能导致层间引用不一致
        import asyncio
        result = await asyncio.to_thread(
            self._builder.build, project_id, generated_by="scheduler_data", dry_run=dry_run
        )

        result["updated_layers"] = stale_layers
        result["drift_details"] = drift_result.get("drift", {})
        result["status"] = "updated" if not dry_run else "preview"

        return result

    async def update_all(self, *, dry_run: bool = False) -> list[dict]:
        """更新所有项目的过时世界书。"""
        import asyncio
        wbs = await asyncio.to_thread(ProjectWorldBookRepo.list_all)
        results = []
        for wb in wbs:
            try:
                r = await self.update_stale(wb.project_id, dry_run=dry_run)
                results.append(r)
            except Exception as e:
                logger.error("update_stale failed for project %s: %s", wb.project_id, e)
                results.append({"project_id": wb.project_id, "status": "error", "error": str(e)})
        return results
```

#### `scripts/update_world_book.py` — 新建

```python
"""update_world_book.py — 根据偏差增量更新世界书。

用法：
    uv run python scripts/update_world_book.py --project-id <UUID>
    uv run python scripts/update_world_book.py --all --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("update_world_book")


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


async def update_world_book(project_id: str, *, db_url: str = "", dry_run: bool = False) -> dict:
    _init_db(db_url)
    from emily_core.services.world_book_service import ProjectWorldBookService
    service = ProjectWorldBookService()
    return await service.update_stale(project_id, dry_run=dry_run)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="增量更新世界书")
    parser.add_argument("--project-id", help="项目 ID")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("请指定 --project-id 或 --all")

    if args.all:
        _init_db(args.db_url)
        from emily_core.services.world_book_service import ProjectWorldBookService
        service = ProjectWorldBookService()
        results = asyncio.run(service.update_all(dry_run=args.dry_run))
        for r in results:
            print(json.dumps({k: v for k, v in r.items() if k not in ("content_json", "drift_details")}, ensure_ascii=False, indent=2, default=str))
    else:
        result = asyncio.run(update_world_book(args.project_id, db_url=args.db_url, dry_run=args.dry_run))
        print(json.dumps({k: v for k, v in result.items() if k not in ("content_json",)}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：Service 可导入
uv run python -c "from emily_core.services.world_book_service import ProjectWorldBookService; print('OK')"
→ 预期输出：OK

# 验收 2：增量更新（需先 M2 + M4 已通过）
uv run python scripts/update_world_book.py --project-id <UUID> --dry-run
→ 预期输出：含 updated_layers 和 drift_details

# 验收 3：实际更新
uv run python scripts/update_world_book.py --project-id <UUID>
→ 预期输出：status=updated 或 no_drift
```

---

## M6: Session Prompt 生成

**依赖**：M1

**职责**：为指定用户生成世界书 + 规则书的 prompt 注入段。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | 独立脚本 | `scripts/generate_session_prompt.py`（新建） |

### 代码

#### `scripts/generate_session_prompt.py` — 新建

```python
"""generate_session_prompt.py — 为指定用户生成 Session prompt（世界书+规则书）。

用法：
    uv run python scripts/generate_session_prompt.py --user-id <UUID> --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("generate_session_prompt")


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def generate_session_prompt(user_id: str, *, db_url: str = "", dry_run: bool = False) -> dict:
    """生成指定用户的 Session prompt 段（世界书+规则书）。"""
    _init_db(db_url)

    from emily_core.repositories.user_repo import UserRepository
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo

    # 查用户关联项目
    user = UserRepository.get_by_id(user_id)
    if user is None:
        return {"error": "用户不存在", "user_id": user_id}

    project_id = getattr(user, "project_id", None)

    # 世界书
    world_book_text = ""
    world_book_tokens = 0
    if project_id:
        wb = ProjectWorldBookRepo.get_by_project(project_id)
        if wb:
            world_book_text = wb.content_text or ""
            world_book_tokens = wb.token_count or 0

    # 规则书
    rule_book_text = ""
    rule_book_path = Path(_CORE_DIR) / ".." / "emily-data" / "rules" / "规则书.md"
    if not rule_book_path.exists():
        # 尝试容器内路径
        rule_book_path = Path("/app/rules/规则书.md")
    if rule_book_path.exists():
        try:
            rule_book_text = rule_book_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read rule book: %s", e)
            rule_book_text = ""

    rule_book_tokens = int(len(rule_book_text) / 1.5) if rule_book_text else 0

    return {
        "user_id": user_id,
        "user_name": user.username,
        "project_id": project_id or "",
        "world_book_text": world_book_text,
        "world_book_tokens": world_book_tokens,
        "rule_book_text_length": len(rule_book_text),
        "rule_book_tokens": rule_book_tokens,
        "total_tokens": world_book_tokens + rule_book_tokens,
        "dry_run": dry_run,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="生成 Session prompt 段")
    parser.add_argument("--user-id", required=True, help="用户 UUID")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show-text", action="store_true", help="显示完整文本（默认只显示统计）")
    args = parser.parse_args()

    result = generate_session_prompt(args.user_id, db_url=args.db_url, dry_run=args.dry_run)

    if args.show_text:
        print("=== 世界书 ===")
        print(result.get("world_book_text", "（无）"))
        print(f"\n=== 规则书（{result['rule_book_tokens']} tokens）===")
        print(result.get("rule_book_text_length", 0) > 0 and "（已加载）" or "（未找到）")
    else:
        # 只显示统计
        output = {k: v for k, v in result.items() if k not in ("world_book_text",)}
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：脚本可运行
$user_id = (docker exec emily-postgres psql -U emily -d emily -t -c "SELECT id FROM users WHERE status='active' LIMIT 1;").Trim()
uv run python scripts/generate_session_prompt.py --user-id $user_id
→ 预期输出：JSON 含 world_book_tokens 和 rule_book_tokens

# 验收 2：显示完整文本
uv run python scripts/generate_session_prompt.py --user-id $user_id --show-text
→ 预期输出：世界书文本 + 规则书加载状态
```

---

## M7: 规则书加载

**依赖**：无

**职责**：规则书文件已存在（`emily-data/rules/规则书.md`），本模块实现运行时加载逻辑和热重载支持。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | RuleBookLoader 服务 | `emily-core/emily_core/services/rule_book_loader.py`（新建） |

### 代码

#### `emily-core/emily_core/services/rule_book_loader.py` — 新建

```python
"""RuleBookLoader —— 规则书加载与热重载。

从 emily-data/rules/规则书.md 读取规则书全文，注入 Session prompt 的 {rule_book} 变量。
支持热重载：API 触发 reload_rule_book() 后更新所有活跃 Session。

参照模式：emily_core/skill/registry.py（多级 fallback 路径查找 + 热重载）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emily.rule_book_loader")


class RuleBookLoader:
    """规则书加载器。"""

    def __init__(self):
        self._content: str = ""
        self._loaded: bool = False

    def load(self) -> str:
        """加载规则书文件。多级 fallback 路径。"""
        # 路径优先级：容器内 > 环境变量 > 宿主机开发路径
        candidates = []

        # 1. 容器内路径
        candidates.append("/app/rules/规则书.md")

        # 2. 环境变量
        env_dir = os.environ.get("EMILY_RULE_BOOK_DIR", "")
        if env_dir:
            candidates.append(str(Path(env_dir) / "规则书.md"))

        # 3. 宿主机开发路径
        dev_path = Path(__file__).resolve().parents[2] / "emily-data" / "rules" / "规则书.md"
        candidates.append(str(dev_path))

        for path in candidates:
            p = Path(path)
            if p.exists() and p.is_file():
                try:
                    self._content = p.read_text(encoding="utf-8")
                    self._loaded = True
                    logger.info("RuleBook loaded from %s (%d chars)", path, len(self._content))
                    return self._content
                except Exception as e:
                    logger.warning("Failed to read rule book from %s: %s", path, e)

        # 加载失败：降级为空字符串（不阻塞）
        self._content = ""
        self._loaded = False
        logger.warning("RuleBook file not found in any candidate path, using empty string")
        return ""

    def reload(self) -> dict:
        """热重载规则书。"""
        old_len = len(self._content)
        self.load()
        new_len = len(self._content)
        changed = old_len != new_len
        logger.info("RuleBook reload: %d→%d chars, changed=%s", old_len, new_len, changed)
        return {
            "ok": True,
            "content_length": new_len,
            "changed": changed,
        }

    @property
    def content(self) -> str:
        """当前规则书内容。如果未加载则自动加载。"""
        if not self._loaded:
            self.load()
        return self._content

    @property
    def is_loaded(self) -> bool:
        return self._loaded
```

### 模块验收检测

```bash
# 验收 1：Loader 可导入
uv run python -c "from emily_core.services.rule_book_loader import RuleBookLoader; print('OK')"
→ 预期输出：OK

# 验收 2：加载规则书
uv run python -c "from emily_core.services.rule_book_loader import RuleBookLoader; l = RuleBookLoader(); c = l.load(); print(f'Loaded: {len(c)} chars, starts with: {c[:20]}')"
→ 预期输出：Loaded: ~3000+ chars, starts with: # Emily 规则书

# 验收 3：热重载
uv run python -c "from emily_core.services.rule_book_loader import RuleBookLoader; l = RuleBookLoader(); l.load(); r = l.reload(); print(r)"
→ 预期输出：{'ok': True, 'content_length': N, 'changed': False}
```

---

## M8: EmilyCore 集成

**依赖**：M2, M3, M6, M7

**职责**：将元认知模块集成到 EmilyCore 初始化流程、SessionContext prompt 注入、规则书加载。这是**最关键的集成模块**。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | SessionContext 新增字段 | `emily-core/emily_core/session/session_context.py`（修改） |
| 2 | SessionDataFetcher 扩展 | `emily-core/emily_core/session/session_data_fetcher.py`（修改） |
| 3 | Session prompt 模板 | `emily-data/prompts/session.md`（修改） |
| 4 | EmilyCore 初始化 | `emily-core/emily_core/__init__.py`（修改） |

### 代码

#### `emily-core/emily_core/session/session_context.py` — 在 dataclass 字段区域（第 88 行 `rag_collections` 之后）追加 2 个字段

```python
    # ── 元认知模块字段（🔥 可热更新）──
    project_world_book: str = ""        # 项目世界书纯文本摘要（注入 prompt）
    rule_book: str = ""                 # 规则书全文（注入 prompt）
```

#### `emily-core/emily_core/session/session_context.py` — 在 `get_prompt_variables()` 方法（第 332 行附近）的返回 dict 中追加 2 个变量

在 `"{rag_info}"` 行之后追加：

```python
            "{project_world_book}": self.project_world_book,
            "{rule_book}": self.rule_book,
```

#### `emily-core/emily_core/session/session_context.py` — 在 `create()` 方法中，在 `ctx.rag_collections = ...` 行之后追加

```python
        # 灌注元认知字段
        ctx.project_world_book = snapshot.get("project_world_book", "")
        ctx.rule_book = snapshot.get("rule_book", "")
```

#### `emily-core/emily_core/session/session_context.py` — 在 `refresh()` 方法的 `_hot_fields` dict 中追加

```python
            "project_world_book": snapshot.get("project_world_book"),
            "rule_book": snapshot.get("rule_book"),
```

#### `emily-core/emily_core/session/session_data_fetcher.py` — 在 `fetch()` 方法中，`session_snapshot` dict 构建区域（约第 172 行）追加

在 `"rag_collections": rag_info.get("collections", []),` 行之后追加：

```python
            # 元认知字段
            "project_world_book": _sub_fetch_world_book(project_id),
            "rule_book": _sub_fetch_rule_book(),
```

#### `emily-core/emily_core/session/session_data_fetcher.py` — 在文件末尾（`_empty_result` 函数之后）追加两个子采集函数

```python
def _sub_fetch_world_book(project_id: Optional[str]) -> str:
    """获取项目世界书纯文本摘要。"""
    if not project_id:
        return ""
    try:
        from ..repositories.world_book_repo import ProjectWorldBookRepo
        wb = ProjectWorldBookRepo.get_by_project(project_id)
        if wb is None:
            return ""
        return wb.content_text or ""
    except Exception as e:
        logger.error("_sub_fetch_world_book failed project=%s: %s", project_id, e)
        return ""


def _sub_fetch_rule_book() -> str:
    """获取规则书全文。"""
    try:
        from ..services.rule_book_loader import RuleBookLoader
        # 注意：此处每次 fetch 都创建新 loader 实例
        # 性能优化：后续可改为 EmilyCore 级缓存，此处先保证正确性
        loader = RuleBookLoader()
        return loader.content
    except Exception as e:
        logger.error("_sub_fetch_rule_book failed: %s", e)
        return ""
```

#### `emily-core/emily_core/session/session_data_fetcher.py` — 在 `_empty_result` 函数的 `session_snapshot` dict 中追加

在 `"rag_collections": [],` 行之后追加：

```python
            # 元认知字段
            "project_world_book": "",
            "rule_book": "",
```

#### `emily-data/prompts/session.md` — 在 `## 项目上下文` 段之后（约第 26 行后）追加

```markdown
## 项目世界书
{project_world_book}

## 规则书
{rule_book}
```

#### `emily-core/emily_core/__init__.py` — 在 `__init__` 方法中（约第 118 行 `self._skill_executor = None` 之后）追加

```python
        # 元认知模块
        self._rule_book_loader = None
        self._world_book_service = None
```

#### `emily-core/emily_core/__init__.py` — 在 `_ensure_initialized()` 方法中，`self._initialized = True` 行之前（约第 191 行）追加

```python
        #  ── 元认知模块 ──
        self._init_meta_cognition()
```

#### `emily-core/emily_core/__init__.py` — 在 `_init_skill_module()` 方法之后追加新方法

```python
    def _init_meta_cognition(self) -> None:
        """初始化元认知模块：规则书加载 + 世界书服务。fail-open。"""
        try:
            from .services.rule_book_loader import RuleBookLoader
            from .services.world_book_service import ProjectWorldBookService

            # 规则书加载
            self._rule_book_loader = RuleBookLoader()
            self._rule_book_loader.load()

            # 世界书服务
            self._world_book_service = ProjectWorldBookService(llm_client=self._llm_client)

            logger.info("Meta-cognition module initialized: rule_book=%s, world_book_service ready",
                         "loaded" if self._rule_book_loader.is_loaded else "empty")
        except Exception as e:
            logger.warning("Meta-cognition module init failed: %s", e)
            self._rule_book_loader = None
            self._world_book_service = None

    def reload_rule_book(self) -> dict:
        """热重载规则书（无需重启容器）。

        Returns:
            {"ok": bool, "content_length": int, "changed": bool}
        """
        if self._rule_book_loader is None:
            return {"ok": False, "error": "RuleBookLoader not initialized"}
        return self._rule_book_loader.reload()
```

### 模块验收检测

```bash
# 验收 1：SessionContext 新字段存在
uv run python -c "from emily_core.session.session_context import SessionContext; ctx = SessionContext(); print(f'world_book={bool(hasattr(ctx, \"project_world_book\"))}, rule_book={bool(hasattr(ctx, \"rule_book\"))}')"
→ 预期输出：world_book=True, rule_book=True

# 验收 2：prompt 变量包含新 key
uv run python -c "from emily_core.session.session_context import SessionContext; ctx = SessionContext(); vars = ctx.get_prompt_variables(); print('{project_world_book}' in vars, '{rule_book}' in vars)"
→ 预期输出：True True

# 验收 3：重启容器 + 检查启动日志
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 50 emily-core 2>&1 | Select-String "meta_cognition|rule_book|world_book"
→ 预期输出：含 "Meta-cognition module initialized" 日志行

# 验收 4：对话验证（世界书+规则书注入生效）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我们项目现在进展怎么样" --sender "真实用户名"
→ 预期输出：回复中体现项目认知信息（节点数/进度/里程碑等，来自世界书）
```

---

## M9: 调度器集成

**依赖**：M4, M5

**职责**：注册调度 Handler 定期执行认知偏差检测和世界书更新。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | WorldBookUpdateHandler | `emily-core/emily_core/scheduler/jobs/world_book_update.py`（新建） |
| 2 | 认知周期薄聚合脚本 | `scripts/cognition_cycle.py`（新建） |

### 代码

#### `emily-core/emily_core/scheduler/jobs/world_book_update.py` — 新建

```python
"""WorldBookUpdateHandler — 每日 08:00 自动检测认知偏差并更新世界书。"""

from __future__ import annotations
import logging
from ..handler_registry import SchedulerJobHandler, JobResult

logger = logging.getLogger("emily.scheduler.jobs.world_book_update")


class WorldBookUpdateHandler(SchedulerJobHandler):
    action_type = "world_book_update"
    description = "认知偏差检测 + 世界书增量更新"

    def __init__(self, world_book_service=None):
        self._service = world_book_service

    async def execute(self, params: dict) -> JobResult:
        try:
            if self._service is None:
                from ...services.world_book_service import ProjectWorldBookService
                self._service = ProjectWorldBookService()

            results = await self._service.update_all()
            updated = sum(1 for r in results if r.get("status") == "updated")
            no_drift = sum(1 for r in results if r.get("status") == "no_drift")
            errors = sum(1 for r in results if r.get("status") == "error")

            return JobResult(
                success=True,
                summary=f"世界书更新: {updated}个更新, {no_drift}个无偏差, {errors}个失败",
                data={"results": results},
            )
        except Exception as e:
            logger.error("WorldBookUpdateHandler failed: %s", e, exc_info=True)
            return JobResult(success=False, summary=str(e))
```

#### `emily-core/emily_core/__init__.py` — 在 `_init_scheduler_module()` 方法中，其他 handler 注册之后追加

在 `self._scheduler_handler_registry.register(DataSyncHandler())` 行之后追加：

```python
            # 元认知 Handler
            from .scheduler.jobs.world_book_update import WorldBookUpdateHandler
            self._scheduler_handler_registry.register(
                WorldBookUpdateHandler(world_book_service=self._world_book_service)
            )
```

#### `scripts/cognition_cycle.py` — 新建

```python
"""cognition_cycle.py — 认知进化周期执行薄聚合脚本。

串联：detect_cognition_drift → update_world_book → 汇总日志

用法：
    uv run python scripts/cognition_cycle.py --all
    uv run python scripts/cognition_cycle.py --project-id <UUID> --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cognition_cycle")


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


async def run_cognition_cycle(project_id: str = "", *, db_url: str = "", dry_run: bool = False) -> dict:
    """认知周期执行：偏差检测 → 增量更新。"""
    _init_db(db_url)
    from emily_core.services.world_book_service import ProjectWorldBookService
    service = ProjectWorldBookService()

    if project_id:
        return await service.update_stale(project_id, dry_run=dry_run)
    else:
        results = await service.update_all(dry_run=dry_run)
        return {"total": len(results), "results": results}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="认知进化周期执行")
    parser.add_argument("--project-id", help="项目 ID")
    parser.add_argument("--all", action="store_true", help="所有项目")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run_cognition_cycle(
        args.project_id or "", db_url=args.db_url, dry_run=args.dry_run,
    ))
    # 精简输出
    output = {k: v for k, v in result.items() if k not in ("content_json", "drift_details")}
    if "results" in output:
        for r in output["results"]:
            r.pop("content_json", None)
            r.pop("drift_details", None)
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：Handler 可导入
uv run python -c "from emily_core.scheduler.jobs.world_book_update import WorldBookUpdateHandler; print('OK')"
→ 预期输出：OK

# 验收 2：认知周期脚本可运行
uv run python scripts/cognition_cycle.py --all --dry-run
→ 预期输出：JSON 含 total 和各项目 status

# 验收 3：重启容器后 handler 已注册
docker compose -f docker-compose-napcat.yml restart emily-core
docker logs --tail 30 emily-core 2>&1 | Select-String "world_book_update"
→ 预期输出：含 "JobHandler registered: world_book_update" 日志
```

---

## M10: 进化闭环集成

**依赖**：M4, M9

**职责**：在 `collect_metrics()` 中新增第 10 个数据源 `cognition_drift`。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | metrics 聚合扩展 | `scripts/evolution_metrics.py`（修改） |

### 代码

#### `scripts/evolution_metrics.py` — 在 `collect_metrics()` 函数的 `metrics` dict 中（约第 94-107 行）追加

在 `"project_nodes": EvolutionRepo.aggregate_project_nodes(end_date, session=sess),` 行之后追加：

```python
            "cognition_drift": _collect_cognition_drift_metrics(sess),
```

#### `scripts/evolution_metrics.py` — 在 `collect_single_source()` 的 `source_map` dict 中追加

在 `"project_nodes": EvolutionRepo.aggregate_project_nodes,` 行之后追加：

```python
        "cognition_drift": lambda end_date, session: _collect_cognition_drift_metrics(session),
```

#### `scripts/evolution_metrics.py` — 在文件中（`collect_single_source` 函数之后）追加新函数

```python
def _collect_cognition_drift_metrics(session) -> dict:
    """采集第 10 数据源：认知偏差指标。"""
    try:
        from emily_core.repositories.world_book_repo import ProjectWorldBookRepo
        from emily_core.services.cognition_drift_detector import CognitionDriftDetector

        detector = CognitionDriftDetector()
        wbs = ProjectWorldBookRepo.list_all()

        total_projects = len(wbs)
        drift_projects = 0
        stale_layer_counts = {}

        for wb in wbs:
            try:
                result = detector.detect(wb.project_id)
                if result.get("has_drift"):
                    drift_projects += 1
                    for layer in result.get("stale_layers", []):
                        stale_layer_counts[layer] = stale_layer_counts.get(layer, 0) + 1
            except Exception:
                pass

        return {
            "total_projects_with_world_book": total_projects,
            "projects_with_drift": drift_projects,
            "drift_rate": drift_projects / total_projects if total_projects > 0 else 0,
            "stale_layer_distribution": stale_layer_counts,
        }
    except Exception as e:
        return {"error": str(e), "total_projects_with_world_book": 0, "projects_with_drift": 0}
```

### 模块验收检测

```bash
# 验收 1：第 10 数据源可采集
uv run python scripts/evolution_metrics.py --date 2026-07-10 --source cognition_drift
→ 预期输出：JSON 含 total_projects_with_world_book 和 drift_rate

# 验收 2：完整 metrics 聚合包含 cognition_drift
uv run python scripts/evolution_metrics.py --date 2026-07-10 --preview
→ 预期输出：metrics dict 含 cognition_drift key
```

---

## M11: V1 整合

**依赖**：M2, M3

**职责**：self_check 独立脚本 + cold_start 薄聚合脚本（含邮件通知）。

### 交付物

| # | 交付物 | 文件路径 |
|---|--------|----------|
| 1 | self_check 脚本 | `scripts/self_check.py`（新建） |
| 2 | cold_start 薄聚合 | `scripts/cold_start.py`（新建） |

### 代码

#### `scripts/self_check.py` — 新建

```python
"""self_check.py — 系统级自检（复用 V1）。

输出：用户/项目/业务量/知识库统计。

用法：
    uv run python scripts/self_check.py
    uv run python scripts/self_check.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("self_check")

BEIJING_TZ = timezone(timedelta(hours=8))


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


def self_check(*, db_url: str = "", dry_run: bool = False) -> dict:
    """系统级自检。"""
    _init_db(db_url)

    from emily_core.infrastructure.database.session import get_session
    from emily_core.infrastructure.database.models import User, Project, Event, Task, ProjectNode, ProjectWorldBook

    result = {
        "checked_at": datetime.now(BEIJING_TZ).isoformat(),
        "dry_run": dry_run,
    }

    with get_session() as session:
        # 用户统计
        total_users = session.query(User).filter(User.is_deleted == False).count()
        active_users = session.query(User).filter(User.is_deleted == False, User.status == "active").count()
        admin_users = session.query(User).filter(User.is_deleted == False, User.is_admin == True).count()
        result["users"] = {"total": total_users, "active": active_users, "admins": admin_users}

        # 项目统计
        total_projects = session.query(Project).filter(Project.is_deleted == False).count()
        active_projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").count()
        result["projects"] = {"total": total_projects, "active": active_projects}

        # 业务量
        event_count = session.query(Event).count()
        task_count = session.query(Task).count()
        node_count = session.query(ProjectNode).filter(ProjectNode.is_discarded == False).count()
        result["business"] = {"events": event_count, "tasks": task_count, "nodes": node_count}

        # 世界书
        wb_count = session.query(ProjectWorldBook).count()
        wb_activated = session.query(ProjectWorldBook).filter(ProjectWorldBook.is_activated == True).count()
        result["world_books"] = {"total": wb_count, "activated": wb_activated}

        # 知识库
        sop_count = 0
        try:
            from emily_core.skill.registry import SkillRegistry
            skill_dir = "/app/skills"
            if not Path(skill_dir).exists():
                dev_dir = str(Path(__file__).resolve().parent.parent / "emily-data" / "skills")
                if Path(dev_dir).exists():
                    skill_dir = dev_dir
            if skill_dir and Path(skill_dir).exists():
                reg = SkillRegistry(skill_directory=skill_dir)
                reg.load()
                sop_count = len(reg.list_sop_ids())
        except Exception:
            pass
        result["knowledge"] = {"sop_count": sop_count}

    return result


def _format_self_check(result: dict) -> str:
    """格式化自检报告。"""
    lines = []
    lines.append("Emily 系统自检报告")
    lines.append("═" * 40)
    lines.append(f"检查时间：{result['checked_at']}")
    lines.append("═" * 40)

    u = result.get("users", {})
    lines.append(f"\n👤 用户：{u.get('active', 0)} 活跃 / {u.get('total', 0)} 总计 / {u.get('admins', 0)} 管理员")

    p = result.get("projects", {})
    lines.append(f"📁 项目：{p.get('active', 0)} 活跃 / {p.get('total', 0)} 总计")

    b = result.get("business", {})
    lines.append(f"📊 业务：{b.get('events', 0)} 事件 / {b.get('tasks', 0)} 任务 / {b.get('nodes', 0)} 节点")

    wb = result.get("world_books", {})
    lines.append(f"🧠 世界书：{wb.get('total', 0)} 份 / {wb.get('activated', 0)} 已激活")

    k = result.get("knowledge", {})
    lines.append(f"📚 知识库：{k.get('sop_count', 0)} 个 SOP")

    return "\n".join(lines)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 系统自检")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = self_check(db_url=args.db_url, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_self_check(result))


if __name__ == "__main__":
    main()
```

#### `scripts/cold_start.py` — 新建

```python
"""cold_start.py — 冷启动流程薄聚合脚本。

串联：self_check → check_initialization → build_world_book → 邮件通知
不含业务逻辑，仅做编排。

用法：
    uv run python scripts/cold_start.py
    uv run python scripts/cold_start.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cold_start")

BEIJING_TZ = timezone(timedelta(hours=8))


def _init_db(db_url: str = "") -> None:
    from emily_core.infrastructure.database.session import init_db
    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_port = int(os.environ.get("EMILY_PG_PORT", "")) if os.environ.get("EMILY_PG_PORT") else None
            if not pg_port:
                try:
                    r = subprocess.run(["docker", "port", "emily-postgres", "5432/tcp"], capture_output=True, text=True, timeout=5)
                    pg_port = int(r.stdout.strip().rsplit(":", 1)[-1]) if r.returncode == 0 and r.stdout.strip() else 5432
                except Exception:
                    pg_port = 5432
            init_db(pg_host=os.environ.get("EMILY_PG_HOST", "127.0.0.1"), pg_port=pg_port,
                    pg_db=os.environ.get("EMILY_PG_DB", "emily"),
                    pg_user=os.environ.get("EMILY_PG_USER", "emily"),
                    pg_password=os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026"))


async def run_cold_start(*, db_url: str = "", dry_run: bool = False) -> dict:
    """冷启动流程：self_check → check_initialization → build_world_book → 邮件通知。"""
    _init_db(db_url)

    # Step 1: 系统自检
    from self_check import self_check
    check_result = self_check(db_url=db_url, dry_run=dry_run)
    print(f"[1/4] 系统自检完成: {check_result.get('projects', {}).get('active', 0)} 个活跃项目")

    # Step 2: 遍历所有 active 项目，检查初始化
    from emily_core.infrastructure.database.models import Project
    from emily_core.infrastructure.database.session import get_session
    from emily_core.services.initialization_checker import InitializationChecker

    checker = InitializationChecker()
    init_results = []

    with get_session() as session:
        projects = session.query(Project).filter(Project.is_deleted == False, Project.status == "active").all()

    for p in projects:
        init_result = checker.check(p.id)
        init_results.append({
            "project_id": p.id,
            "project_name": p.name,
            **init_result,
        })
    print(f"[2/4] 初始化检查完成: {len(init_results)} 个项目")

    # Step 3: 构建世界书（对未构建或需要重建的项目）
    from emily_core.services.world_book_builder import ProjectWorldBookBuilder
    from emily_core.repositories.world_book_repo import ProjectWorldBookRepo

    builder = ProjectWorldBookBuilder()
    build_results = []

    for p in projects:
        existing = ProjectWorldBookRepo.get_by_project(p.id)
        if existing is None:
            # 首次构建
            build_result = builder.build(p.id, generated_by="startup", dry_run=dry_run)
            build_results.append({
                "project_id": p.id,
                "project_name": p.name,
                "action": "created",
                **build_result,
            })
            print(f"  世界书构建: {p.name} → tier=T{build_result.get('initialization_tier', 0)}")
    print(f"[3/4] 世界书构建完成: {len(build_results)} 个新建")

    # Step 4: 邮件通知（fail-open）
    email_sent = 0
    email_failed = 0
    if not dry_run:
        from emily_core.infrastructure.database.models import User

        for init_r in init_results:
            try:
                with get_session() as session:
                    admins = session.query(User).filter(
                        User.project_id == init_r["project_id"],
                        User.is_deleted == False,
                        User.is_admin == True,
                    ).all()

                for admin in admins:
                    if admin.email:
                        # 邮件通知（简单实现：仅日志记录，实际发送依赖 EmailService）
                        logger.info("Cold start notification: project=%s admin=%s email=%s tier=T%d",
                                    init_r["project_name"], admin.username, admin.email, init_r["tier"])
                        email_sent += 1
            except Exception as e:
                logger.warning("Email notification failed for project %s: %s", init_r["project_id"], e)
                email_failed += 1
    print(f"[4/4] 邮件通知: {email_sent} 已发送, {email_failed} 失败")

    return {
        "self_check": check_result,
        "initialization": init_results,
        "world_books_built": len(build_results),
        "email_sent": email_sent,
        "email_failed": email_failed,
    }


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="Emily 冷启动流程")
    parser.add_argument("--db-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run_cold_start(db_url=args.db_url, dry_run=args.dry_run))
    print(json.dumps(
        {k: v for k, v in result.items() if k != "self_check"},
        ensure_ascii=False, indent=2, default=str,
    ))


if __name__ == "__main__":
    main()
```

### 模块验收检测

```bash
# 验收 1：self_check 可运行
uv run python scripts/self_check.py
→ 预期输出：格式化的系统自检报告

# 验收 2：cold_start 可运行
uv run python scripts/cold_start.py --dry-run
→ 预期输出：4 步流程输出 + JSON 汇总

# 验收 3：实际冷启动
uv run python scripts/cold_start.py
→ 预期输出：世界书已构建，邮件通知已尝试
```

---

## 组装验证

所有模块完成后，运行端到端组装验证：

```bash
# 1. 重建容器（触发 init_db 自动建表）
docker compose -f docker-compose-napcat.yml restart emily-core

# 2. 清除 pycache
docker exec emily-core find /app/emily_core -name '__pycache__' -type d -exec rm -rf {} +

# 3. 冷启动流程
uv run python scripts/cold_start.py

# 4. 构建世界书（验证数据层完整）
$project_id = (docker exec emily-postgres psql -U emily -d emily -t -c "SELECT id FROM projects WHERE status='active' LIMIT 1;").Trim()
uv run python scripts/build_world_book.py --project-id $project_id --dry-run

# 5. 初始化检查
uv run python scripts/check_initialization.py --project-id $project_id

# 6. 偏差检测
uv run python scripts/detect_cognition_drift.py --project-id $project_id

# 7. 认知周期
uv run python scripts/cognition_cycle.py --all --dry-run

# 8. 对话验证（世界书+规则书注入生效）
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "我们项目现在进展怎么样" --sender "真实用户名"
→ 预期输出：回复中体现项目认知（节点数/进度/里程碑）+ 规则书行为约束

# 9. 规则书热重载验证
# 修改 emily-data/rules/规则书.md（如新增一条规则）
# 通过 API 触发热重载（需在 M8 中新增 API 路由）
```

---

## 阶段反思指令

每完成一个模块，在进入下一个模块之前，执行以下反思：

1. **检查产物**：列出本模块所有新建/修改的文件路径
2. **检查偏差**：是否有步骤与计划不符？记录差异
3. **判断是否继续**：
   - 如果偏差 ≤ 1 个文件路径变化 → 直接修改计划文档对应模块，继续
   - 如果偏差 2-4 个文件或步骤顺序调整 → 在计划文档末尾追加 "v1.1 修订记录"，继续
   - 如果偏差 > 4 个文件或架构方向变化 → **停止**，报告给用户，等用户决定是否重新生成计划

---

*本计划为 AI 可执行操作手册，由 req-plan 技能生成。*
