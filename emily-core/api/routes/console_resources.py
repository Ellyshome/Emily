"""emy-console 控制台 API —— 文件 / 节点 / SOP / RAG / 日志 / 自检等端点。

按用户过滤可见范围的端点统一经 `_build_visible_sets` 解析：
  - files     → VisibleFileSetResolver（①自传 ∪ ②公开 ∪ ③节点可见∩密级 ∪ ④显式授权）
  - nodes     → 权限快照 authorized_node_ids
  - sops      → 权限快照 sop_allow
  - rag_files → doc_id ∈ 可见文件集合（doc_id 锚定 files.id）
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger("emily.api.console")

router = APIRouter(prefix="/console", tags=["console"])

# 列表返回上限，防止极端情况下响应过大
_LIST_LIMIT = 2000


def _err(message: str, code: int = 1) -> dict:
    return {"code": code, "message": message, "data": None}


def _ok(data) -> dict:
    return {"code": 0, "message": "ok", "data": data}


def _get_storage_service():
    """获取系统正确的 FileStorageService（storage_root 来自 EMILY_STORAGE_ROOT=/app/attachments）。

    FileStorageService() 无参构造默认 data/files，与容器实际挂载 /app/attachments 不符，
    必须复用 core 里已按 EMILY_STORAGE_ROOT 实例化的 storage。
    """
    from api.server import get_core
    core = get_core()
    fm = getattr(core, "_file_manager", None)
    storage = getattr(fm, "_storage", None) if fm is not None else None
    if storage is not None:
        return storage
    from emily_core.services.file_storage_service import FileStorageService
    return FileStorageService()


def _get_node_service():
    """获取系统正确的 NodeService（复用 core 已注入实例，同源同能力）。"""
    from api.server import get_core

    core = get_core()
    svc = getattr(core, "_node_service", None)
    if svc is not None:
        return svc
    from emily_core.repositories.permission_repo import PermissionRepository
    from emily_core.services.node_service import NodeService
    return NodeService(user_repo=PermissionRepository())


def _get_file_manager():
    """获取系统正确的 FileManager（复用 core 已注入实例，同源同能力）。"""
    from api.server import get_core

    core = get_core()
    return getattr(core, "_file_manager", None)


def _get_knowledge_service(tei=None, repo=None):
    """构造 KnowledgeService（复用 core 的 chunk repo 与 storage，同源同能力）。

    `tei` 由调用方按请求选定后端后传入（与既有 `_build_embedding_client` 一致）。
    """
    from api.server import get_core
    from emily_core.services.knowledge_service import KnowledgeService

    core = get_core()
    return KnowledgeService(
        tei=tei,
        repo=repo if repo is not None else getattr(core, "_knowledge_chunk_repo", None),
        storage=_get_storage_service(),
    )


def _get_self_check_fn():
    """加载 scripts/self_check.py 的 self_check()（复用运维脚本能力，容器/开发两态）。"""
    import importlib.util
    from pathlib import Path

    for base in ("/app", str(Path(__file__).resolve().parents[3])):
        script_path = Path(base) / "scripts" / "self_check.py"
        if script_path.exists():
            spec = importlib.util.spec_from_file_location("emily_self_check", script_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.self_check
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  全量清单（同步查询，由 asyncio.to_thread 包裹）
# ══════════════════════════════════════════════════════════════════════════════

def _list_sops() -> list[dict]:
    from emily_core.infrastructure.database.models import SOPBusinessFlow
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(SOPBusinessFlow)
            .filter(SOPBusinessFlow.is_deleted.isnot(True))
            .order_by(SOPBusinessFlow.sop_id)
            .limit(_LIST_LIMIT)
            .all()
        )
    return [
        {
            "sop_id": r.sop_id,
            "name": r.display_name or r.sop_file_name,
            "file_name": r.sop_file_name,
            "description": r.description or "",
            "category": r.category or "",
            "sop_type": r.sop_type or "",
            "is_active": bool(r.is_active),
            "is_deprecated": bool(r.is_deprecated),
        }
        for r in rows
    ]


def _list_project_events(
    project_id: str = "",
    node_id: str = "",
    kind: str = "",
    limit: int = 200,
) -> list[dict]:
    """统一项目事件时间线（project_events 单表继承）。

    会议 / 事件记录 / 任务 / 文件归档 / 业务流转单 / 节点成果 / 节点事件
    统一归一到 project_events，按 created_at 倒序。支持按项目/节点/类型过滤。
    """
    from emily_core.infrastructure.database.models import (
        Project, ProjectEvent, ProjectEventKind, ProjectNode,
    )
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        q = (
            session.query(
                ProjectEvent.id.label("id"),
                ProjectEvent.event_no.label("event_no"),
                ProjectEvent.event_kind.label("event_kind"),
                ProjectEvent.event_type.label("event_type"),
                ProjectEvent.title.label("title"),
                ProjectEvent.summary.label("summary"),
                ProjectEvent.status.label("status"),
                ProjectEvent.node_id.label("node_id"),
                ProjectEvent.project_id.label("project_id"),
                ProjectEvent.actor_id.label("actor_id"),
                ProjectEvent.occurred_at.label("occurred_at"),
                ProjectEvent.created_at.label("created_at"),
                Project.name.label("project_name"),
                ProjectNode.node_name.label("node_name"),
            )
            .outerjoin(Project, Project.id == ProjectEvent.project_id)
            .outerjoin(ProjectNode, ProjectNode.node_id == ProjectEvent.node_id)
        )
        if project_id:
            q = q.filter(ProjectEvent.project_id == project_id)
        if node_id:
            q = q.filter(ProjectEvent.node_id == node_id)
        if kind:
            q = q.filter(ProjectEvent.event_kind == kind)
        q = q.order_by(ProjectEvent.created_at.desc()).limit(limit)
        rows = q.all()

    return [
        {
            "id": r.id,
            "event_no": r.event_no or "",
            "event_kind": r.event_kind or "",
            "event_kind_display": ProjectEventKind.display(r.event_kind or ""),
            "event_type": r.event_type or "",
            "title": r.title or "",
            "summary": r.summary or "",
            "status": r.status or "",
            "node_id": r.node_id or "",
            "node_name": r.node_name or "",
            "project_id": r.project_id or "",
            "project_name": r.project_name or "",
            "actor_id": r.actor_id or "",
            "occurred_at": r.occurred_at or "",
            "created_at": r.created_at or "",
        }
        for r in rows
    ]


def _list_node_table() -> list[dict]:
    """全景节点大表：每个节点的 id、参与人列表、共享文件列表。"""
    from emily_core.infrastructure.database.models import (
        File, NodeAccessibleFile, NodeParticipant, Project, ProjectNode, User,
    )
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        nodes = (
            session.query(
                ProjectNode.node_id.label("node_id"),
                ProjectNode.node_name.label("node_name"),
                ProjectNode.node_type.label("node_type"),
                ProjectNode.status.label("status"),
                ProjectNode.project_id.label("project_id"),
                Project.name.label("project_name"),
                ProjectNode.responsible_user_id.label("responsible_user_id"),
                ProjectNode.deadline.label("deadline"),
                ProjectNode.remark.label("remark"),
                ProjectNode.template_ref_id.label("template_ref_id"),
            )
            .outerjoin(Project, Project.id == ProjectNode.project_id)
            .filter(ProjectNode.is_discarded.isnot(True))
            .order_by(ProjectNode.project_id, ProjectNode.node_id)
            .limit(_LIST_LIMIT)
            .all()
        )

        node_ids = [n.node_id for n in nodes]

        # 参与人（node_participants → users）
        participants_map: dict[str, list[dict]] = {}
        if node_ids:
            part_rows = (
                session.query(
                    NodeParticipant.node_id,
                    User.id.label("user_id"),
                    User.username.label("username"),
                    NodeParticipant.participant_role,
                )
                .join(User, User.id == NodeParticipant.user_id)
                .filter(NodeParticipant.node_id.in_(node_ids))
                .all()
            )
            for r in part_rows:
                participants_map.setdefault(r.node_id, []).append({
                    "user_id": r.user_id,
                    "name": r.username or r.user_id,
                    "role": r.participant_role or "participant",
                })

        # 共享文件（node_accessible_files → files）
        files_map: dict[str, list[dict]] = {}
        if node_ids:
            file_rows = (
                session.query(
                    NodeAccessibleFile.node_id,
                    File.id.label("file_id"),
                    File.filename.label("filename"),
                    File.file_no.label("file_no"),
                )
                .join(File, File.id == NodeAccessibleFile.file_id)
                .filter(NodeAccessibleFile.node_id.in_(node_ids))
                .all()
            )
            for r in file_rows:
                files_map.setdefault(r.node_id, []).append({
                    "file_id": r.file_id,
                    "filename": r.filename or "",
                    "file_no": r.file_no or "",
                })

    return [
        {
            "node_id": n.node_id,
            "node_name": n.node_name,
            "node_type": n.node_type or "",
            "status": n.status or "",
            "project_id": n.project_id or "",
            "project_name": n.project_name or "",
            "responsible_user_id": n.responsible_user_id or "",
            "deadline": n.deadline or "",
            "remark": n.remark or "",
            "template_ref_id": n.template_ref_id or "",
            "participants": participants_map.get(n.node_id, []),
            "shared_files": files_map.get(n.node_id, []),
        }
        for n in nodes
    ]

# ══════════════════════════════════════════════════════════════════════════════
#  全景节点参考模板 / 装配 / 收容 —— **全部经能力层**（C13 观察窗口，不在此复刻业务逻辑）
#
#  模板发现入口唯一：index.yaml（由 scripts/maintain_node_template_index.py 在宿主机生成）。
#  解析/装配/判定/迁正/停用一律调用能力层：
#    · NodeTemplateLoader        —— 清单与详情（只读检索能力同源）
#    · NodeAssemblyService       —— 对象声明解析 + 四类采集器 → 只读草稿
#    · NodeContainerService      —— 收容区查询 / 认领迁正 / 事件归位
#    · NodeService               —— 创建期一次性落库 / 停用恢复
# ══════════════════════════════════════════════════════════════════════════════

def _template_loader():
    from emily_core.services.node_template_loader import NodeTemplateLoader

    return NodeTemplateLoader()


def _assembly_service():
    from emily_core.services.node_assembly_service import NodeAssemblyService

    return NodeAssemblyService()



# ══════════════════════════════════════════════════════════════════════════════
#  权限快照 + 可见集合（同步）
# ══════════════════════════════════════════════════════════════════════════════

def _build_visible_sets(user_id: str) -> tuple[set[str], set[str], set[str]]:
    """返回 (可见文件 id 集合, 可见节点 id 集合, 可见 SOP id 集合)。"""
    from emily_core.services.permission_service import PermissionService
    from emily_core.services.visible_file_set_resolver import VisibleFileSetResolver

    # 优先复用 core 已初始化的 PermissionService（含 L2 缓存与 skill_registry）
    perm_svc: PermissionService | None = None
    try:
        from api.server import get_core
        core = get_core()
        perm_svc = getattr(core, "_permission_service", None)
    except Exception:
        perm_svc = None
    if perm_svc is None:
        perm_svc = PermissionService()

    perm = perm_svc.build_permission_dict(user_id)

    visible_file_ids = set(
        VisibleFileSetResolver().resolve_visible_file_list(
            user_id,
            company_id=perm.get("company_id", ""),
            info_level=perm.get("info_level", "public"),
        )
    )
    node_ids = set(perm.get("authorized_node_ids", []))
    sop_ids = set(perm.get("sop_allow", []))

    return visible_file_ids, node_ids, sop_ids


# ══════════════════════════════════════════════════════════════════════════════
#  端点
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users():
    """用户下拉候选（复用 scripts.options 的 users 数据源）。"""
    from emily_core.scripts.options import list_options

    try:
        users = await asyncio.to_thread(list_options, "users")
    except Exception as ex:
        logger.warning("console.users failed: %s", ex)
        return _err(f"读取用户列表失败：{ex}")
    return _ok({"users": users})


@router.get("/node-table")
async def get_node_table(user_id: str = Query("", description="用户 ID，空=返回全量")):
    """全景节点大表（含参与人、共享文件）；传入 user_id 时按权限过滤。"""
    try:
        rows = await asyncio.to_thread(_list_node_table)

        scope = "all"
        if user_id:
            scope = "user"
            _, visible_node_ids, _ = await asyncio.to_thread(_build_visible_sets, user_id)
            rows = [r for r in rows if r["node_id"] in visible_node_ids]

        return _ok({
            "user_id": user_id or None,
            "scope": scope,
            "count": len(rows),
            "rows": rows,
        })
    except Exception as ex:
        logger.exception("console.node_table failed user_id=%s", user_id)
        return _err(f"读取全景节点表失败：{ex}")


@router.get("/node-templates")
async def get_node_templates(node_type: str = Query(""), keyword: str = Query("")):
    """参考模板清单 —— 经能力层 NodeTemplateLoader（与对话/脚本同源，路由不复刻解析）。"""
    try:
        loader = _template_loader()
        available, why = await asyncio.to_thread(loader.is_available)
        if not available:
            return _ok({"exists": False, "count": 0, "templates": [],
                        "reason": why, "dir": str(getattr(loader, "templates_dir", ""))})
        templates = await asyncio.to_thread(loader.list_templates, node_type, keyword)
        return _ok({
            "dir": str(getattr(loader, "templates_dir", "")),
            "index_file": str(Path(getattr(loader, "templates_dir", "")) / "index.yaml"),
            "exists": True,
            "count": len(templates),
            "templates": [
                {"ref_id": t.ref_id, "node_name": t.node_name, "node_type": t.node_type,
                 "stage_id": t.stage_id, "summary": t.summary,
                 "attachment_count": t.attachment_count}
                for t in templates
            ],
        })
    except Exception as ex:
        logger.exception("console.node_templates failed")
        return _err(f"读取参考模板库失败：{ex}")


@router.get("/node-template")
async def get_node_template_detail(ref_id: str = Query(..., description="模板 ref_id")):
    """按 ref_id 读模板详情（清单 / 成果 / 对象声明 / 附件清单）—— 经能力层。"""
    try:
        from emily_core.services.node_assembly_service import NodeDeclarationParser

        detail = await asyncio.to_thread(_template_loader().get_template, ref_id)
        if detail is None:
            return _err(f"未在模板库中找到模板 {ref_id}")
        parsed = await asyncio.to_thread(NodeDeclarationParser().parse, detail.declarations_raw)
        return _ok({
            "ref_id": detail.ref_id,
            "node_name": detail.node_name,
            "node_type": detail.node_type,
            "stage_id": detail.stage_id,
            "summary": detail.summary,
            "deliverables": [
                {"deliverable_name": d.name, "target_amount": d.target_amount,
                 "unit": d.unit, "is_required": d.is_required,
                 "typical_filenames": d.typical_filenames}
                for d in detail.deliverables
            ],
            "declarations": parsed.to_dict(),
            "preconditions": detail.preconditions,
            "attachments": [
                {"name": a.name, "size": a.size, "type": a.type} for a in detail.attachments
            ],
            "warnings": list(detail.warnings) + list(parsed.warnings),
        })
    except Exception as ex:
        logger.exception("console.node_template_detail failed ref=%s", ref_id)
        return _err(f"读取模板详情失败：{ex}")


@router.get("/node-draft")
async def get_node_draft(
    ref_id: str = Query(..., description="模板 ref_id"),
    project_id: str = Query("", description="目标项目 ID（用于检索项目内已有成果）"),
    user_id: str = Query("", description="操作人 UUID（共享文件候选受其可见范围约束）"),
):
    """按模板装配「只读草稿」（四类候选 + 依据 + 未解析项）—— 经能力层 NodeAssemblyService。"""
    try:
        draft = await asyncio.to_thread(
            _assembly_service().build_draft, ref_id, project_id, user_id)
        return _ok(draft.to_dict())
    except KeyError as ex:
        return _err(f"未在模板库中找到模板：{ex}")
    except Exception as ex:
        logger.exception("console.node_draft failed ref=%s project=%s", ref_id, project_id)
        return _err(f"装配节点草稿失败：{ex}")


@router.get("/sops")
async def get_sops(user_id: str = Query("", description="用户 ID，空=返回全量")):
    """现有 SOP 清单；传入 user_id 时按该用户权限过滤。"""
    try:
        sops = await asyncio.to_thread(_list_sops)

        scope = "all"
        if user_id:
            scope = "user"
            _, _, visible_sop_ids = await asyncio.to_thread(_build_visible_sets, user_id)
            sops = [s for s in sops if s["sop_id"] in visible_sop_ids]

        return _ok({
            "user_id": user_id or None,
            "scope": scope,
            "count": len(sops),
            "rows": sops,
        })
    except Exception as ex:
        logger.exception("console.sops failed user_id=%s", user_id)
        return _err(f"读取 SOP 清单失败：{ex}")


@router.get("/project-events")
async def get_project_events(
    project_id: str = Query("", description="按项目过滤"),
    node_id: str = Query("", description="按全景节点过滤"),
    kind: str = Query("", description="按事件类型过滤（EVENT/TASK/MEETING/FILE/BUSINESS_FLOW/DELIVERABLE/NODE_EVENT）"),
    limit: int = Query(200, description="返回上限"),
):
    """统一项目事件时间线 —— 会议/事件/任务/文件归档/流转单/节点成果/节点事件。

    数据源为 project_events 单表（统一事件积累），挂在 project_id + node_id 下，
    保证事件有归属、有责任人；无法确定归属的挂在临时节点 UNASSIGNED。
    """
    try:
        rows = await asyncio.to_thread(_list_project_events, project_id, node_id, kind, limit)
        from emily_core.infrastructure.database.models import ProjectEventKind
        return _ok({
            "count": len(rows),
            "kinds": ProjectEventKind.ALL,
            "kind_display": ProjectEventKind.DISPLAY_NAMES,
            "rows": rows,
        })
    except Exception as ex:
        logger.exception("console.project_events failed")
        return _err(f"读取项目事件失败：{ex}")


@router.get("/project-events/{event_id}")
def get_project_event_detail(event_id: str):
    """读取单条项目事件的完整细节（含 payload、操作人姓名、认证信息，只读）。"""
    import json
    from emily_core.infrastructure.database.models import (
        Project, ProjectEvent, ProjectEventKind, ProjectNode, User,
    )
    from emily_core.infrastructure.database.session import get_session

    try:
        with get_session() as session:
            row = session.query(ProjectEvent).filter(ProjectEvent.id == event_id).first()
            if row is None:
                return _err("项目事件不存在")
            event_kind = row.event_kind or ""
            project_name = node_name = actor_name = ""
            if row.project_id:
                p = session.query(Project).filter(Project.id == row.project_id).first()
                project_name = p.name if p else ""
            if row.node_id:
                n = session.query(ProjectNode).filter(ProjectNode.node_id == row.node_id).first()
                node_name = n.node_name if n else ""
            if row.actor_id:
                u = session.query(User).filter(User.id == row.actor_id).first()
                actor_name = u.username if u else ""
            try:
                payload = json.loads(row.payload or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            detail = {
                "id": row.id,
                "event_no": row.event_no or "",
                "event_kind": event_kind,
                "event_kind_display": ProjectEventKind.display(event_kind),
                "event_type": row.event_type or "",
                "title": row.title or "",
                "summary": row.summary or "",
                "status": row.status or "",
                "node_id": row.node_id or "",
                "node_name": node_name or "",
                "project_id": row.project_id or "",
                "project_name": project_name or "",
                "actor_id": row.actor_id or "",
                "actor_name": actor_name or "",
                "occurred_at": row.occurred_at or "",
                "created_at": row.created_at or "",
                "confirmed_by": row.confirmed_by or "",
                "confirmed_at": row.confirmed_at or "",
                "source_message_id": row.source_message_id or "",
                "payload": payload,
            }
        return _ok(detail)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.project_event_detail failed")
        return _err(f"读取项目事件详情失败：{ex}")


@router.post("/upload")
async def upload_file(
    user_id: str = Form(""),
    file: UploadFile = File(...),
    confidentiality: int = Form(1),
    purpose: str = Form("RECORD"),
    file_category: str = Form("OTHER"),
):
    """上传文件到文件库（以选定用户名义归档，默认密级内部）。

    动作与留痕均在 FileManager.archive_upload（service 动作层，同源同痕），
    本路由只做参数校验与转调（不自行落盘、不直调 repo）。
    `purpose` / `file_category` 由调用方指定（缺省 RECORD / OTHER）；
    `purpose=REFERENCE` 时按 REFERENCE 策略异步入 RAG（与 record_file 通道同源）。
    """
    if not user_id:
        return _err("请选择上传用户")

    if confidentiality not in (0, 1, 2):
        confidentiality = 1

    from emily_core.infrastructure.database.models import FileCategory, FilePurpose

    purpose = FilePurpose.validate(purpose)
    file_category = FileCategory.validate(file_category)

    data = await file.read()
    if not data:
        return _err("文件内容为空")

    fm = _get_file_manager()
    if fm is None:
        return _err("文件服务未就绪")

    try:
        res = await asyncio.to_thread(
            fm.archive_upload,
            file.filename or "",
            data,
            uploaded_by=user_id,
            content_type=file.content_type or "",
            confidentiality=confidentiality,
            file_category=file_category,
            purpose=purpose,
        )
    except Exception as ex:
        logger.exception("console.upload failed user_id=%s", user_id)
        return _err(f"上传失败：{ex}")

    if not res.get("success"):
        return _err("上传失败：文件记录未创建")

    # REFERENCE 类文件异步入通用 RAG 库（复用 record_file 通道的 _index_reference_file）
    if purpose == FilePurpose.REFERENCE:
        from api.server import get_core

        core = get_core()
        tei = getattr(core, "_tei_client", None)
        kc_repo = getattr(core, "_knowledge_chunk_repo", None)
        if tei is not None and kc_repo is not None:
            from emily_core.tools.file_tool import _index_reference_file

            asyncio.create_task(_index_reference_file(
                res.get("file_id", ""), tei, kc_repo, file_manager=fm,
            ))
            logger.info("console.upload: REFERENCE file scheduled for RAG indexing: %s",
                        res.get("file_id", ""))

    # 归档后分析（门禁 → 归属判定 → 临时节点/提案）：经注册表分发，fail-open 不阻断归档
    try:
        from emily_core.services.archive_handler_registry import ArchiveHandlerRegistry

        asyncio.create_task(ArchiveHandlerRegistry.dispatch(
            file_id=res.get("file_id", ""),
            project_id=project_id or "",
            actor_id=user_id or "",
            filename=res.get("filename", "") or "",
        ))
    except Exception as ex:  # 分发失败不影响归档结果
        logger.warning("console.upload: archive dispatch skipped: %s", ex)

    return _ok({k: res.get(k) for k in (
        "file_id", "file_no", "filename", "size", "confidentiality",
    )} | {"purpose": purpose, "file_category": file_category})


# ══════════════════════════════════════════════════════════════════════════════
#  文件管理 —— 库内全量文件清单 + 删除（节点/RAG 进出复用下方既有接口）
# ══════════════════════════════════════════════════════════════════════════════

def _beijing_ymd(value) -> str:
    """created_at（UTC，形如 '2026-09-14 02:54:26.652742+00'）→ 北京时间 YYYY-MM-DD。

    使用 UTC 日期直接截取会让凌晨 0-8 点上传的文件显示成前一天，故按北京时间换算。
    """
    from datetime import datetime, timezone

    from emily_core.infrastructure.database.models import BEIJING_TZ

    if not value:
        return ""
    dt = value
    if isinstance(dt, str):
        s = dt.strip().replace(" ", "T", 1)
        if s.endswith("+00"):
            s += ":00"  # '+00' → '+00:00'，补齐 ISO 偏移格式
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return dt[:10]
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime("%Y-%m-%d")


def _container_abspath(storage_path: str, root: str) -> str:
    """相对存储根的路径 → 容器内绝对路径（如 /app/attachments/mock/...）。"""
    if not storage_path:
        return ""
    base = (root or "").rstrip("/\\")
    rel = str(storage_path).replace("\\", "/").lstrip("/")
    return f"{base}/{rel}" if base else rel


def _list_files_managed() -> list[dict]:
    """文件管理清单：文件名/ID/密级/上传人/上传日期/容量/是否入 RAG/可见节点/存放路径。"""
    from emily_core.infrastructure.database.models import (
        File, KnowledgeChunk, NodeAccessibleFile, ProjectNode, User,
    )
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        files = (
            session.query(
                File.id.label("id"),
                File.filename.label("name"),
                File.file_no.label("file_no"),
                File.confidentiality.label("confidentiality"),
                File.uploaded_by.label("uploaded_by"),
                File.created_at.label("created_at"),
                File.file_size.label("file_size"),
                File.storage_path.label("storage_path"),
            )
            .filter(File.is_deleted.isnot(True))
            .order_by(File.created_at.desc())
            .limit(_LIST_LIMIT)
            .all()
        )

        file_ids = [f.id for f in files]

        # 上传人用户名
        user_map: dict[str, str] = {}
        uploaded_by_ids = {f.uploaded_by for f in files if f.uploaded_by}
        if uploaded_by_ids:
            user_rows = session.query(User.id, User.username).filter(
                User.id.in_(uploaded_by_ids)
            ).all()
            user_map = {r.id: (r.username or r.id) for r in user_rows}

        # RAG 已入库 doc_id 集合
        rag_doc_ids: set[str] = set()
        if file_ids:
            rag_doc_ids = {
                r.doc_id
                for r in session.query(KnowledgeChunk.doc_id)
                .filter(KnowledgeChunk.doc_id.in_(file_ids))
                .distinct()
                .all()
            }

        # 可见节点列表（file_id → 节点）
        nodes_map: dict[str, list[dict]] = {}
        if file_ids:
            naf_rows = (
                session.query(
                    NodeAccessibleFile.file_id,
                    ProjectNode.node_id,
                    ProjectNode.node_name,
                )
                .join(ProjectNode, ProjectNode.node_id == NodeAccessibleFile.node_id)
                .filter(NodeAccessibleFile.file_id.in_(file_ids))
                .order_by(ProjectNode.node_id)
                .all()
            )
            for r in naf_rows:
                nodes_map.setdefault(r.file_id, []).append({
                    "node_id": r.node_id,
                    "name": r.node_name or r.node_id,
                })

    try:
        _root = str(_get_storage_service()._storage_root)
    except Exception:  # 存储服务未就绪时不阻断清单
        _root = ""

    return [
        {
            "id": f.id,
            "name": f.name or "",
            "file_no": f.file_no or "",
            "confidentiality": f.confidentiality or 0,
            "uploaded_by": f.uploaded_by or "",
            "uploaded_by_name": (user_map.get(f.uploaded_by, "") if f.uploaded_by else ""),
            "created_at": f.created_at or "",
            "created_ymd": _beijing_ymd(f.created_at),
            "file_size": int(f.file_size or 0),
            "in_rag": f.id in rag_doc_ids,
            "visible_nodes": nodes_map.get(f.id, []),
            "storage_abspath": _container_abspath(f.storage_path, _root),
        }
        for f in files
    ]


def _storage_info() -> dict:
    """文件管理实际存储位置信息（容器 / 端口 / 容器内目录 / 宿主机目录）。"""
    from datetime import datetime, timezone

    storage = _get_storage_service()
    root = str(storage._storage_root).rstrip("/\\")
    platform = storage._platform or "napcat"
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return {
        "container": "emily-core",
        "port": 18080,
        "dir_container": f"{root}/{platform}/{month}/",
        "dir_host": f"D:\\app\\Emily\\emily-data\\attachments\\{platform}\\{month}\\",
    }


async def _delete_file(file_id: str, operator_id: str = "") -> dict:
    """软删除文件：复用 file_manager.soft_delete + RAG 分块清理 + 节点可见关联清理。"""
    import json

    from api.server import get_core
    from emily_core.infrastructure.logging.business_event_logger import BusinessEventLogger
    from emily_core.repositories.file_repo import FileRepository
    from emily_core.repositories.node_repo import NodeAccessibleFileRepo

    core = get_core()
    fm = getattr(core, "_file_manager", None)
    if fm is None:
        return {"success": False, "error": "文件服务未就绪"}

    file_record = await asyncio.to_thread(FileRepository.get_by_id, file_id)
    if file_record is None or file_record.is_deleted:
        return {"success": False, "error": "文件不存在或已删除"}
    name = file_record.filename or ""

    ok = await asyncio.to_thread(fm.soft_delete, file_id, operator_id)
    if not ok:
        return {"success": False, "error": "文件不存在或已删除"}

    deleted_chunks = 0
    chunk_repo = getattr(core, "_knowledge_chunk_repo", None)
    if chunk_repo is not None:
        deleted_chunks = await asyncio.to_thread(chunk_repo.delete_by_doc, file_id) or 0

    deleted_links = await asyncio.to_thread(NodeAccessibleFileRepo.remove_by_file, file_id) or 0

    result = {
        "success": True,
        "file_id": file_id,
        "name": name,
        "deleted_chunks": int(deleted_chunks),
        "deleted_links": int(deleted_links),
    }

    # 留痕由 FileManager.soft_delete 的挂载点产生（service 动作层），
    # 入口层不再写留痕 —— 否则同一次删除会记两条。
    return result


class FileDeleteRequest(BaseModel):
    file_id: str
    operator_id: str = ""


@router.get("/files")
async def list_managed_files(user_id: str = Query("", description="用户 ID，空=返回全量")):
    """文件管理：库内文件清单（含容量、RAG 状态、可见节点）；传入 user_id 时按权限裁剪。"""
    try:
        rows = await asyncio.to_thread(_list_files_managed)
        if user_id:
            try:
                visible_file_ids, _, _ = await asyncio.to_thread(_build_visible_sets, user_id)
                rows = [r for r in rows if r["id"] in visible_file_ids]
            except Exception as ex:
                logger.warning("console.files visible filter failed user_id=%s: %s", user_id, ex)
        storage = await asyncio.to_thread(_storage_info)
        return _ok({"count": len(rows), "rows": rows, "storage": storage})
    except Exception as ex:
        logger.exception("console.files failed")
        return _err(f"读取文件清单失败：{ex}")


@router.post("/file-delete")
async def delete_managed_file(req: FileDeleteRequest):
    """删除库内文件（软删除，同时清理 RAG 分块与节点关联，以 operator_id 登记日志）。"""
    file_id = req.file_id.strip()
    if not file_id:
        return _err("请选择要删除的文件")
    try:
        res = await _delete_file(file_id, req.operator_id)
    except Exception as ex:
        logger.exception("console.file_delete failed file_id=%s", file_id)
        return _err(f"删除失败：{ex}")
    if not res.get("success"):
        return _err(res.get("error", "删除失败"))
    return _ok(res)


class FileConfidentialityRequest(BaseModel):
    file_id: str
    confidentiality: int
    operator_id: str = ""


@router.post("/file-confidentiality")
async def update_managed_file_confidentiality(req: FileConfidentialityRequest):
    """调整文件密级（仅上传人本人或 L5/L6 管理员；密级变动后即时重算可见性）。"""
    file_id = req.file_id.strip()
    if not file_id:
        return _err("请选择要调整密级的文件")
    if req.confidentiality not in (0, 1, 2):
        return _err("密级值域非法：仅允许 0=公开 / 1=内部 / 2=机密")
    try:
        from emily_core.services.file_service import FileService
        res = await asyncio.to_thread(
            FileService().update_confidentiality,
            file_id, req.confidentiality, req.operator_id,
        )
    except Exception as ex:
        logger.exception("console.file_confidentiality failed file_id=%s", file_id)
        return _err(f"密级调整失败：{ex}")
    if not res.get("success"):
        return _err(res.get("reply", res.get("error", "密级调整失败")))
    return _ok(res)


# ══════════════════════════════════════════════════════════════════════════════
#  Prompt 工程 —— 提示词模板清单 + 详情（内容 / 插入点 / 来源描述）
# ══════════════════════════════════════════════════════════════════════════════

def _prompts_dir() -> Path:
    """解析提示词模板目录（与 prompt_loader 多级回退优先级一致）。"""
    import os
    from pathlib import Path

    env_dir = os.environ.get("EMILY_PROMPTS_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p
    container = Path("/app/prompts")
    if container.exists():
        return container
    return Path(__file__).resolve().parents[4] / "emily-data" / "prompts"


def _list_prompts() -> list[dict]:
    """列出全部提示词模板文件（*.md）。"""
    d = _prompts_dir()
    rows: list[dict] = []
    for p in sorted(d.glob("*.md")):
        rows.append({
            "name": p.stem,
            "source": str(p),
            "size": p.stat().st_size,
        })
    return rows


def _prompt_detail(name: str) -> dict:
    """读取单个模板：来源描述（HTML 注释）+ 正文 + 插入点 {var} 标记。"""
    import re
    from pathlib import Path

    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return {"success": False, "error": "非法模板名"}

    p = _prompts_dir() / f"{name}.md"
    if not p.exists():
        return {"success": False, "error": "模板不存在"}

    raw = p.read_text(encoding="utf-8")

    # 来源描述：文件头部 HTML 注释（用途 / 加载位置 / 模板变量说明）
    comments = re.findall(r"<!--(.*?)-->", raw, flags=re.DOTALL)
    description = "\n".join(c.strip() for c in comments if c.strip())

    # 正文：去掉 HTML 注释
    content = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)

    # 插入点：{var} 单花括号占位符（自动跳过 {{...}} JSON 模板）
    insert_points = sorted(set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", content)))

    return {
        "success": True,
        "name": name,
        "source": str(p),
        "description": description,
        "insert_points": insert_points,
        "content": content,
    }


@router.get("/prompts")
async def list_prompts():
    """Prompt 工程：列出全部提示词模板。"""
    try:
        rows = await asyncio.to_thread(_list_prompts)
        return _ok({"count": len(rows), "prompts": rows})
    except Exception as ex:
        logger.exception("console.prompts failed")
        return _err(f"读取提示词模板失败：{ex}")


@router.get("/prompt")
async def prompt_detail(name: str = Query("")):
    """Prompt 工程：单个模板详情（内容 / 插入点 / 来源描述）。"""
    try:
        res = await asyncio.to_thread(_prompt_detail, name)
    except Exception as ex:
        logger.exception("console.prompt failed name=%s", name)
        return _err(f"读取模板详情失败：{ex}")
    if not res.get("success"):
        return _err(res.get("error", "读取失败"))
    res.pop("success", None)
    return _ok(res)


# ══════════════════════════════════════════════════════════════════════════════
#  RAG 入库
# ══════════════════════════════════════════════════════════════════════════════

class RagIndexRequest(BaseModel):
    file_id: str
    user_id: str = ""
    backend: str = "auto"   # auto=本地优先+API兜底，local=仅本地，api=仅远程 API


class RagDeleteRequest(BaseModel):
    doc_id: str
    operator_id: str = ""


class RagSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    backend: str = "auto"
    user_id: str = ""


def _get_embedding_config() -> dict:
    """读取当前 core 的 embedding 配置。"""
    from api.server import get_core

    cfg = get_core().config
    return {
        "api_url": getattr(cfg, "embedding_api_url", "") or "",
        "api_key": getattr(cfg, "embedding_api_key", "") or "",
        "api_model": getattr(cfg, "embedding_model", "") or "",
        "tei_url": getattr(cfg, "tei_url", "") or "",
    }


def _build_embedding_client(backend: str):
    """按 backend 构造 embedding client，返回 (client, error)。

    backend 取值：auto（默认，本地优先 + 远程 API 兜底）/ local（仅本地）/ api（仅远程）。
    选型规则统一复用 emily_core.infrastructure.embedding.factory，不在此复刻第二份逻辑。
    """
    from api.server import get_core
    from emily_core.infrastructure.embedding.factory import (
        MODE_LOCAL,
        MODE_REMOTE,
        create_embedding_client,
        normalize_mode,
    )

    mode = normalize_mode(backend)
    client = create_embedding_client(get_core().config, mode=mode)
    if client is not None:
        return client, None

    if mode == MODE_LOCAL:
        return None, "本地 TEI 地址未配置"
    if mode == MODE_REMOTE:
        return None, "远程 Embedding API 未配置完整（url/key/model）"
    return None, "本地 TEI 与远程 Embedding API 均不可用"


@router.post("/rag-index")
async def rag_index_file(req: RagIndexRequest):
    """给选定文件执行 RAG 入库（doc_id 锚定 files.id，选定执行人溯源）。

    动作与留痕均在 KnowledgeService.index_file（service 动作层，同源同痕）。
    """
    file_id = req.file_id.strip()
    if not file_id:
        return _err("请选择要入库的文件")

    from api.server import get_core

    repo = getattr(get_core(), "_knowledge_chunk_repo", None)
    if repo is None:
        return _err("RAG 知识库未就绪")

    tei, err = _build_embedding_client(req.backend)
    if tei is None:
        return _err(err)

    try:
        res = await _get_knowledge_service(tei=tei, repo=repo).index_file(
            file_id, operator_id=req.user_id or "",
        )
    except Exception as ex:
        logger.exception("console.rag_index failed file_id=%s", file_id)
        return _err(f"入库失败：{ex}")

    if not res.get("success"):
        return _err(res.get("error", "入库失败"))

    return _ok({k: res.get(k) for k in (
        "file_id", "file_no", "filename", "doc_id", "count", "elapsed_ms",
    )})


@router.get("/rag/backends")
async def rag_backends():
    """返回 embedding 后端可用性，供前端渲染单选（本地不可用则置灰）。"""
    try:
        cfg = _get_embedding_config()

        api_available = bool(cfg["api_url"] and cfg["api_key"] and cfg["api_model"])

        local_available = False
        local_reason = ""
        if cfg["tei_url"]:
            try:
                from emily_core.infrastructure.embedding.tei_client import TeiClient
                local_available = await TeiClient(cfg["tei_url"]).is_available()
                if not local_available:
                    local_reason = "本地 TEI 服务未运行或不可达"
            except Exception as ex:  # 探测失败视为不可用
                local_reason = f"本地 TEI 探测失败：{ex}"
        else:
            local_reason = "本地 TEI 地址未配置"

        return _ok({
            "auto": {
                "available": api_available or local_available,
                "label": "自动（本地优先，API 兜底）",
            },
            "api": {
                "available": api_available,
                "label": "远程 API（SiliconFlow）",
                "model": cfg["api_model"],
            },
            "local": {
                "available": local_available,
                "label": "本地（TEI）",
                "reason": local_reason,
                "url": cfg["tei_url"],
            },
        })
    except Exception as ex:
        logger.exception("console.rag_backends failed")
        return _err(f"读取 embedding 后端状态失败：{ex}")


@router.post("/rag-delete")
async def rag_delete_file(req: RagDeleteRequest):
    """删除库内指定文档的全部向量分块（doc_id 锚定 files.id）。

    动作、授权与留痕统一走 `rag_remove_document` 工具（缺口 G-5：console 与 IM 同源同能力）；
    授权口径为该工具内的 L5/L6 判定（无操作人或取不到等级一律拒绝），本路由不另立判定。
    """
    doc_id = req.doc_id.strip()
    if not doc_id:
        return _err("请选择要删除的库内文件")

    from api.server import get_core

    repo = getattr(get_core(), "_knowledge_chunk_repo", None)
    if repo is None:
        return _err("RAG 知识库未就绪")

    from emily_core.tools.embed_tool import handle_rag_remove_document

    try:
        res = await handle_rag_remove_document(
            {"doc_id": doc_id},
            knowledge_service=_get_knowledge_service(repo=repo),
            user_id=req.operator_id or "",
        )
    except Exception as ex:
        logger.exception("console.rag_delete failed doc_id=%s", doc_id)
        return _err(f"删除失败：{ex}")

    if not res.get("success"):
        return _err(res.get("reply", "删除失败"))
    data = res.get("data") or {}
    return _ok({"doc_id": data.get("doc_id", doc_id), "deleted": data.get("deleted", 0)})


@router.post("/rag-search")
async def rag_search(req: RagSearchRequest):
    """根据输入信息查库（向量检索）。

    检索实现与留痕均在 KnowledgeService.search（service 动作层，同源同痕）；
    可见范围过滤（scope）仍由本路由按所选用户解析后下传。
    """
    query = (req.query or "").strip()
    if not query:
        return _err("请输入查询内容")

    client, err = _build_embedding_client(req.backend)
    if client is None:
        return _err(err)

    from api.server import get_core

    repo = getattr(get_core(), "_knowledge_chunk_repo", None)
    if repo is None:
        return _err("RAG 知识库未就绪")

    # C14/M3：按所选用户可见文件范围过滤（复用 VisibleFileSetResolver，宁少答不泄露）
    scoped_doc_ids = None
    user_id = (req.user_id or "").strip()
    try:
        if user_id:
            visible_file_ids, _, _ = await asyncio.to_thread(_build_visible_sets, user_id)
            scoped_doc_ids = list(visible_file_ids)

        res = await _get_knowledge_service(tei=client, repo=repo).search(
            query,
            top_k=req.top_k,
            scoped_doc_ids=scoped_doc_ids,
            operator_id=user_id,
        )
    except Exception as ex:
        logger.exception("console.rag_search failed query=%s", query)
        return _err(f"查库失败：{ex}")

    if not res.get("success"):
        return _err(res.get("error", "查库失败"))

    return _ok({
        "query": res.get("query", query),
        "backend": req.backend,
        "total": res.get("total", 0),
        "results": res.get("results", []),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  全景节点表 —— 参与人 / 共享文件 增删（操作人以日志登记）
# ══════════════════════════════════════════════════════════════════════════════

class NodeParticipantMutate(BaseModel):
    action: str = "add"          # add | remove
    node_id: str = ""
    user_id: str = ""
    operator_id: str = ""
    role: str = "participant"    # participant / approver / observer


class NodeFileMutate(BaseModel):
    action: str = "add"          # add | remove
    node_id: str = ""
    file_id: str = ""
    operator_id: str = ""


@router.post("/node-participant")
async def mutate_node_participant(req: NodeParticipantMutate):
    """节点参与人增删。

    动作、授权与留痕统一走 `manage_node_participant` 工具（缺口 G-6：console 与 IM 同源同能力）；
    授权口径为该工具内的 L5/L6 判定（无操作人或取不到等级一律拒绝），本路由不另立判定。
    """
    if req.action not in ("add", "remove"):
        return _err("action 仅支持 add / remove")
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")

    from emily_core.tools.node_tool import handle_manage_node_participant

    try:
        res = await handle_manage_node_participant(
            {
                "action": req.action,
                "node_id": req.node_id,
                "user_id": req.user_id,
                "role": req.role or "participant",
            },
            node_service=_get_node_service(),
            user_id=req.operator_id,
        )
    except Exception as ex:
        logger.exception("console.node_participant failed node=%s", req.node_id)
        return _err(f"操作失败：{ex}")
    if not res.get("success"):
        return _err(res.get("reply", "操作失败"))
    return _ok({
        "success": True,
        "event_type": "participant_added" if req.action == "add" else "participant_removed",
        "node_id": req.node_id,
    })


@router.post("/node-file")
async def mutate_node_file(req: NodeFileMutate):
    """节点共享文件增删（复用 NodeService，以 operator_id 身份登记日志）。"""
    if req.action not in ("add", "remove"):
        return _err("action 仅支持 add / remove")
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    try:
        svc = _get_node_service()
        if req.action == "add":
            result = await svc.add_node_file(req.node_id, req.file_id, req.operator_id)
        else:
            result = await svc.remove_node_file(req.node_id, req.file_id, req.operator_id)
    except Exception as ex:
        logger.exception("console.node_file failed node=%s", req.node_id)
        return _err(f"操作失败：{ex}")
    if not result.success:
        return _err(result.message or "操作失败")
    return _ok({
        "success": True,
        "event_type": "file_added" if req.action == "add" else "file_removed",
        "node_id": req.node_id,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  全景节点表 —— 节点本身的增 / 改 / 删
#
#  口径：
#    - 三个动作均以 operator_id 登记操作人与日志，未选操作人一律拒绝；
#    - 创建口径（权限、成果必备、节点ID 规则）由 NodeService.create_node 统一把关；
#    - 修改仅开放 名称 / 截止时间 / 备注（NodeService.update_node 现有能力）；
#    - 删除为软删（置 is_discarded），保留成果/依赖/文件/参与人/事件等关联记录。
# ══════════════════════════════════════════════════════════════════════════════

class NodeCreateRequest(BaseModel):
    project_id: str = ""
    node_id: str = ""                    # 留空则按 generate_node_id(node_name, project_id) 生成
    node_name: str = ""
    node_type: str = "TASK"              # MILESTONE / TASK
    deadline: str = ""
    remark: str = ""
    responsible_user_id: str = ""        # 留空则取 creator_id
    template_ref_id: str = ""            # 来源参考模板 ref_id（选模板创建时记录）
    deliverables: list[dict] = Field(default_factory=list)
    dependencies: list[dict] = Field(
        default_factory=list,
        description="待建前置依赖（每项含 depends_on_deliverable_id）。由界面确认后传入，"
                    "落库编排中**最后**执行（依赖指向的是已存在的成果）。",
    )
    planned_start_at: str = ""           # 计划启动时间（ISO8601；到点即激活）
    participant_user_ids: list[dict] = Field(
        default_factory=list, description="参与人候选（每项 user_id + 可选 role）")
    shared_file_ids: list[str] = Field(
        default_factory=list, description="共享文件候选（file_id 列表，越权项被丢弃并回报）")
    operator_id: str = ""


class NodeUpdateRequest(BaseModel):
    node_id: str = ""
    node_name: str | None = None
    deadline: str | None = None
    remark: str | None = None
    operator_id: str = ""


class NodeDeleteRequest(BaseModel):
    node_id: str = ""
    operator_id: str = ""


@router.post("/node-create")
async def create_node_from_console(req: NodeCreateRequest):
    """新增全景节点（emy-console「增加节点」）。

    节点ID 留空时按 `generate_node_id(node_name, project_id)` 生成（与批量创建同规则）。
    权限与「至少一条必需成果」的把关在 NodeService.create_node，本路由不另立判定。
    """
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    if not req.project_id:
        return _err("请选择所属项目")
    if not req.node_name.strip():
        return _err("请填写节点名称")
    if not req.deadline.strip():
        return _err("请填写截止时间")

    from emily_core.services.node_batch import generate_node_id
    from emily_core.services.node_commands import CreateNodeCommand

    svc = _get_node_service()
    node_id = req.node_id.strip() or generate_node_id(req.node_name.strip(), req.project_id)

    # 手工创建场景下重复 node_id 会误导操作人（同名+同项目算出同一个 node_id），
    # 故先行判定并明确拒绝——经**公开方法** `get_node_detail` 查询，不访问服务内部成员（C13）。
    try:
        duplicated = await svc.get_node_detail(node_id)
    except Exception:
        duplicated = None
    if duplicated is not None:
        return _err(f"节点ID「{node_id}」已存在，请改用其它节点名称或手工指定节点ID")

    # 一次性落库六类对象（节点 → 成果 → 参与单位 → 参与人 → 共享文件 → 依赖），
    # 顺序与失败隔离策略由能力层统一实现（M4），路由不另立判定。
    cmd = CreateNodeCommand(
        project_id=req.project_id,
        node_id=node_id,
        node_name=req.node_name.strip(),
        deadline=req.deadline.strip(),
        creator_id=req.operator_id,
        remark=req.remark,
        responsible_user_id=req.responsible_user_id,
        node_type=req.node_type or "TASK",
        deliverables=req.deliverables,
        template_ref_id=req.template_ref_id,
        planned_start_at=req.planned_start_at or "",
        participant_user_ids=req.participant_user_ids or [],
        shared_file_ids=req.shared_file_ids or [],
        dependencies=req.dependencies or [],
    )
    try:
        result = await svc.create_node(cmd)
    except Exception as ex:
        logger.exception("console.node_create failed project=%s", req.project_id)
        return _err(f"创建失败：{ex}")
    if not result.success:
        return _err(result.message or "创建失败")

    return _ok({
        "success": True,
        "node_id": result.node_id,
        "status": result.status,
        "message": result.message,
        "created": result.created,
        "failed": result.failed,
        "discarded": result.discarded,
    })


@router.post("/node-update")
async def update_node_from_console(req: NodeUpdateRequest):
    """修改节点字段（名称 / 截止时间 / 备注），复用 NodeService.update_node。"""
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    if not req.node_id:
        return _err("缺少节点ID")

    from emily_core.services.node_commands import UpdateNodeCommand

    cmd = UpdateNodeCommand(
        node_id=req.node_id,
        operator_id=req.operator_id,
        node_name=req.node_name,
        deadline=req.deadline,
        remark=req.remark,
    )
    try:
        result = await _get_node_service().update_node(cmd)
    except Exception as ex:
        logger.exception("console.node_update failed node=%s", req.node_id)
        return _err(f"保存失败：{ex}")
    if not result.success:
        return _err(result.message or "保存失败")
    return _ok({"success": True, "node_id": req.node_id, "message": result.message})


@router.post("/node-delete")
async def delete_node_from_console(req: NodeDeleteRequest):
    """删除节点（软删：置 is_discarded，关联成果/依赖/事件等记录保留）。"""
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    if not req.node_id:
        return _err("缺少节点ID")

    from emily_core.services.node_commands import DiscardNodeCommand

    cmd = DiscardNodeCommand(node_id=req.node_id, operator_id=req.operator_id)
    try:
        result = await _get_node_service().discard_node(cmd)
    except Exception as ex:
        logger.exception("console.node_delete failed node=%s", req.node_id)
        return _err(f"删除失败：{ex}")
    if not result.success:
        return _err(result.message or "删除失败")
    return _ok({"success": True, "node_id": req.node_id, "message": result.message})


# ══════════════════════════════════════════════════════════════════════════════
#  收容区 / 认领迁正 / 事件归位 / 停用恢复 —— 动作一律复用能力层（C13）
# ══════════════════════════════════════════════════════════════════════════════

class NodePromoteRequest(BaseModel):
    node_id: str = ""
    target_parent_id: str = ""
    operator_id: str = ""
    remark: str = ""


class EventReassignRequest(BaseModel):
    event_id: str = ""
    target_node_id: str = ""
    operator_id: str = ""
    remark: str = ""


class NodeDisableRequest(BaseModel):
    node_id: str = ""
    operator_id: str = ""
    reason: str = ""


class NodeEnableRequest(BaseModel):
    node_id: str = ""
    operator_id: str = ""
    remark: str = ""


def _container_service():
    from emily_core.services.node_container_service import NodeContainerService

    return NodeContainerService(node_service=_get_node_service())


@router.get("/node-containers")
async def get_node_containers(
    project_id: str = Query(..., description="项目 ID"),
    viewer_id: str = Query("", description="查看人 UUID（可见范围判定）"),
):
    """收容区清单（只读）：临时节点与未归类收容节点，区分「无归属 / 待认领 / 待归类收容」。"""
    try:
        level = 0
        if viewer_id:
            from emily_core.services.permission_service import PermissionService

            perms = await asyncio.to_thread(
                PermissionService().build_permission_dict, viewer_id)
            level = int(perms.get("level", 0) or 0)
        data = await _container_service().list_contained(project_id, viewer_id, level)
        return _ok(data)
    except Exception as ex:
        logger.exception("console.node_containers failed project=%s", project_id)
        return _err(f"读取收容区失败：{ex}")


@router.post("/node-promote")
async def promote_node_from_console(req: NodePromoteRequest):
    """认领迁正（临时节点 → 正式归属位置；编号不变，留痕）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    if not req.node_id:
        return _err("缺少节点ID")
    from emily_core.services.node_commands import PromoteNodeCommand

    try:
        res = await _container_service().promote(PromoteNodeCommand(
            node_id=req.node_id, target_parent_id=req.target_parent_id,
            operator_id=req.operator_id, remark=req.remark))
    except Exception as ex:
        logger.exception("console.node_promote failed node=%s", req.node_id)
        return _err(f"迁正失败：{ex}")
    if not res.success:
        return _err(res.message or "迁正失败")
    return _ok({"success": True, "node_id": res.node_id, "message": res.message})


