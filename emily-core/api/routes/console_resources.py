"""emy-console 资源清单 API —— 四组资源 + 按用户权限过滤。

四组资源：
  1. files    项目文件（files 表）
  2. nodes    全景节点（project_nodes 表）
  3. sops     SOP（sop_business_flows 表）
  4. rag_files RAG 已收录文件（knowledge_chunks 按 doc_id 去重）

可见性规则（与系统其余部分保持一致）：
  - files     → VisibleFileSetResolver（①自传 ∪ ②公开 ∪ ③节点可见∩密级 ∪ ④显式授权）
  - nodes     → 权限快照 authorized_node_ids
  - sops      → 权限快照 sop_allow
  - rag_files → doc_id ∈ 可见文件集合（doc_id 锚定 files.id）

user_id 为空时返回全量（"默认展示所有文件"），传入 user_id 时按上述规则过滤。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import func

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

def _list_files() -> list[dict]:
    from emily_core.infrastructure.database.models import File, Project
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(
                File.id.label("id"),
                File.filename.label("name"),
                File.file_no.label("file_no"),
                File.file_category.label("category"),
                File.confidentiality.label("confidentiality"),
                File.project_id.label("project_id"),
                Project.name.label("project_name"),
            )
            .outerjoin(Project, Project.id == File.project_id)
            .filter(File.is_deleted.isnot(True))
            .order_by(File.created_at.desc())
            .limit(_LIST_LIMIT)
            .all()
        )
    return [
        {
            "id": r.id,
            "name": r.name,
            "file_no": r.file_no,
            "category": r.category or "",
            "confidentiality": r.confidentiality or 0,
            "project_id": r.project_id or "",
            "project_name": r.project_name or "",
        }
        for r in rows
    ]


def _list_nodes() -> list[dict]:
    from emily_core.infrastructure.database.models import Project, ProjectNode
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(
                ProjectNode.node_id.label("node_id"),
                ProjectNode.node_name.label("name"),
                ProjectNode.project_id.label("project_id"),
                ProjectNode.status.label("status"),
                ProjectNode.node_type.label("node_type"),
                Project.name.label("project_name"),
            )
            .outerjoin(Project, Project.id == ProjectNode.project_id)
            .filter(ProjectNode.is_discarded.isnot(True))
            .order_by(ProjectNode.project_id, ProjectNode.node_id)
            .limit(_LIST_LIMIT)
            .all()
        )
    return [
        {
            "node_id": r.node_id,
            "name": r.name,
            "project_id": r.project_id or "",
            "project_name": r.project_name or "",
            "status": r.status or "",
            "node_type": r.node_type or "",
        }
        for r in rows
    ]


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


def _list_rag_files() -> list[dict]:
    from emily_core.infrastructure.database.models import KnowledgeChunk
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        rows = (
            session.query(
                KnowledgeChunk.doc_id.label("doc_id"),
                KnowledgeChunk.doc_name.label("name"),
                func.count(KnowledgeChunk.id).label("chunk_count"),
                func.max(KnowledgeChunk.ingest_status).label("status"),
            )
            .group_by(KnowledgeChunk.doc_id, KnowledgeChunk.doc_name)
            .order_by(KnowledgeChunk.doc_name)
            .limit(_LIST_LIMIT)
            .all()
        )
    return [
        {
            "doc_id": r.doc_id,
            "name": r.name or r.doc_id,
            "chunk_count": int(r.chunk_count or 0),
            "status": r.status or "",
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
            "project_name": n.project_name or "",
            "responsible_user_id": n.responsible_user_id or "",
            "participants": participants_map.get(n.node_id, []),
            "shared_files": files_map.get(n.node_id, []),
        }
        for n in nodes
    ]


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


@router.get("/resources")
async def get_resources(user_id: str = Query("", description="用户 ID，空=返回全量")):
    """四组资源清单；传入 user_id 时按该用户权限过滤可见范围。"""
    try:
        files, nodes, sops, rag_files = await asyncio.to_thread(
            lambda: (_list_files(), _list_nodes(), _list_sops(), _list_rag_files())
        )

        scope = "all"
        if user_id:
            scope = "user"
            visible_file_ids, visible_node_ids, visible_sop_ids = await asyncio.to_thread(
                _build_visible_sets, user_id
            )
            files = [f for f in files if f["id"] in visible_file_ids]
            nodes = [n for n in nodes if n["node_id"] in visible_node_ids]
            sops = [s for s in sops if s["sop_id"] in visible_sop_ids]
            rag_files = [r for r in rag_files if r["doc_id"] in visible_file_ids]

        return _ok({
            "user_id": user_id or None,
            "scope": scope,
            "groups": {
                "files": files,
                "nodes": nodes,
                "sops": sops,
                "rag_files": rag_files,
            },
            "counts": {
                "files": len(files),
                "nodes": len(nodes),
                "sops": len(sops),
                "rag_files": len(rag_files),
            },
        })
    except Exception as ex:
        logger.exception("console.resources failed user_id=%s", user_id)
        return _err(f"读取资源清单失败：{ex}")


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
):
    """上传文件到文件库（以选定用户名义归档，默认密级内部）。"""
    if not user_id:
        return _err("请选择上传用户")

    if confidentiality not in (0, 1, 2):
        confidentiality = 1

    data = await file.read()
    if not data:
        return _err("文件内容为空")

    try:
        from emily_core.repositories.file_repo import FileRepository
        from emily_core.services.file_storage_service import FileStorageService

        storage = _get_storage_service()  # 正确 root=/app/attachments（复用 core 实例）
        file_no = FileRepository.generate_file_no()
        target_dir = storage.ensure_dir()  # /app/attachments/napcat/YYYY-MM/

        ext = FileStorageService._infer_extension(file.filename or "", file.content_type or "")
        saved_name = f"{file_no}{ext}"
        local_path = target_dir / saved_name
        await asyncio.to_thread(local_path.write_bytes, data)

        record = await asyncio.to_thread(
            FileRepository.create,
            file_no=file_no,
            filename=file.filename or saved_name,
            uploaded_by=user_id,
            file_type=file.content_type or "",
            storage_path=str(local_path.relative_to(storage._storage_root)),
            file_size=len(data),
            file_category="OTHER",
            purpose="RECORD",
            confidentiality=confidentiality,
        )

        return _ok({
            "file_id": str(record.id) if record else "",
            "file_no": file_no,
            "filename": file.filename or saved_name,
            "size": len(data),
            "confidentiality": confidentiality,
        })
    except Exception as ex:
        logger.exception("console.upload failed user_id=%s", user_id)
        return _err(f"上传失败：{ex}")


# ══════════════════════════════════════════════════════════════════════════════
#  文件管理 —— 库内全量文件清单 + 删除（节点/RAG 进出复用下方既有接口）
# ══════════════════════════════════════════════════════════════════════════════

def _list_files_managed() -> list[dict]:
    """文件管理清单：文件名/ID/密级/上传人/时间/容量/是否入 RAG/可见节点列表。"""
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

    return [
        {
            "id": f.id,
            "name": f.name or "",
            "file_no": f.file_no or "",
            "confidentiality": f.confidentiality or 0,
            "uploaded_by": f.uploaded_by or "",
            "uploaded_by_name": (user_map.get(f.uploaded_by, "") if f.uploaded_by else ""),
            "created_at": f.created_at or "",
            "file_size": int(f.file_size or 0),
            "in_rag": f.id in rag_doc_ids,
            "visible_nodes": nodes_map.get(f.id, []),
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

    if operator_id:
        try:
            await BusinessEventLogger.log(
                event_category="file",
                event_action="console_file_delete",
                target_id=file_id,
                summary=f"删除文件：{name or file_id}",
                detail_json=json.dumps(result, ensure_ascii=False),
                user_id=operator_id,
            )
        except Exception as ex:
            logger.warning("console.file_delete log write failed: %s", ex)

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
    backend: str = "api"   # api=远程 Embedding API，local=本地 TEI


class RagDeleteRequest(BaseModel):
    doc_id: str
    operator_id: str = ""


class RagSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    backend: str = "api"
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

    client 为 TeiClient（本地）或 RemoteEmbeddingClient（远程 API），
    二者均暴露 embed(texts) -> list[list[float]]，供入库/查库复用。
    """
    cfg = _get_embedding_config()
    if backend == "local":
        if not cfg["tei_url"]:
            return None, "本地 TEI 地址未配置"
        from emily_core.infrastructure.embedding.tei_client import TeiClient
        return TeiClient(cfg["tei_url"]), None
    if backend == "api":
        if not (cfg["api_url"] and cfg["api_key"] and cfg["api_model"]):
            return None, "远程 Embedding API 未配置完整（url/key/model）"
        from emily_core.infrastructure.embedding.remote_client import RemoteEmbeddingClient
        return RemoteEmbeddingClient(
            api_url=cfg["api_url"], api_key=cfg["api_key"], model=cfg["api_model"],
        ), None
    return None, f"未知 embedding 后端：{backend}"


