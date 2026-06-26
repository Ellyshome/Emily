"""StateMachineApplication — orchestration layer for the global state machine.

Follows the plan_task_app.py pattern:
    - Takes a StateMachineService instance
    - Orchestrates service calls and formats user-facing text replies
    - Returns dict with {"success", "reply", ...} keys
"""

from emily_core.services.state_machine_service import StateMachineService, NodeNotFoundError, InvalidStateTransitionError
from emily_core.services.state_machine_commands import (
    ChangeNodeStatusCommand,
    ForceActivateNodeCommand,
    StageProgress,
    OverallProgress,
    NodeInfo,
    AuditLogQuery,
)


class StateMachineApplication:
    """Application-layer orchestrator for state machine operations."""

    def __init__(self, service: StateMachineService):
        self._service = service

    async def change_node_status(self, node_id: str, target_status: str, *,
                                 operator_id: str = "system",
                                 reason: str = "") -> dict:
        try:
            cmd = ChangeNodeStatusCommand(
                node_id=node_id, target_status=target_status,
                operator_id=operator_id, reason=reason,
            )
            resp = await self._service.change_node_status(cmd)
            return {
                "success": True,
                "node_id": resp.node_id,
                "from_status": resp.from_status,
                "to_status": resp.to_status,
                "cascaded": resp.cascaded_nodes,
                "reply": resp.reply,
            }
        except InvalidStateTransitionError as e:
            return {"success": False, "error": str(e), "reply": f"状态变更被拒绝：{e}"}
        except NodeNotFoundError as e:
            return {"success": False, "error": str(e), "reply": f"节点未找到：{e}"}
        except ValueError as e:
            return {"success": False, "error": str(e), "reply": f"非法参数：{e}"}

    async def force_activate_node(self, node_id: str, *,
                                  operator_id: str = "system",
                                  reason: str = "") -> dict:
        try:
            cmd = ForceActivateNodeCommand(
                node_id=node_id, operator_id=operator_id, reason=reason,
            )
            resp = await self._service.force_activate_node(cmd)
            return {"success": True, "node_id": resp.node_id, "reply": resp.reply}
        except Exception as e:
            return {"success": False, "error": str(e), "reply": str(e)}

    async def get_node(self, node_id: str) -> dict:
        info = await self._service.get_node_info(node_id)
        if info is None:
            return {"success": False, "reply": f"节点 '{node_id}' 不存在"}
        return {"success": True, "node": info}

    async def list_nodes(self, *, stage_id: int = None, status: str = None) -> dict:
        nodes = await self._service.list_nodes(stage_id=stage_id, status=status)
        return {"success": True, "nodes": nodes, "count": len(nodes)}

    async def get_stage_progress(self, stage_id: int) -> dict:
        sp = await self._service.get_stage_progress(stage_id)
        if sp is None:
            return {"success": False, "reply": f"阶段 {stage_id} 不存在"}
        return {"success": True, "stage": sp}

    async def get_overall_progress(self) -> dict:
        op = await self._service.get_overall_progress()
        return {"success": True, "overall": op}

    async def get_satisfaction(self, node_id: str) -> dict:
        try:
            score = await self._service.calculate_precondition_score(node_id)
            return {"success": True, "node_id": node_id, "precondition_score": score}
        except NodeNotFoundError as e:
            return {"success": False, "error": str(e)}

    async def get_audit_logs(self, target_type: str = "", target_id: str = "",
                             limit: int = 100, offset: int = 0) -> dict:
        query = AuditLogQuery(target_type=target_type, target_id=target_id,
                              limit=limit, offset=offset)
        logs = await self._service.get_audit_logs(query)
        return {"success": True, "logs": logs, "count": len(logs)}