@router.post("/node-event-reassign")
async def reassign_event_from_console(req: EventReassignRequest):
    """事件归位（未归类收容节点 → 具体节点；事件编号不变，留痕）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    if not req.event_id or not req.target_node_id:
        return _err("缺少事件ID或目标节点")
    from emily_core.services.node_commands import ReassignEventCommand

    try:
        res = await _container_service().reassign_event(ReassignEventCommand(
            event_id=req.event_id, target_node_id=req.target_node_id,
            operator_id=req.operator_id, remark=req.remark))
    except Exception as ex:
        logger.exception("console.event_reassign failed event=%s", req.event_id)
        return _err(f"归位失败：{ex}")
    if not res.success:
        return _err(res.message or "归位失败")
    return _ok({"success": True, "node_id": res.node_id, "message": res.message})


@router.post("/node-disable")
async def disable_node_from_console(req: NodeDisableRequest):
    """停用节点（L4+，退出管控，非终态可恢复）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    from emily_core.services.node_commands import DisableNodeCommand

    try:
        res = await _get_node_service().disable_node(DisableNodeCommand(
            node_id=req.node_id, operator_id=req.operator_id, reason=req.reason))
    except Exception as ex:
        logger.exception("console.node_disable failed node=%s", req.node_id)
        return _err(f"停用失败：{ex}")
    if not res.success:
        return _err(res.message or "停用失败")
    return _ok({"success": True, "node_id": res.node_id, "status": res.status,
                "message": res.message})