def _prepare_file_chunks(file_id: str) -> dict:
    """读取文件实体 → 解析文本 → 结构分块（同步，供 to_thread 包裹）。"""
    from pathlib import Path

    from emily_core.infrastructure.database.models import File
    from emily_core.infrastructure.database.session import get_session
    from emily_core.services.document_parser import DocumentParser
    from emily_core.services.structural_chunker import StructuralChunker

    with get_session() as session:
        f = session.query(File).filter(File.id == file_id).first()
        if f is None:
            return {"success": False, "error": "文件不存在"}
        if not f.storage_path:
            return {"success": False, "error": "文件缺少本地存储路径"}
        storage_path = f.storage_path
        file_no = f.file_no or file_id
        filename = f.filename or file_no

    storage_root = str(_get_storage_service()._storage_root)
    local_path = Path(storage_root) / storage_path
    if not local_path.exists():
        return {"success": False, "error": f"文件实体不存在：{local_path.name}"}

    text = DocumentParser().parse(local_path)
    if not text.strip():
        return {"success": False, "error": "文件解析后无文本内容（可能是不支持的二进制格式）"}

    chunks = StructuralChunker().chunk(text)
    if not chunks:
        return {"success": False, "error": "分块结果为空"}

    return {
        "success": True,
        "chunks": chunks,
        "file_no": file_no,
        "filename": filename,
    }


