"""State Machine API routes.

Endpoints:
    GET  /nodes                     — list all nodes (filter: stage_id, status)
    GET  /nodes/{node_id}           — node detail with dependencies
    PUT  /nodes/{node_id}/status    — change node status
    POST /nodes/{node_id}/activate  — force-activate external-trigger node
    GET  /nodes/{node_id}/satisfaction — precondition score
    GET  /stages                    — list all 7 stages
    GET  /stages/{stage_id}/progress — stage progress
    GET  /progress                  — overall project progress
    GET  /audit-logs                — audit log query
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from emily_core.application.state_machine_app import StateMachineApplication

router = APIRouter(prefix="/state-machine", tags=["state-machine"])

_app: StateMachineApplication | None = None


def set_state_machine_app(app: StateMachineApplication) -> None:
    global _app
    _app = app


def _get_app() -> StateMachineApplication:
    if _app is None:
        raise HTTPException(status_code=503, detail="State machine module not initialized")
    return _app


# ========================================================================
#  Pydantic models
# ========================================================================

class StatusChangeRequest(BaseModel):
    target_status: str
    operator_id: str = "system"
    reason: str = ""


class ForceActivateRequest(BaseModel):
    operator_id: str = "system"
    reason: str = ""


# ========================================================================
#  Node endpoints
# ========================================================================

@router.get("/nodes")
async def list_nodes(stage_id: int | None = Query(None), status: str | None = Query(None)):
    app = _get_app()
    result = await app.list_nodes(stage_id=stage_id, status=status)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    app = _get_app()
    result = await app.get_node(node_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["reply"])
    return result


@router.put("/nodes/{node_id}/status")
async def change_node_status(node_id: str, body: StatusChangeRequest):
    app = _get_app()
    result = await app.change_node_status(
        node_id, body.target_status,
        operator_id=body.operator_id, reason=body.reason,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("reply", result.get("error", "")))
    return result


@router.post("/nodes/{node_id}/activate")
async def force_activate_node(node_id: str, body: ForceActivateRequest):
    app = _get_app()
    result = await app.force_activate_node(
        node_id, operator_id=body.operator_id, reason=body.reason,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("reply", ""))
    return result


@router.get("/nodes/{node_id}/satisfaction")
async def get_node_satisfaction(node_id: str):
    app = _get_app()
    result = await app.get_satisfaction(node_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error", ""))
    return result


# ========================================================================
#  Stage endpoints
# ========================================================================

@router.get("/stages")
async def list_stages():
    app = _get_app()
    overall = await app.get_overall_progress()
    return {"success": True, "stages": overall.get("overall").stages if overall.get("success") else []}


@router.get("/stages/{stage_id}/progress")
async def get_stage_progress(stage_id: int):
    app = _get_app()
    result = await app.get_stage_progress(stage_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("reply", ""))
    return result


# ========================================================================
#  Global endpoints
# ========================================================================

@router.get("/progress")
async def get_overall_progress():
    app = _get_app()
    result = await app.get_overall_progress()
    if not result["success"]:
        raise HTTPException(status_code=500, detail="Failed to compute progress")
    return result


@router.get("/audit-logs")
async def get_audit_logs(
    target_type: str | None = Query(None),
    target_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    app = _get_app()
    result = await app.get_audit_logs(
        target_type=target_type or "", target_id=target_id or "",
        limit=limit, offset=offset,
    )
    return result