@router.post("/node-enable")
async def enable_node_from_console(req: NodeEnableRequest):
    """恢复被停用的节点（回到真实三态）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    from emily_core.services.node_commands import EnableNodeCommand

    try:
        res = await _get_node_service().enable_node(EnableNodeCommand(
            node_id=req.node_id, operator_id=req.operator_id, remark=req.remark))
    except Exception as ex:
        logger.exception("console.node_enable failed node=%s", req.node_id)
        return _err(f"恢复失败：{ex}")
    if not res.success:
        return _err(res.message or "恢复失败")
    return _ok({"success": True, "node_id": res.node_id, "status": res.status,
                "message": res.message})


# ══════════════════════════════════════════════════════════════════════════════
#  日志聚合 —— 聚合各类 log，按「人 / 记录模块」过滤
# ══════════════════════════════════════════════════════════════════════════════

# 模块白名单与查询逻辑已下沉至 ConsoleLogRepo（repositories/console_log_repo.py）。


@router.get("/logs")
async def get_aggregated_logs(
    user_id: str = Query("", description="按用户过滤（操作人/归属人）"),
    module: str = Query("", description="记录模块 key，空=全部模块"),
    limit: int = Query(500, ge=1, le=2000, description="返回条数上限"),
    exclude_sources: str = Query(
        "", description="排除的流量性质，逗号分隔（如 test / test,ops）；空=不排除"),
):
    """聚合查看各类日志，可按「人」和「记录模块」过滤。

    exclude_sources：按流量性质排除（仅对带 source 列的模块生效，见操作留痕治理）。
    """
    from emily_core.repositories.console_log_repo import ConsoleLogRepo, LOG_MODULES

    log_keys = {m["key"] for m in LOG_MODULES}
    if module and module not in log_keys:
        return _err(f"未知记录模块：{module}")

    from emily_core.infrastructure.logging.audit import VALID_SOURCES

    exclude_list = [s.strip() for s in (exclude_sources or "").split(",") if s.strip()]
    unknown = [s for s in exclude_list if s not in VALID_SOURCES]
    if unknown:
        return _err(f"未知流量性质：{','.join(unknown)}")

    try:
        rows = await asyncio.to_thread(
            ConsoleLogRepo.query_aggregated, user_id, module, limit, exclude_list or None,
        )
    except Exception as ex:
        logger.exception("console.logs failed module=%s user=%s", module, user_id)
        return _err(f"读取日志失败：{ex}")
    return _ok({
        "user_id": user_id or None,
        "module": module or None,
        "exclude_sources": exclude_list,
        "modules": [
            {
                "key": m["key"],
                "label": m["label"],
                "user_filterable": bool(m["user"]),
                "source_filterable": bool(m.get("source")),
            }
            for m in LOG_MODULES
        ],
        "count": len(rows),
        "rows": rows,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  系统自检 —— 复用 self_check 统计 + tools_consistency 一致性检查
# ══════════════════════════════════════════════════════════════════════════════

class SelfCheckRequest(BaseModel):
    mode: str = "full"                # full | quick
    check_tool_registry: bool = True  # 仅 full 模式生效：是否连库查 tool_registry
    operator_id: str = ""             # 操作人（日志登记）


async def _run_self_check(mode: str, check_tool_registry: bool, operator_id: str) -> dict:
    """复用 scripts/self_check.py 的 self_check()（动作层自带留痕），此处只做报告裁剪。"""
    fn = _get_self_check_fn()
    if fn is None:
        raise RuntimeError("未找到 scripts/self_check.py")

    result = await asyncio.to_thread(
        fn, mode=mode, check_tool_registry=check_tool_registry, operator_id=operator_id,
    )

    report = {
        "checked_at": result.get("checked_at", ""),
        "mode": mode,
        "operator_id": operator_id,
        "stats": {
            "users": result.get("users", {}),
            "projects": result.get("projects", {}),
            "business": result.get("business", {}),
            "world_books": result.get("world_books", {}),
            "knowledge": result.get("knowledge", {}),
            "audit": result.get("audit", {}),
        },
        "tools_consistency": result.get("tools_consistency", {}),
    }

    return report


@router.post("/self-check")
async def run_self_check(req: SelfCheckRequest):
    """系统自检：统计 + 工具一致性检查（复用 self_check 脚本，以 operator_id 登记日志）。"""
    mode = (req.mode or "").strip() or "full"
    if mode not in ("full", "quick"):
        return _err("mode 仅支持 full / quick")
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    try:
        report = await _run_self_check(mode, bool(req.check_tool_registry), req.operator_id)
    except Exception as ex:
        logger.exception("console.self_check failed mode=%s", mode)
        return _err(f"自检失败：{ex}")
    return _ok(report)


# ══════════════════════════════════════════════════════════════════════════════
#  LLM 流量追踪 —— 读取 mitmproxy 落盘的 jsonl（与 /app/logs 共享卷）
# ══════════════════════════════════════════════════════════════════════════════

def _llm_trace_path() -> str:
    """LLM 流量 jsonl 路径（默认 /app/logs/llm_trace.jsonl，可经环境变量覆盖）。"""
    import os
    if os.environ.get("EMILY_LLM_TRACE_FILE"):
        return os.environ["EMILY_LLM_TRACE_FILE"]
    base = os.environ.get("LLM_TRACE_OUTPUT", "/app/logs/llm_trace")
    return base if base.endswith(".jsonl") else base + ".jsonl"


def _parse_maybe_json(v):
    """request_body/response_body 在 jsonl 里是 JSON 字符串，此处还原为对象。"""
    import json
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return None


def _format_trace_row(r: dict) -> dict:
    """抽取列表展示所需的元数据，同时保留 request/response 全文供详情展开。"""
    return {
        "seq": r.get("seq"),
        "timestamp": r.get("timestamp", ""),
        "model": r.get("model", ""),
        "messages_count": r.get("messages_count"),
        "finish_reason": r.get("finish_reason", ""),
        "usage": r.get("usage") or {},
        "request": _parse_maybe_json(r.get("request_body")),
        "response": _parse_maybe_json(r.get("response_body")),
    }


def _read_llm_trace(offset: int | None, limit: int) -> dict:
    """读 jsonl：offset=None 返回末尾最近 limit 条；offset>=0 返回 [offset:] 增量。"""
    import json
    from pathlib import Path

    path = Path(_llm_trace_path())
    if not path.exists():
        return {"available": False, "path": str(path), "total": 0,
                "next_offset": 0, "rows": []}

    lines: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as ex:
        logger.warning("console.llm_trace read failed path=%s: %s", path, ex)
        return {"available": False, "path": str(path), "total": 0,
                "next_offset": 0, "rows": []}

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        lines.append(obj)

    total = len(lines)
    if offset is None:
        rows = lines[-limit:] if limit > 0 else []
    else:
        rows = lines[offset:]
        if limit > 0 and len(rows) > limit:
            rows = rows[-limit:]

    return {
        "available": True,
        "path": str(path),
        "total": total,
        "next_offset": total,
        "rows": [_format_trace_row(r) for r in rows],
    }


@router.get("/llm-trace")
async def get_llm_trace(
    offset: int | None = Query(default=None, ge=0,
                               description="行偏移；None=返回末尾最近 limit 条"),
    limit: int = Query(50, ge=1, le=500, description="返回条数上限"),
):
    """LLM 流量追踪：读取 mitmproxy 落盘的 jsonl（增量轮询：offset=上次 next_offset）。"""
    try:
        data = await asyncio.to_thread(_read_llm_trace, offset, limit)
    except Exception as ex:
        logger.exception("console.llm_trace failed")
        return _err(f"读取 LLM 流量失败：{ex}")
    return _ok(data)


# ══════════════════════════════════════════════════════════════════════════════
#  会话池观测（原运维看板 /api/v1/monitor/sessions 的接续）
#  数据源：会话池内存态（SessionLoopPool / SessionPoolManager）+ messages 表（最近消息）
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  会话日志（只读观测）：会话索引（DB）+ 对话全文（md 文件）
#  归档由 SessionContext._persist_archive 写入：DB 存薄索引，正文落在 md 文件
#  注：原「会话池」观测端点（/session-pool 与 /session-pool/{id}/messages）已于
#      2026-09-15 退役——进行中会话已包含在下方索引（status=active）中，无需两套只读视图。
#  展示口径：列表**按 conversation 合并**（一个会话一行）；全文跨段合并读取。
# ══════════════════════════════════════════════════════════════════════════════

#: 截断原因中文文案（与前端 ARCHIVE_REASON_LABEL 保持一致；后端用于合并段落的分隔标题）
ARCHIVE_REASON_TEXT = {
    "expired": "TTL 截断",
    "terminated": "手动终止",
    "manual": "手动归档",
    "restart": "重启收口",
}

def _archive_dir():
    """归档 md 目录：优先复用 core 已实例化的 writer.archive_dir，否则三级回退。"""
    from pathlib import Path
    from api.server import get_core
    writer = getattr(get_core(), "_session_archive_writer", None)
    configured = getattr(writer, "archive_dir", "") if writer is not None else ""
    if configured and Path(configured).exists():
        return Path(configured)
    for c in (Path("/app/session_archives"),
              Path(__file__).resolve().parents[2] / "emily-data" / "session_archives"):
        if c.exists():
            return c
    return Path(configured) if configured else Path("/app/session_archives")


def _resolve_archive_file(stored_path: str, archive_dir) -> "object | None":
    """解析归档 md 路径；限定在归档目录内（防目录穿越），不存在返回 None。"""
    from pathlib import Path
    try:
        root = archive_dir.resolve()
    except Exception:  # noqa: BLE001
        return None
    candidates = []
    if stored_path:
        candidates.append(Path(stored_path))
        candidates.append(archive_dir / Path(stored_path).name)
    for c in candidates:
        try:
            if c.exists() and c.resolve().is_relative_to(root):
                return c
        except Exception:  # noqa: BLE001
            continue
    return None


@router.get("/session-archives")
def get_session_archives(limit: int = Query(default=200, ge=1, le=1000)):
    """列出**按会话合并**的会话日志（一个 conversation 一行），附带正文文件数与总大小。

    合并口径见 `SessionArchiveRepo.list_grouped_by_conversation`：索引在会话建立时即
    实时落库（进行中会话以 `status=active` 在列），同一 conversation 在截断/重启后再来
    消息会开启新段。

    轮次（turn_count）与正文大小**按去重后的归档文件**聚合：
      - 同一 conversation 同天重启会复用同一个 md 文件（`_path_for` 按"开始日期 + 姓名 + conv 前缀"
        命名），若按"段"累加会重复计数；
      - 跨天分段则各自成文件，轮次累加为全会话总轮次。
    """
    from emily_core.repositories.session_archive_repo import SessionArchiveRepo
    from emily_core.services.session_archive_writer import SessionArchiveWriter
    try:
        groups = SessionArchiveRepo.list_grouped_by_conversation(limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_archives failed")
        return _err(f"会话日志读取失败：{e}")
    adir = _archive_dir()
    for g in groups:
        files: list[str] = []
        turns = 0
        size = 0
        latest_file = None
        for seg in sorted(g.get("segments") or [],
                          key=lambda s: (str(s.get("started_at") or ""),
                                         str(s.get("last_active_at") or ""))):
            p = _resolve_archive_file(seg.get("md_file_path") or "", adir)
            if p is None or str(p) in files:
                continue
            files.append(str(p))
            latest_file = p
            size += p.stat().st_size
            try:
                turns += SessionArchiveWriter.count_turns(str(p)) or 0
            except Exception as e:  # noqa: BLE001
                logger.debug("count_turns failed for %s: %s", p, e)
        g["file_count"] = len(files)
        g["file_size"] = size
        g["has_content"] = bool(files)
        g["turn_count"] = turns
        # 展示用文件名取最新一段的正文文件（无正文时保留库里原值，供"无正文"提示）
        g["md_file_path"] = latest_file.name if latest_file is not None else (g.get("md_file_path") or "")
    return _ok({"rows": groups, "archive_dir": str(adir)})


@router.get("/session-archives/{archive_id}/content")
def get_session_archive_content(archive_id: str):
    """读取该**会话**的对话全文（md 原文，只读）。

    传任一段的 id 即可：内部展开为该 conversation 的**全部归档段**，
    按时间升序、**按文件去重**后拼接（同天重启复用同一文件 → 只读一次，不重复）。
    多段之间插入分隔标题，便于人工阅读。
    """
    from emily_core.repositories.session_archive_repo import SessionArchiveRepo
    from emily_core.services.session_archive_writer import SessionArchiveWriter
    try:
        row = SessionArchiveRepo.get_by_id(archive_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_archive.content failed")
        return _err(f"归档记录读取失败：{e}")
    if not row:
        return _err("归档记录不存在")

    conversation_id = str(row.get("conversation_id") or "")
    segments: list[dict] = [row]
    if conversation_id:
        try:
            all_segments = SessionArchiveRepo.list_by_conversation(conversation_id)
            if all_segments:
                segments = all_segments
        except Exception as e:  # noqa: BLE001
            logger.warning("list_by_conversation failed: %s — 退化为单段读取", e)

    adir = _archive_dir()
    parts: list[str] = []
    seen: list[str] = []
    turns = 0
    for seg in segments:
        p = _resolve_archive_file(seg.get("md_file_path") or "", adir)
        if p is None or str(p) in seen:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            logger.warning("归档正文读取失败 %s: %s", p, e)
            continue
        seen.append(str(p))
        try:
            turns += SessionArchiveWriter.count_turns(str(p)) or 0
        except Exception as e:  # noqa: BLE001
            logger.debug("count_turns failed for %s: %s", p, e)
        if parts:
            label = seg.get("archive_reason") or seg.get("status") or ""
            reason = ARCHIVE_REASON_TEXT.get(str(label), str(label))
            parts.append(
                f"\n\n{'═' * 30}\n【第 {len(parts) + 1} 段 · {reason} · {p.name}】\n{'═' * 30}\n"
            )
        parts.append(text)

    if not parts:
        return _err("归档正文文件不存在")
    content = "".join(parts)
    if len(content) > 400_000:
        content = content[:400_000] + "\n\n…（正文超长，已截断）"
    return _ok({
        "id": archive_id,
        "conversation_id": conversation_id,
        "user_name": row.get("user_name", ""),
        "platform": row.get("platform", ""),
        "im_user_id": row.get("im_user_id", ""),
        "is_guest": row.get("is_guest", False),
        "status": row.get("status", ""),
        "last_active_at": row.get("last_active_at", ""),
        "archived_at": row.get("archived_at", ""),
        "archive_reason": row.get("archive_reason", ""),
        "turn_count": turns,
        "file_name": Path(seen[-1]).name if seen else "",
        "file_count": len(seen),
        "segment_count": len(segments),
        "content": content,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  测试用例库（只读）：Issues/测试用例/*.md 清单 + 单条用例细节
#  用例以 md 表格承载（表头含「目标特性」/「用例 ID」），本模块解析该表逐条索引。
# ══════════════════════════════════════════════════════════════════════════════

def _test_cases_dir():
    """解析测试用例库目录：环境变量 → 容器挂载 → 仓库开发路径。"""
    import os
    from pathlib import Path

    env_dir = os.environ.get("EMILY_TEST_CASES_DIR")
    if env_dir and Path(env_dir).exists():
        return Path(env_dir)
    container = Path("/app/test_cases")
    if container.exists():
        return container
    return Path(__file__).resolve().parents[3] / "Issues" / "测试用例"


def _md_row_cells(line: str) -> list[str]:
    """拆一行 md 表格为单元格列表。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _md_is_separator(cells: list[str]) -> bool:
    """md 表格的分隔行（|---|---|）。"""
    return bool(cells) and all(c and set(c) <= set("-: ") for c in cells)