@router.post("/rag-index")
async def rag_index_file(req: RagIndexRequest):
    """给选定文件执行 RAG 入库（doc_id 锚定 files.id，选定执行人溯源）。"""
    file_id = req.file_id.strip()
    if not file_id:
        return _err("请选择要入库的文件")

    try:
        prep = await asyncio.to_thread(_prepare_file_chunks, file_id)
        if not prep.get("success"):
            return _err(prep.get("error", "入库失败"))

        from api.server import get_core
        core = get_core()
        repo = getattr(core, "_knowledge_chunk_repo", None)
        if repo is None:
            return _err("RAG 知识库未就绪")

        tei, err = _build_embedding_client(req.backend)
        if tei is None:
            return _err(err)

        from emily_core.tools.embed_tool import handle_embed_and_index
        res = await handle_embed_and_index({
            "chunks": prep["chunks"],
            "doc_metadata": {
                "doc_id": file_id,
                "doc_name": prep.get("file_no") or file_id,
                "stage": "console_rag_index",
                "role": req.user_id or "",
            },
        }, tei=tei, repo=repo)

        if not res.get("success"):
            return _err(res.get("error", "入库失败"))

        return _ok({
            "file_id": file_id,
            "file_no": prep.get("file_no", ""),
            "filename": prep.get("filename", ""),
            "doc_id": res.get("doc_id", file_id),
            "count": res.get("count", 0),
            "elapsed_ms": res.get("elapsed_ms", 0),
        })
    except Exception as ex:
        logger.exception("console.rag_index failed file_id=%s", file_id)
        return _err(f"入库失败：{ex}")


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
    """删除库内指定文档的全部向量分块（doc_id 锚定 files.id），以 operator_id 登记日志。"""
    doc_id = req.doc_id.strip()
    if not doc_id:
        return _err("请选择要删除的库内文件")

    try:
        from api.server import get_core
        repo = getattr(get_core(), "_knowledge_chunk_repo", None)
        if repo is None:
            return _err("RAG 知识库未就绪")

        deleted = await asyncio.to_thread(repo.delete_by_doc, doc_id)

        if req.operator_id:
            try:
                import json

                from emily_core.infrastructure.database.models import BusinessEventLog
                from emily_core.infrastructure.database.session import get_session
                with get_session() as session:
                    session.add(BusinessEventLog(
                        user_id=req.operator_id,
                        event_category="rag",
                        event_action="console_rag_delete",
                        summary=f"RAG 移除：{doc_id}",
                        detail_json=json.dumps(
                            {"doc_id": doc_id, "deleted": int(deleted or 0)},
                            ensure_ascii=False),
                    ))
            except Exception as ex:
                logger.warning("console.rag_delete log write failed: %s", ex)

        return _ok({"doc_id": doc_id, "deleted": int(deleted or 0)})
    except Exception as ex:
        logger.exception("console.rag_delete failed doc_id=%s", doc_id)
        return _err(f"删除失败：{ex}")


