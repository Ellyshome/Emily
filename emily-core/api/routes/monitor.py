"""监控 API 路由 —— 只读运维看板。

端点：
    GET /api/v1/monitor/containers                  — 容器运行状态
    GET /api/v1/monitor/sessions                    — Session 池状态
    GET /api/v1/monitor/sessions/{conversation_id}/messages — 会话最近消息
    GET /api/v1/monitor/nodes                       — 全景节点列表
    GET /api/v1/monitor/nodes/{node_id}             — 节点详情
    GET /api/v1/monitor/files                       — 管控文件列表
    GET /api/v1/monitor/users                       — 人员列表

参照模式：api/routes/node.py（lazy _get_service + set_xxx_service 注入）。
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Query

from .monitor_schemas import MonitorApiResponse

logger = logging.getLogger("emily.api.monitor")

router = APIRouter(prefix="/monitor", tags=["monitor"])

# 延迟初始化（与 node 路由同模式）
_service = None


def set_monitor_service(service) -> None:
    """由 EmilyCore 注入 MonitorService 实例。"""
    global _service
    _service = service


def _get_service():
    """惰性获取 MonitorService。"""
    global _service
    if _service is not None:
        return _service
    try:
        from api.server import get_core
        core = get_core()
        core._ensure_initialized()
        _service = core._monitor_service
    except Exception:
        pass
    if _service is None:
        raise HTTPException(status_code=503, detail="Monitor module not initialized")
    return _service


# ══════════════════════════════════════════════════════════════════════════════
# 容器状态
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/containers")
async def get_containers():
    """获取受监控容器运行状态。"""
    svc = _get_service()
    containers = await svc.get_containers()
    im_accounts = await svc.get_im_accounts()
    # 注入 NapCat WebUI token
    napcat_token = os.environ.get("NAPCAT_WEBUI_TOKEN", "")
    for acc in im_accounts:
        if acc["platform"] == "qq":
            acc["webui_token"] = napcat_token
    return MonitorApiResponse(data={
        "containers": containers,
        "im_accounts": im_accounts,
    })

# ══════════════════════════════════════════════════════════════════════════════
# Session 池
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def get_sessions():
    """获取活跃 Session 池状态。"""
    svc = _get_service()
    data = await svc.get_session_pool()
    return MonitorApiResponse(data=data)


@router.get("/sessions/{conversation_id}/messages")
async def get_session_messages(
    conversation_id: str,
    limit: int = Query(default=5, ge=1, le=20),
):
    """获取指定会话最近 N 条消息。"""
    svc = _get_service()
    messages = await svc.get_session_messages(conversation_id, limit=limit)
    return MonitorApiResponse(data=messages)


# ══════════════════════════════════════════════════════════════════════════════
# 全景节点
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/nodes")
async def list_nodes(
    project_id: str | None = Query(default=None, description="按项目筛选"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询全景节点列表（仅业务字段，排除已废弃）。"""
    svc = _get_service()
    nodes = await svc.list_nodes(project_id=project_id, limit=limit, offset=offset)
    return MonitorApiResponse(data=nodes)


@router.get("/nodes/{node_id}")
async def get_node_detail(node_id: str):
    """查询单节点完整业务字段。"""
    svc = _get_service()
    node = await svc.get_node_detail(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return MonitorApiResponse(data=node)


# ══════════════════════════════════════════════════════════════════════════════
# 管控文件
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/files")
async def list_files(
    project_id: str | None = Query(default=None, description="按项目筛选"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询管控文件列表（仅最新版本，排除已删除）。"""
    svc = _get_service()
    files = await svc.list_files(project_id=project_id, limit=limit, offset=offset)
    return MonitorApiResponse(data=files)


# ══════════════════════════════════════════════════════════════════════════════
# 人员列表
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    """查询人员列表（活跃用户）。"""
    svc = _get_service()
    users = await svc.list_users(limit=limit, offset=offset)
    return MonitorApiResponse(data=users)