def _parse_case_tables(text: str) -> list[dict]:
    """解析 md 中所有「用例表」——表头含「目标特性」或「用例 ID」的表格。

    返回 [{"section": 所在二级标题, "header": [...], "rows": [[...], ...]}]，
    其余表格（历史结论 / 归因 / 实现锚点等）自动忽略。
    """
    tables: list[dict] = []
    section = ""
    header: list[str] | None = None
    rows: list[list[str]] = []
    section_at_header = ""

    def _flush():
        nonlocal header, rows, section_at_header
        if header is not None:
            tables.append({"section": section_at_header, "header": header, "rows": rows})
        header, rows = None, []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            _flush()
            section = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith("|"):
            _flush()
            continue
        cells = _md_row_cells(stripped)
        if _md_is_separator(cells):
            continue
        if header is None:
            header, section_at_header, rows = cells, section, []
        else:
            rows.append(cells)
    _flush()

    def _is_case_table(t: dict) -> bool:
        h = t["header"]
        return bool(h) and h[0] == "编号" and ("目标特性" in h or "用例 ID" in h)

    return [t for t in tables if _is_case_table(t)]


def _md_section(text: str, name: str) -> str:
    """取「## …{name}…」二级章节正文（到下一个一/二级标题为止）。"""
    out: list[str] = []
    capturing = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip()
            if capturing and level <= 2:
                break
            if not capturing and level == 2 and name in title:
                capturing = True
                continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _list_test_cases() -> dict:
    """扫描测试用例库，逐条索引全部用例（只读）。"""
    d = _test_cases_dir()
    cases: list[dict] = []
    files: list[dict] = []
    for p in sorted(d.glob("*.md")):
        if p.stem.lower() == "readme":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            logger.debug("test_cases read failed: %s — %s", p, e)
            continue
        count = 0
        for t in _parse_case_tables(text):
            header = t["header"]
            for row in t["rows"]:
                case_no = (row[0] if row else "").strip()
                if not case_no:
                    continue
                title = ""
                if "目标特性" in header:
                    i = header.index("目标特性")
                    title = row[i] if i < len(row) else ""
                elif "用例 ID" in header:
                    i = header.index("用例 ID")
                    title = row[i] if i < len(row) else ""
                cases.append({
                    "id": f"{p.stem}::{case_no}",
                    "case_no": case_no,
                    "title": title,
                    "file": p.name,
                    "file_stem": p.stem,
                    "section": t["section"],
                })
                count += 1
        files.append({"file": p.name, "file_stem": p.stem, "case_count": count})
    return {"count": len(cases), "rows": cases, "files": files, "dir": str(d)}