@router.post("/rag-search")
async def rag_search(req: RagSearchRequest):
    """根据输入信息查库（向量 + 关键词混合检索）。"""
    query = (req.query or "").strip()
    if not query:
        return _err("请输入查询内容")

    try:
        client, err = _build_embedding_client(req.backend)
        if client is None:
            return _err(err)

        from api.server import get_core
        repo = getattr(get_core(), "_knowledge_chunk_repo", None)
        if repo is None:
            return _err("RAG 知识库未就绪")

        from emily_core.providers.rag.pgvector_provider import PgVectorRagProvider
        provider = PgVectorRagProvider(tei=client, repo=repo)

        # C14/M3：按所选用户可见文件范围过滤（复用 VisibleFileSetResolver，宁少答不泄露）
        scoped_doc_ids = None
        user_id = (req.user_id or "").strip()
        if user_id:
            visible_file_ids, _, _ = await asyncio.to_thread(_build_visible_sets, user_id)
            scoped_doc_ids = list(visible_file_ids)

        resp = await provider.search(query, top_k=req.top_k, scoped_doc_ids=scoped_doc_ids)

        results = [
            {
                "content": r.content,
                "score": r.score,
                "source_document": r.source_document,
                "source_file_id": r.source_file_id,
                "source_title": r.source_title,
            }
            for r in resp.results
        ]
        return _ok({
            "query": query,
            "backend": req.backend,
            "total": resp.total,
            "results": results,
        })
    except Exception as ex:
        logger.exception("console.rag_search failed query=%s", query)
        return _err(f"查库失败：{ex}")


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
    """节点参与人增删（复用 NodeService，以 operator_id 身份登记日志）。"""
    if req.action not in ("add", "remove"):
        return _err("action 仅支持 add / remove")
    if not req.operator_id:
        return _err("请选择操作人（用于日志登记）")
    try:
        svc = _get_node_service()
        if req.action == "add":
            result = await svc.add_node_participant(
                req.node_id, req.user_id, req.operator_id, req.role or "participant",
            )
        else:
            result = await svc.remove_node_participant(
                req.node_id, req.user_id, req.operator_id,
            )
    except Exception as ex:
        logger.exception("console.node_participant failed node=%s", req.node_id)
        return _err(f"操作失败：{ex}")
    if not result.success:
        return _err(result.message or "操作失败")
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
#  日志聚合 —— 聚合各类 log，按「人 / 记录模块」过滤
# ══════════════════════════════════════════════════════════════════════════════

# 模块白名单与查询逻辑已下沉至 ConsoleLogRepo（repositories/console_log_repo.py）。


@router.get("/logs")
async def get_aggregated_logs(
    user_id: str = Query("", description="按用户过滤（操作人/归属人）"),
    module: str = Query("", description="记录模块 key，空=全部模块"),
    limit: int = Query(500, ge=1, le=2000, description="返回条数上限"),
):
    """聚合查看各类日志，可按「人」和「记录模块」过滤。"""
    from emily_core.repositories.console_log_repo import ConsoleLogRepo, LOG_MODULES

    log_keys = {m["key"] for m in LOG_MODULES}
    if module and module not in log_keys:
        return _err(f"未知记录模块：{module}")
    try:
        rows = await asyncio.to_thread(ConsoleLogRepo.query_aggregated, user_id, module, limit)
    except Exception as ex:
        logger.exception("console.logs failed module=%s user=%s", module, user_id)
        return _err(f"读取日志失败：{ex}")
    return _ok({
        "user_id": user_id or None,
        "module": module or None,
        "modules": [
            {"key": m["key"], "label": m["label"], "user_filterable": bool(m["user"])}
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
    """复用 scripts/self_check.py 的 self_check()，再补操作日志。"""
    import json

    from emily_core.infrastructure.logging.business_event_logger import BusinessEventLogger

    fn = _get_self_check_fn()
    if fn is None:
        raise RuntimeError("未找到 scripts/self_check.py")

    result = await asyncio.to_thread(
        fn, mode=mode, check_tool_registry=check_tool_registry,
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
        },
        "tools_consistency": result.get("tools_consistency", {}),
    }

    if operator_id:
        try:
            await BusinessEventLogger.log(
                event_category="system",
                event_action="console_self_check",
                summary=f"系统自检（{mode}）",
                detail_json=json.dumps(
                    {"mode": mode, "check_tool_registry": check_tool_registry},
                    ensure_ascii=False),
                user_id=operator_id,
            )
        except Exception as ex:
            logger.warning("console.self_check log write failed: %s", ex)

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

def _get_session_pool():
    """取当前生效的会话池：新路径 SessionLoopPool 优先（与路径开关一致），回退旧池。

    注：`use_loop=True` 时消息走 SessionLoopPool，旧的 SessionPoolManager 恒为空，
    必须按开关取池，否则观测到的永远是 0。
    """
    from api.server import get_core
    core = get_core()
    router = getattr(core, "_session_path_router", None)
    if router is not None and router.use_loop():
        pool = getattr(core, "_session_loop_pool", None)
        if pool is not None:
            return pool
    return getattr(core, "_session_pool", None)


@router.get("/session-pool")
def get_session_pool():
    """活跃 Session 池快照：总数 / 池运行时长 / 各会话空闲时长。"""
    pool = _get_session_pool()
    if pool is None:
        return _err("session pool 未就绪")
    try:
        return _ok(pool.get_status())
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_pool failed")
        return _err(f"会话池读取失败：{e}")


@router.get("/session-pool/{conversation_id}/messages")
def get_session_pool_messages(
    conversation_id: str,
    limit: int = Query(default=5, ge=1, le=50, description="返回最近消息条数"),
):
    """指定会话（业务 conversation_id）的最近消息，供会话池逐条查看。"""
    from emily_core.repositories.message_repo import MessageRepository
    try:
        rows = MessageRepository.get_recent_by_business_conversation(
            conversation_id, limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_pool.messages failed")
        return _err(f"会话消息读取失败：{e}")
    return _ok({"conversation_id": conversation_id, "messages": rows})


# ══════════════════════════════════════════════════════════════════════════════
#  会话归档（只读观测）：归档索引（DB）+ 对话全文（md 文件）
#  归档由 SessionContext._persist_archive 写入：DB 存薄索引，正文落在 md 文件
# ══════════════════════════════════════════════════════════════════════════════

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
    """列出全部会话归档记录（按归档时间倒序），附带正文文件大小。

    轮次（turn_count）**以归档正文为准**：从 md 的「## 第 N 轮」标题实时统计，
    避免历史记录中 DB 里恒为 0 的口径偏差。
    """
    from emily_core.repositories.session_archive_repo import SessionArchiveRepo
    from emily_core.services.session_archive_writer import SessionArchiveWriter
    try:
        rows = SessionArchiveRepo.list_all(limit=limit)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_archives failed")
        return _err(f"会话归档读取失败：{e}")
    adir = _archive_dir()
    for r in rows:
        p = _resolve_archive_file(r.get("md_file_path") or "", adir)
        r["file_size"] = p.stat().st_size if p is not None else 0
        r["has_content"] = p is not None
        if p is not None:
            try:
                counted = SessionArchiveWriter.count_turns(str(p))
                if counted:
                    r["turn_count"] = counted
            except Exception as e:  # noqa: BLE001
                logger.debug("count_turns failed for %s: %s", p, e)
    return _ok({"rows": rows, "archive_dir": str(adir)})


@router.get("/session-archives/{archive_id}/content")
def get_session_archive_content(archive_id: str):
    """读取单条归档的对话全文（md 原文，只读）。"""
    from emily_core.repositories.session_archive_repo import SessionArchiveRepo
    try:
        row = SessionArchiveRepo.get_by_id(archive_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_archive.content failed")
        return _err(f"归档记录读取失败：{e}")
    if not row:
        return _err("归档记录不存在")
    p = _resolve_archive_file(row.get("md_file_path") or "", _archive_dir())
    if p is None:
        return _err("归档正文文件不存在")
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        logger.exception("console.session_archive.read failed")
        return _err(f"归档正文读取失败：{e}")
    if len(content) > 400_000:
        content = content[:400_000] + "\n\n…（正文超长，已截断）"
    # 轮次与列表口径一致：以归档正文「## 第 N 轮」实时统计，避免 DB 旧值恒为 0
    from emily_core.services.session_archive_writer import SessionArchiveWriter
    turn_count = row.get("turn_count", 0)
    try:
        counted = SessionArchiveWriter.count_turns(str(p))
        if counted:
            turn_count = counted
    except Exception as e:  # noqa: BLE001
        logger.debug("count_turns failed for %s: %s", p, e)
    return _ok({
        "id": archive_id,
        "conversation_id": row.get("conversation_id", ""),
        "user_name": row.get("user_name", ""),
        "archived_at": row.get("archived_at", ""),
        "archive_reason": row.get("archive_reason", ""),
        "turn_count": turn_count,
        "file_name": p.name,
        "content": content,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  测试用例库（只读）：需求/测试用例/*.md 清单 + 单条用例细节
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
    return Path(__file__).resolve().parents[3] / "需求" / "测试用例"


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
    """测试用例库清单（只读）：需求/测试用例/*.md 的用例表逐条索引。"""
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