def _test_case_detail(case_id: str) -> dict:
    """单条用例细节：用例字段 + 所属文件的类别说明 / 前置 / 执行命令参考。"""
    from pathlib import Path

    case_id = (case_id or "").strip()
    if "::" not in case_id:
        return {"success": False, "error": "用例标识非法"}
    file_stem, case_no = case_id.split("::", 1)
    if (not file_stem or not case_no
            or file_stem in (".", "..") or case_no in (".", "..")
            or "/" in file_stem or "\\" in file_stem
            or "/" in case_no or "\\" in case_no):
        return {"success": False, "error": "用例标识非法"}

    root = _test_cases_dir().resolve()
    p = root / f"{file_stem}.md"
    try:
        if not p.exists() or not p.resolve().is_relative_to(root):
            return {"success": False, "error": "用例所属文件不存在"}
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.debug("test_case detail read failed: %s — %s", p, e)
        return {"success": False, "error": "用例所属文件不存在"}

    target = None
    for t in _parse_case_tables(text):
        for row in t["rows"]:
            if (row[0] if row else "").strip() != case_no:
                continue
            header = t["header"]
            target = {
                "case_no": case_no,
                "section": t["section"],
                "fields": [
                    {"label": header[i], "value": (row[i] if i < len(row) else "")}
                    for i in range(len(header))
                ],
            }
            break
        if target:
            break
    if target is None:
        return {"success": False, "error": "未在该文件中找到该用例"}

    # 类别说明：文件头部连续引用块（> 开头）
    overview_lines: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith(">"):
            overview_lines.append(line.strip().lstrip(">").strip())
        elif overview_lines:
            break

    target.update({
        "file": p.name,
        "file_stem": file_stem,
        "file_path": str(p),
        "overview": "\n".join(overview_lines),
        "precondition": _md_section(text, "前置"),
        "command": _md_section(text, "执行命令参考"),
    })
    return {"success": True, **target}


@router.get("/test-cases")
def get_test_cases():
    """测试用例库清单（只读）：Issues/测试用例/*.md 的用例表逐条索引。"""
    try:
        return _ok(_list_test_cases())
    except Exception as e:  # noqa: BLE001
        logger.exception("console.test_cases failed")
        return _err(f"测试用例库读取失败：{e}")


@router.get("/test-cases/{case_id}")
def get_test_case_detail(case_id: str):
    """单条测试用例细节（只读）。case_id 形如「01_会话主干与主循环::FR-A1」。"""
    try:
        res = _test_case_detail(case_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.test_case_detail failed")
        return _err(f"测试用例详情读取失败：{e}")
    if not res.get("success"):
        return _err(res.get("error", "测试用例详情读取失败"))
    return _ok(res)


# ══════════════════════════════════════════════════════════════════════════════
#  MCP 配置管理（读写 mcp_servers.json + 在线探测）
# ══════════════════════════════════════════════════════════════════════════════

def _mcp_cfg_path():
    """MCP 配置文件路径（复用 mcp 扩展层的 resolve_config_path）。"""
    from pathlib import Path
    from emily_core.mcp.config import resolve_config_path
    p = resolve_config_path()
    return p or Path("/app/config/mcp_servers.json")


def _load_mcp_config():
    from emily_core.mcp.config import load_config
    return load_config(_mcp_cfg_path())


def _save_mcp_config(cfg) -> None:
    import json
    path = _mcp_cfg_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _server_to_dict(sc) -> dict:
    """把 McpServerConfig 序列化为前端友好的 dict。"""
    return {
        "name": sc.name,
        "description": sc.description,
        "enabled": sc.enabled,
        "transport": sc.transport,
        "command": sc.command,
        "args": list(sc.args),
        "env": dict(sc.env),
        "cwd": sc.cwd,
        "url": sc.url,
        "headers": dict(sc.headers),
        "tool_prefix": sc.tool_prefix,
        "category": sc.category,
        "permission_flag": sc.permission_flag,
        "write_mode": sc.write_mode,
        "timeout_seconds": sc.timeout_seconds,
    }


@router.get("/mcp/servers")
def get_mcp_servers():
    """返回当前 MCP Server 配置清单（不探测在线状态）。"""
    cfg = _load_mcp_config()
    return _ok({
        "servers": [_server_to_dict(sc) for sc in cfg.servers],
        "config_path": str(_mcp_cfg_path()),
    })


class McpServerUpsert(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = {}
    tool_prefix: str = ""
    category: str = "base"
    permission_flag: str = "all"
    write_mode: str = "read"
    timeout_seconds: float = 30.0


@router.post("/mcp/server")
def upsert_mcp_server(req: McpServerUpsert):
    """新增或更新一个 MCP Server（按 name upsert）。"""
    from emily_core.mcp.config import McpServerConfig
    cfg = _load_mcp_config()
    try:
        sc = McpServerConfig(**req.model_dump())
    except Exception as ex:  # noqa: BLE001
        return _err(f"配置校验失败：{ex}")
    idx = next((i for i, s in enumerate(cfg.servers) if s.name == sc.name), None)
    action = "updated" if idx is not None else "created"
    if idx is not None:
        cfg.servers[idx] = sc
    else:
        cfg.servers.append(sc)
    _save_mcp_config(cfg)
    return _ok({"saved": True, "action": action, "name": sc.name,
                "note": "配置已保存，重启 emily-core 后生效"})


@router.delete("/mcp/server")
def delete_mcp_server(name: str = Query(..., description="要删除的 server 名称")):
    cfg = _load_mcp_config()
    before = len(cfg.servers)
    cfg.servers = [s for s in cfg.servers if s.name != name]
    if len(cfg.servers) == before:
        return _err(f"MCP server '{name}' 不存在")
    _save_mcp_config(cfg)
    return _ok({"deleted": True, "name": name,
                "note": "配置已删除，重启 emily-core 后生效"})


class McpToggle(BaseModel):
    name: str
    enabled: bool


@router.post("/mcp/toggle")
def toggle_mcp_server(req: McpToggle):
    cfg = _load_mcp_config()
    for s in cfg.servers:
        if s.name == req.name:
            s.enabled = req.enabled
            _save_mcp_config(cfg)
            return _ok({"toggled": True, "name": s.name, "enabled": s.enabled,
                        "note": "已保存，重启 emily-core 后生效"})
    return _err(f"MCP server '{req.name}' 不存在")


@router.post("/mcp/probe")
def probe_mcp_server(name: str = Query(..., description="要探测的 server 名称")):
    """即时连接 MCP Server 并列出工具，返回在线状态。"""
    from emily_core.mcp.manager import probe_server
    cfg = _load_mcp_config()
    for s in cfg.servers:
        if s.name == name:
            result = probe_server(s)
            return _ok({"name": s.name, **result})
    return _err(f"MCP server '{name}' 不存在")


# ══════════════════════════════════════════════════════════════════════════════
#  LangGraph 工具 —— 展示执行引擎注册的 BaseTool 与 tool_node
# ══════════════════════════════════════════════════════════════════════════════

# tool_node 是 graph.py 注册的图节点（执行体在 agent/loop.py），此处展示其静态元信息。
_TOOL_NODE_META = {
    "name": "tool_node",
    "graph_source": "emily-core/emily_core/workitem/langgraph_engine/graph.py",
    "loop_source": "emily-core/emily_core/workitem/langgraph_engine/agent/loop.py",
    "description": (
        "执行 agent_node 暂存的 pending tool_call：调 handler 后将结果追加为 "
        "tool_result message + StepResult；ask_user 走 interrupt 挂起（WAITING_FOR_INPUT）。"
    ),
    "interrupt_tools": ["ask_user"],
    "routes": [
        {"condition": "complete_work 已调用（wi_state=summarizing）", "target": "summarizing"},
        {"condition": "其余情况", "target": "agent_node（继续 ReAct 循环）"},
    ],
}


@router.get("/langgraph-tools")
async def get_langgraph_tools():
    """展示 LangGraph 执行引擎中注册的 BaseTool（BusinessFlowTool 注册表）与 tool_node 节点。"""
    try:
        from api.server import get_core
        core = get_core()
    except Exception as ex:  # noqa: BLE001
        return _err(f"获取内核失败：{ex}")

    # ── BaseTool 注册表（BusinessFlowTool：base / business / project 三类）──
    tools: list[dict] = []
    categories: dict[str, int] = {}
    reg = getattr(core, "_business_flow_tools", None)
    if reg is not None:
        for t in reg._tools.values():
            cat = getattr(t, "category", "") or "base"
            categories[cat] = categories.get(cat, 0) + 1
            params = t.parameters if isinstance(t.parameters, dict) else {}
            props = params.get("properties", {}) or {}
            tools.append({
                "name": t.name,
                "description": t.description,
                "category": cat,
                "permission": getattr(t, "permission_flag", "all"),
                "write_mode": getattr(t, "write_mode", "read"),
                "has_schema": bool(props),
                "param_count": len(props),
            })
    tools.sort(key=lambda x: (x["category"], x["name"]))

    # ── 参数解析器（作为 function-calling tool 暴露，第二层权限）──
    resolvers: list[dict] = []
    rreg = getattr(core, "_resolvers", None)
    if rreg is not None:
        for r in rreg.list_all():
            spec = r.spec.get("function", {}) if isinstance(r.spec, dict) else {}
            resolvers.append({"name": r.name, "description": spec.get("description", "")})

    # ── 控制工具（complete_work / ask_user，绕过权限过滤直接追加给 LLM）──
    from emily_core.workitem.langgraph_engine.agent.control_tools import CONTROL_TOOL_SPECS
    control_tools = [
        {"name": s["function"]["name"], "description": s["function"].get("description", "")}
        for s in CONTROL_TOOL_SPECS
    ]

    return _ok({
        "tool_node": _TOOL_NODE_META,
        "tools": tools,
        "categories": categories,
        "resolvers": resolvers,
        "control_tools": control_tools,
        "counts": {
            "tools": len(tools),
            "resolvers": len(resolvers),
            "control": len(control_tools),
        },
    })


# ── 测试会话设置（左侧栏顶部：测试前缀 + 交互通道）──

class TestSettingsIn(BaseModel):
    """测试会话设置请求体。"""
    test_prefixes: list[str] = Field(default_factory=list)
    interaction_channel: str = ""


@router.get("/test-settings")
async def get_test_settings():
    """读取测试前缀与交互通道。

    测试前缀：会话 ID 命中任前缀 → 判定为测试会话，其出站只走 SSE、不外发真实 IM；
    留空 = 不做前缀判定（通道账号能对上真实用户时回复照常发真实 IM）。
    交互通道：测试注入（消息模拟器 / emy-test / 对话面板）默认使用的渠道。
    """
    try:
        from api.server import get_core
        from emily_core.services.test_settings import describe
        state = describe(getattr(get_core(), "config", None))
    except Exception as ex:  # noqa: BLE001
        return _err(f"读取测试会话设置失败：{ex}")
    return _ok(state)


@router.post("/test-settings")
async def set_test_settings(req: TestSettingsIn):
    """保存测试前缀与交互通道（写入 /app/runtime/test_settings.json，立即生效）。"""
    try:
        from api.server import get_core
        from emily_core.services.test_settings import describe, save_settings
        save_settings(req.test_prefixes, req.interaction_channel)
        state = describe(getattr(get_core(), "config", None))
    except Exception as ex:  # noqa: BLE001
        return _err(f"保存测试会话设置失败：{ex}")
    prefixes = state.get("test_prefixes") or []
    message = (
        f"已保存：测试前缀 {('、'.join(prefixes)) if prefixes else '（空＝不拦截，按真实投递）'}；"
        f"交互通道 {state.get('interaction_channel') or '未指定'}"
    )
    return _ok({"state": state, "message": message})


@router.get("/channels")
async def get_channels():
    """接入渠道连通性（QQ / 企业微信 / 微信小程序 / 邮箱）—— emy-console「模块能力」只读展示。

    渠道判定口径（实现见 emily_core/services/channel_status_service.py）：
      - QQ        NapCat 容器 running 且日志中解析出已登录 QQ 号
      - 企业微信  AstrBot 容器 running 且 wecom 适配器 enable 且日志无凭证错误
      - 微信小程序 EMILY_WXMP_DOMAIN 已配置且 <域名>/health 可达
      - 邮箱      IMAP 登录成功 且 收到自发的「系统启动完成」报告邮件

    每个渠道附带 details（详情键值对）与 qrcode（QQ 未登录时的扫码二维码）。
    """
    try:
        from emily_core.services.channel_status_service import get_access_channels
        channels = await get_access_channels()
    except Exception as ex:  # noqa: BLE001
        return _err(f"读取渠道连通性失败：{ex}")
    return _ok({"channels": channels})


@router.get("/channels/qq-qrcode")
async def get_qq_qrcode():
    """重新搜索 NapCat 容器内时间最近的 QQ 登录二维码（前端点击二维码时调用）。

    每次调用都会在容器内按文件生成时间重新搜索，返回最新一张二维码（data URL）。
    """
    try:
        from emily_core.services.channel_status_service import get_qq_qrcode as _get_qq_qrcode
        data = await _get_qq_qrcode()
    except Exception as ex:  # noqa: BLE001
        return _err(f"获取 QQ 登录二维码失败：{ex}")
    return _ok(data)


class ChannelParamsUpdate(BaseModel):
    """渠道参数更新请求体（控制台「点击参数值 → 弹窗替换」的统一提交格式）。

    - ``params``：要替换的字段值；密文字段不回显，留空/不传＝保持原值
    - ``clear``：要清空的字段名（仅 clearable 字段，如小程序关联域名、企微客服账号）
    """
    params: dict[str, str] = {}
    clear: list[str] = []


class WebuiCredentialUpdate(BaseModel):
    """AstrBot WebUI 凭据记录（控制台「渠道连通性」头部工具组保存）。

    密码为明文，只落在运行时目录（emily-data/runtime，compose 里读写挂载、不入 git）；
    留空表示只记录备注，不动已存的密码。
    ``operator`` 为用户 UUID（与其他控制台动作一致），``operator_label`` 为可读署名。
    """
    username: str = "astrbot"
    password: str = ""
    webui_url: str = ""
    note: str = ""
    operator: str = ""
    operator_label: str = ""


class WebuiCredentialVerify(BaseModel):
    """WebUI 登录实测请求体（拿记录里的账号密码打一次 AstrBot 登录接口）。"""
    username: str = "astrbot"
    password: str = ""


@router.get("/webui-credentials")
async def get_webui_credentials():
    """AstrBot WebUI 凭据速查：实时状态 + 控制台留存记录 + 新机部署/重置文案。

    客户端密码在 AstrBot 侧只存 pbkdf2（旧部署 md5）哈希，**无法从配置还原明文**，
    且随机初始密码只在「生成密码那一次」的启动日志里打印一次。故此处只回状态：
      - ``password_state``：unset（尚未设置）／initial（生成后未改）／
        builtin_default（仍是内置默认密码）／set（已改过）
      - ``record``：操作人自己留在运行时的密码与经过（read-only 回显，密码打码由前端处理）
      - ``reference``：新机部署 env 片段与忘记后的重置步骤，供一键复制
    """
    try:
        from emily_core.services.channel_status_service import get_webui_credentials as _get
        data = await _get()
    except Exception as ex:  # noqa: BLE001
        return _err(f"读取 WebUI 凭据状态失败：{ex}")
    return _ok(data)


@router.post("/webui-credentials")
async def set_webui_credentials(req: WebuiCredentialUpdate):
    """保存 AstrBot WebUI 凭据记录（写入 /app/runtime/deploy_credentials.json）。"""
    try:
        from emily_core.services.channel_status_service import save_webui_credentials as _save
        data = await _save(req.model_dump())
    except ValueError as ex:
        return _err(str(ex))
    except Exception as ex:  # noqa: BLE001
        return _err(f"保存 WebUI 凭据记录失败：{ex}")
    return _ok(data)


@router.post("/webui-credentials/verify")
async def verify_webui_credentials(req: WebuiCredentialVerify):
    """用给定账号密码实测一次 AstrBot WebUI 登录（只证明密码是否有效，不修改任何配置）。

    失败原因可能是密码错，也可能是撞上 WebUI 限流（auth_rate_limit 默认 1 QPS / 突发 3），
    故把 AstrBot 的原始 message 一并回给前端。
    """
    try:
        from emily_core.services.channel_status_service import verify_webui_login as _verify
        data = await _verify(req.username, req.password)
    except Exception as ex:  # noqa: BLE001
        return _err(f"WebUI 登录实测失败：{ex}")
    return _ok(data)


@router.post("/channels/wxmp-domain")
async def set_wxmp_domain(req: ChannelParamsUpdate):
    """录入 / 清除微信小程序渠道的关联域名。

    写入控制台覆盖配置（emily-data/runtime/channel_overrides.json，容器内 /app/runtime），
    优先于环境变量 EMILY_WXMP_DOMAIN；清空该字段即清除覆盖、回落到环境变量。
    """
    cleared = "domain" in req.clear
    domain = "" if cleared else req.params.get("domain", "")
    try:
        from emily_core.services.channel_status_service import set_wxmp_domain as _set_wxmp_domain
        channel = await _set_wxmp_domain(domain)
    except ValueError as ex:
        return _err(str(ex))
    except Exception as ex:  # noqa: BLE001
        return _err(f"保存关联域名失败：{ex}")
    message = "已清除覆盖（回落到环境变量）" if cleared else "已保存并生效"
    return _ok({"channel": channel, "message": message})


@router.post("/channels/wecom-params")
async def set_wecom_params(req: ChannelParamsUpdate):
    """录入 / 清空企业微信关键参数（企业 ID / Secret / 回调 Token / 加密密钥 / 客服账号）。

    写入 AstrBot 配置（容器内 /AstrBot/data/cmd_config.json 的 wecom 适配器，写前备份
    为 cmd_config.json.bak），随后重启 AstrBot 容器使其生效；密文字段留空表示保持原值。
    返回 saved / cleared（实际改动的字段）与 message，不返回渠道快照
    —— 重启后适配器状态需数秒才稳定，前端据此延时刷新。
    """
    try:
        from emily_core.services.channel_status_service import set_wecom_params as _set_wecom_params
        data = await _set_wecom_params(req.params, req.clear)
    except ValueError as ex:
        return _err(str(ex))
    except Exception as ex:  # noqa: BLE001
        return _err(f"保存企业微信参数失败：{ex}")
    return _ok(data)


# ══════════════════════════════════════════════════════════════════════════════
#  与 Emily 对话 —— 模拟接入渠道（QQ / 微信客服 / 微信小程序）的直接对话通道
#
#  入站消息不走上层薄插件，直接进程内投递给 EmilyCore.handle_message：
#    · 短路同步回复   → 直接以 reply 事件流式返回
#    · 异步处理（204）→ 订阅 outbound_bus，把本次会话的 progress / reply /
#                       file_send 事件按 SSE 帧持续推给控制台
#  发送者统一取控制台左侧全局「操作人」，会话按「渠道:用户」隔离。
# ══════════════════════════════════════════════════════════════════════════════

# 聊天附件暂存目录（容器 /app/runtime/chat_uploads，开发态 emily-data/runtime/chat_uploads）
_CHAT_UPLOAD_SUBDIR = ("/app/runtime/chat_uploads", "emily-data/runtime/chat_uploads")

# 等待异步回复的上限（秒）：Agent 多轮推理耗时较长，与插件侧口径一致
_CHAT_REPLY_TIMEOUT = 180.0

# 支持模拟接入的渠道（platform 取值与各渠道薄插件一致）
CHAT_PLATFORMS = {"napcat": "QQ", "wecom": "微信客服", "wxmp": "微信小程序"}


def _chat_upload_dir():
    """聊天附件暂存目录（按三级探测解析，父目录按需创建）。"""
    from pathlib import Path

    from emily_core.infrastructure.paths import resolve_data_path

    path = Path(resolve_data_path("", _CHAT_UPLOAD_SUBDIR[0], _CHAT_UPLOAD_SUBDIR[1]))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chat_loopback_base() -> str:
    """Core 自取附件的回环地址（本地 HTTP，绕过外部代理直连自身）。"""
    import os

    base = os.environ.get("EMILY_CHAT_LOOPBACK", "").strip()
    return (base or "http://127.0.0.1:18080").rstrip("/")


def _chat_attachment_type(filename: str) -> int:
    """按扩展名推断附件类型：2=图片 3=文件 4=语音 5=视频。"""
    from pathlib import Path

    ext = Path(filename).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return 2
    if ext in (".mp3", ".wav", ".ogg", ".aac", ".m4a"):
        return 4
    if ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        return 5
    return 3


def _sse_frame(event: str, data: dict) -> str:
    """构造一个 SSE 帧。"""
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/upload")
async def chat_upload_file(
    user_id: str = Form(""),
    file: UploadFile = File(...),
):
    """暂存对话附件并返回可被 Core 拉取的地址（模拟 IM 文件消息的 URL）。"""
    import uuid
    from pathlib import Path

    if not user_id:
        return _err("请先选择操作人")
    data = await file.read()
    if not data:
        return _err("文件内容为空")

    token = uuid.uuid4().hex
    filename = file.filename or "file"
    local_path = _chat_upload_dir() / f"{token}_{Path(filename).name}"
    try:
        await asyncio.to_thread(local_path.write_bytes, data)
    except Exception as ex:  # noqa: BLE001
        logger.exception("console.chat_upload failed user_id=%s", user_id)
        return _err(f"暂存附件失败：{ex}")

    return _ok({
        "token": token,
        "url": f"{_chat_loopback_base()}/api/v1/console/chat/attachment/{token}",
        "file_name": filename,
        "type": _chat_attachment_type(filename),
        "size": len(data),
    })


@router.get("/chat/attachment/{token}")
async def chat_attachment(token: str):
    """向 Core 提供暂存附件（token 为 32 位十六进制，杜绝路径穿越）。"""
    import re

    from fastapi.responses import FileResponse

    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return _err("非法附件标识")
    matches = list(_chat_upload_dir().glob(f"{token}_*"))
    if not matches:
        return _err("附件不存在或已过期")
    return FileResponse(str(matches[0]), filename=matches[0].name)


class ChatSendRequest(BaseModel):
    """对话发送请求体。"""

    message: str = ""
    user_id: str = ""
    platform: str = "napcat"
    conversation_id: str = ""
    attachments: list[dict] = Field(default_factory=list)


def _chat_event_matches(event_type: str, data: dict, cid: str, msg_id: str) -> bool:
    """判断出站事件是否属于本次对话（避免多路并发串台）。"""
    dcid = str(data.get("conversation_id") or "")
    if event_type == "reply":
        return dcid == cid or data.get("reply_to_message_id") == msg_id
    if event_type == "file_send":
        return dcid == cid
    if event_type == "progress":
        # 部分前导消息未携带会话号，控制台为单人使用，按空会话号容忍
        return (not dcid) or dcid == cid
    return False


@router.post("/chat/send")
async def chat_send(req: ChatSendRequest):
    """向 Emily 发送一条消息，并以 SSE 帧流式返回前导消息 / 回复 / 发送文件。"""
    import uuid

    from fastapi.responses import StreamingResponse

    if not req.user_id:
        return _err("请先选择操作人")
    if not (req.message.strip() or req.attachments):
        return _err("请输入消息或添加附件")
    if req.platform not in CHAT_PLATFORMS:
        return _err(f"不支持的接入渠道：{req.platform}")

    from api.server import get_core

    try:
        core = get_core()
    except Exception as ex:  # noqa: BLE001
        return _err(f"Emily 内核未就绪：{ex}")

    from emily_core.adapters.standard.message import StandardMessage

    cid = req.conversation_id or f"{req.platform}:{req.user_id}"
    msg_id = f"console_chat_{uuid.uuid4().hex[:12]}"
    event_id = f"console_chat_{uuid.uuid4().hex[:12]}"

    sender_name = req.user_id
    try:
        from emily_core.repositories.user_repo import UserRepository

        user = UserRepository.get_by_id(req.user_id)
        if user and user.username:
            sender_name = user.username
    except Exception as ex:  # noqa: BLE001
        logger.debug("chat_send: load user failed: %s", ex)

    attachments = [a for a in req.attachments if isinstance(a, dict)]
    msg_type = 1
    if attachments:
        first = attachments[0].get("type", 3)
        msg_type = first if first in (2, 3, 4, 5) else 3

    message = StandardMessage(
        message_id=msg_id,
        platform=req.platform,
        conversation_type="private",
        conversation_id=cid,
        sender_id=req.user_id,
        sender_name=sender_name,
        content=req.message,
        is_at_bot=False,
        msg_type=msg_type,
        attachments=attachments,
        event_id=event_id,
    )

    bus = core.outbound_bus
    queue = bus.subscribe()

    async def event_stream():
        import asyncio as _asyncio
        import time as _time

        try:
            yield _sse_frame("start", {
                "conversation_id": cid,
                "platform": req.platform,
                "platform_label": CHAT_PLATFORMS[req.platform],
                "sender_name": sender_name,
            })

            # 留痕上下文：console 模拟对话属测试流量（覆盖中间件注入的 ops）
            # 走的是真实 IM 入口，产物与真实消息同构，故必须显式标记 source=test
            from emily_core.infrastructure.logging.audit import (
                SOURCE_TEST,
                bind_audit_context,
            )

            bind_audit_context(source=SOURCE_TEST, override=True)

            task = _asyncio.ensure_future(core.handle_message(message, event_id=event_id))
            waiter = task
            deadline = _time.monotonic() + _CHAT_REPLY_TIMEOUT
            last_beat = _time.monotonic()
            got_reply = False

            while not got_reply:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    yield _sse_frame("timeout", {"conversation_id": cid})
                    break

                # 保活：长时间无事件时每 15s 送一个 SSE 注释帧，避免链路静默超时
                now = _time.monotonic()
                if now - last_beat >= 15.0:
                    last_beat = now
                    yield ": keep-alive\n\n"

                getter = _asyncio.ensure_future(queue.get())
                watchers = {getter} if waiter is None else {getter, waiter}
                done, _pending = await _asyncio.wait(
                    watchers, timeout=min(remaining, 1.0), return_when=_asyncio.FIRST_COMPLETED,
                )
                if getter not in done:
                    getter.cancel()

                if waiter is not None and waiter in done:
                    waiter = None
                    try:
                        reply = task.result()
                    except Exception as ex:  # noqa: BLE001
                        logger.exception("console.chat_send handle_message failed")
                        yield _sse_frame("error", {"message": f"处理失败：{ex}"})
                        break
                    if reply is not None:
                        yield _sse_frame("reply", {
                            "conversation_id": reply.conversation_id,
                            "content": reply.content,
                        })
                        got_reply = True
                        break

                if getter in done:
                    event = getter.result()
                    etype = event.get("type", "message")
                    data = event.get("data") or {}
                    if _chat_event_matches(etype, data, cid, msg_id):
                        yield _sse_frame(etype, data)
                        if etype == "reply":
                            got_reply = True
                            break

            yield _sse_frame("done", {"conversation_id": cid})
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ══════════════════════════════════════════════════════════════════════════════
#  人员管理 —— 只读观察 + 写能力复用 PersonnelService（能力层，零业务逻辑）
# ══════════════════════════════════════════════════════════════════════════════

class PersonnelLevelRequest(BaseModel):
    operator_id: str = ""
    target_user_id: str = ""
    new_level: int = 0
    reason: str = ""


class PersonnelCompanyRequest(BaseModel):
    operator_id: str = ""
    target_user_id: str = ""
    company_id: str = ""
    reason: str = ""


class CompanyCreateRequest(BaseModel):
    operator_id: str = ""
    name: str = ""


class CompanyDeleteRequest(BaseModel):
    operator_id: str = ""
    company_id: str = ""


class PersonnelPromptRequest(BaseModel):
    user_id: str = ""


def _get_user_memory_service():
    """复用 core 已注入的 UserMemoryService（同源同能力）。"""
    from api.server import get_core

    core = get_core()
    mem = getattr(core, "_user_memory_service", None)
    if mem is not None:
        return mem
    from emily_core.services.user_memory_service import UserMemoryService
    return UserMemoryService()


@router.get("/personnel/list")
async def personnel_list(limit: int = Query(_LIST_LIMIT, ge=1, le=_LIST_LIMIT)):
    """人员台账五列列表（只读，无副作用）。"""
    from emily_core.services.personnel_service import PersonnelService

    try:
        rows = await asyncio.to_thread(PersonnelService.list_personnel, limit=limit)
    except Exception as ex:
        logger.exception("console.personnel_list failed")
        return _err(f"读取人员列表失败：{ex}")
    return _ok({"personnel": rows, "truncated": len(rows) >= limit})


@router.get("/personnel/companies")
async def personnel_companies():
    """企业清单（含在职归属人数，只读）。"""
    from emily_core.services.personnel_service import PersonnelService

    try:
        rows = await asyncio.to_thread(PersonnelService.list_companies)
    except Exception as ex:
        logger.exception("console.personnel_companies failed")
        return _err(f"读取企业清单失败：{ex}")
    return _ok({"companies": rows})


@router.get("/personnel/memory")
async def personnel_memory(user_id: str = Query("", description="目标人员 ID")):
    """按人员查看长期记忆条目（只读，与注入同源）。"""
    if not user_id:
        return _err("缺少 user_id")
    from emily_core.services.personnel_service import PersonnelService

    try:
        username = await asyncio.to_thread(PersonnelService.get_username, user_id)
        if not username:
            return _err("人员不存在或无用户名")
        mem = _get_user_memory_service()
        result = await asyncio.to_thread(mem.list_entries, username)
    except Exception as ex:
        logger.exception("console.personnel_memory failed user=%s", user_id)
        return _err(f"读取记忆失败：{ex}")
    return _ok(result)


@router.post("/personnel/level")
async def personnel_level(req: PersonnelLevelRequest):
    """人员等级调整（复用 PersonnelService，授权/校验/留痕均在能力层）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    if not req.target_user_id:
        return _err("缺少目标人员")
    from emily_core.services.personnel_service import PersonnelService

    try:
        res = await asyncio.to_thread(
            PersonnelService.adjust_user_level,
            req.operator_id, req.target_user_id, req.new_level, req.reason,
        )
    except Exception as ex:
        logger.exception("console.personnel_level failed target=%s", req.target_user_id)
        return _err(f"操作失败：{ex}")
    if not res.get("success"):
        return _err(res.get("message") or "操作失败")
    return _ok(res.get("data") or {})


@router.post("/personnel/company")
async def personnel_company(req: PersonnelCompanyRequest):
    """人员企业归属调整（复用 PersonnelService）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    if not req.target_user_id:
        return _err("缺少目标人员")
    from emily_core.services.personnel_service import PersonnelService

    try:
        res = await asyncio.to_thread(
            PersonnelService.adjust_user_company,
            req.operator_id, req.target_user_id, req.company_id, req.reason,
        )
    except Exception as ex:
        logger.exception("console.personnel_company failed target=%s", req.target_user_id)
        return _err(f"操作失败：{ex}")
    if not res.get("success"):
        return _err(res.get("message") or "操作失败")
    return _ok(res.get("data") or {})


@router.post("/personnel/company-create")
async def personnel_company_create(req: CompanyCreateRequest):
    """新增企业（复用 PersonnelService）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    from emily_core.services.personnel_service import PersonnelService

    try:
        res = await asyncio.to_thread(
            PersonnelService.create_company, req.operator_id, req.name,
        )
    except Exception as ex:
        logger.exception("console.personnel_company_create failed")
        return _err(f"操作失败：{ex}")
    if not res.get("success"):
        return _err(res.get("message") or "操作失败")
    return _ok(res.get("data") or {})


@router.post("/personnel/company-delete")
async def personnel_company_delete(req: CompanyDeleteRequest):
    """删除企业（复用 PersonnelService，含在职人数前置判定）。"""
    if not req.operator_id:
        return _err("请选择操作人")
    from emily_core.services.personnel_service import PersonnelService

    try:
        res = await asyncio.to_thread(
            PersonnelService.delete_company, req.operator_id, req.company_id,
        )
    except Exception as ex:
        logger.exception("console.personnel_company_delete failed company=%s", req.company_id)
        return _err(f"操作失败：{ex}")
    if not res.get("success"):
        return _err(res.get("message") or "操作失败")
    return _ok(res.get("data") or {})


@router.post("/personnel/prompt")
async def personnel_prompt(req: PersonnelPromptRequest):
    """一键生成会话拉起提示词（只读，无副作用）。"""
    if not req.user_id:
        return _err("缺少 user_id")
    from api.server import get_core
    from emily_core.services.session_prompt_service import SessionPromptService

    try:
        core = get_core()
        res = await asyncio.to_thread(SessionPromptService.generate, req.user_id, core)
    except Exception as ex:
        logger.exception("console.personnel_prompt failed user=%s", req.user_id)
        return _err(f"生成失败：{ex}")
    if not res.get("success"):
        return _err(res.get("reason_code") or "生成失败")
    return _ok({"prompt": res.get("prompt", ""), "meta": res.get("meta", {})})
