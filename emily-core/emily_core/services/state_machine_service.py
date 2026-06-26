"""StateMachineService — core engine for the global state machine.

Follows the plan_task_service.py pattern:
    - async methods with asyncio.to_thread wrapping sync repo calls
    - _validate_transition static helper
    - CQRS command DTOs from state_machine_commands
    - audit log writing in same transaction
"""

import asyncio
import json
import difflib
from datetime import datetime, timezone, timedelta
from typing import Optional

from emily_core.repositories.sm_node_repo import SMNodeRepository
from emily_core.repositories.sm_stage_repo import SMStageRepository
from emily_core.repositories.sm_audit_repo import SMAuditRepository
from emily_core.infrastructure.database.session import get_session
from emily_core.state_machine.node_state import (
    NodeStatus,
    TRANSITIONS,
    TERMINAL_STATES,
    is_valid_transition,
    is_terminal,
)
from emily_core.state_machine.stage_state import STAGE_LABELS
from emily_core.services.state_machine_commands import (
    ChangeNodeStatusCommand,
    ChangeStatusResponse,
    ForceActivateNodeCommand,
    StageProgress,
    OverallProgress,
    NodeInfo,
    AuditLogQuery,
)

BEIJING_TZ = timezone(timedelta(hours=8))


class InvalidStateTransitionError(ValueError):
    """Raised when a state transition is not allowed by the transition matrix."""
    pass


class NodeNotFoundError(ValueError):
    """Raised when a node_id is not found."""
    pass


class StateMachineService:
    """Core state machine engine for the global project state machine."""

    def __init__(self, *,
                 node_repo: SMNodeRepository,
                 stage_repo: SMStageRepository,
                 audit_repo: SMAuditRepository,
                 cascade_max_depth: int = 5,
                 auto_start_enabled: bool = False,
                 ):
        self._node_repo = node_repo
        self._stage_repo = stage_repo
        self._audit_repo = audit_repo
        self._cascade_max_depth = cascade_max_depth
        self._auto_start_enabled = auto_start_enabled

    # ========================================================================
    #  Status Change
    # ========================================================================

    async def change_node_status(self, cmd: ChangeNodeStatusCommand) -> ChangeStatusResponse:
        """Change a node's status with validation, audit, and cascade."""
        target = NodeStatus(cmd.target_status)

        # 1. Load node
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            raise NodeNotFoundError(f"节点 '{cmd.node_id}' 不存在")

        current_status = node.status
        current = NodeStatus(current_status) if current_status else None

        # 2. Validate transition
        self._validate_transition(current, target)

        # 3. Execute in transaction: status update + audit log
        now_str = datetime.now(BEIJING_TZ).isoformat()
        terminated = is_terminal(NodeStatus(current_status)) if current else False
        if terminated:
            raise InvalidStateTransitionError(
                f"节点 '{cmd.node_id}' 已处于终态 '{current_status}'，不可变更"
            )

        with get_session() as session:
            # Status update
            updated = self._node_repo.update_status(
                cmd.node_id, target.value,
                actual_start_date=now_str if target == NodeStatus.IN_PROGRESS else None,
                actual_end_date=now_str if target == NodeStatus.COMPLETED else None,
                block_reason=cmd.reason if target == NodeStatus.BLOCKED else None,
                delay_reason=cmd.reason if target == NodeStatus.DELAYED else None,
                session=session,
            )
            if updated is None:
                raise NodeNotFoundError(f"节点 '{cmd.node_id}' 不存在")

            # Audit log — status history
            snapshot = {
                "node_id": updated.node_id,
                "node_name": updated.node_name,
                "status": updated.status,
                "risk_level": updated.risk_level,
                "precondition_score": updated.precondition_score,
            }
            self._audit_repo.write_status_history(
                cmd.node_id, current_status or "", target.value,
                operator_id=cmd.operator_id,
                reason=cmd.reason,
                snapshot=snapshot,
                session=session,
            )

            # Cascade update downstream nodes' precondition scores
            cascaded = []
            if target == NodeStatus.COMPLETED:
                cascaded = self._cascade_update(cmd.node_id, session=session)

            # Update stage progress
            if updated.stage_id:
                self._recalc_stage_progress(updated.stage_id, session=session)

        # 4. Build response
        reply = self._format_status_change_reply(cmd.node_id, current_status, target.value, cascaded)
        return ChangeStatusResponse(
            success=True,
            node_id=cmd.node_id,
            from_status=current_status or "",
            to_status=target.value,
            precondition_score=updated.precondition_score,
            cascaded_nodes=cascaded,
            reply=reply,
        )

    async def force_activate_node(self, cmd: ForceActivateNodeCommand) -> ChangeStatusResponse:
        """Force-activate an externally-triggered node (bypass dependency checks)."""
        # Validate the node exists and is externally-triggered
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            raise NodeNotFoundError(f"节点 '{cmd.node_id}' 不存在")

        change_cmd = ChangeNodeStatusCommand(
            node_id=cmd.node_id,
            target_status=NodeStatus.IN_PROGRESS.value,
            operator_id=cmd.operator_id,
            reason=f"外部触发强制启动: {cmd.reason}",
        )
        return await self.change_node_status(change_cmd)

    # ========================================================================
    #  Transition Validation
    # ========================================================================

    @staticmethod
    def _validate_transition(current: Optional[NodeStatus], target: NodeStatus) -> None:
        if current is None:
            current = NodeStatus.NOT_STARTED
        if not is_valid_transition(current, target):
            allowed = [s.value for s in TRANSITIONS.get(current, [])]
            raise InvalidStateTransitionError(
                f"非法状态流转：'{current.value}' → '{target.value}'，"
                f"允许的目标状态：{allowed or '（终态，不可变更）'}"
            )

    # ========================================================================
    #  Precondition Score Calculation
    # ========================================================================

    async def calculate_precondition_score(self, node_id: str) -> int:
        """Calculate the precondition satisfaction score (0-100) for a node.

        Formula from 需求 §4.3:
            satisfaction = SUM(predecessor_status_coefficient * dependency_weight)

        Status coefficients:
            COMPLETED = 1.0
            IN_PROGRESS (>80% progress implicitly) = 0.8  (weak deps can start early)
            IN_PROGRESS (≤80%) = 0.5
            Other = 0

        Strong dependency constraint:
            If any required dependency is NOT COMPLETED → satisfaction capped at 99.
        """
        deps = await asyncio.to_thread(self._node_repo.get_dependencies, node_id)
        if not deps:
            return 100  # No dependencies → ready to start

        total_weight = 0.0
        earned_weight = 0.0
        has_strong_unsatisfied = False

        for dep in deps:
            w = float(dep.weight)
            total_weight += w
            dep_node = await asyncio.to_thread(self._node_repo.get_by_node_id, dep.to_node_id)
            if dep_node is None:
                continue

            dep_status = dep_node.status
            if dep_status == NodeStatus.COMPLETED.value:
                coeff = 1.0
            elif dep_status == NodeStatus.IN_PROGRESS.value:
                coeff = 0.8  # Simplified — could check progress > 80% via heuristic
            else:
                coeff = 0.0

            if dep.required and dep_status != NodeStatus.COMPLETED.value:
                has_strong_unsatisfied = True

            earned_weight += w * coeff

        if total_weight == 0:
            return 100

        score = int((earned_weight / total_weight) * 100)
        if has_strong_unsatisfied:
            score = min(score, 99)  # cap at 99 when any required dep is unsatisfied

        return score

    # ========================================================================
    #  Cascade Update
    # ========================================================================

    def _cascade_update(self, completed_node_id: str, *, session, depth: int = 0) -> list[str]:
        """BFS cascade: when a node completes, update all downstream nodes' precondition scores.

        Returns list of node_ids that were auto-started as a result.
        """
        if depth >= self._cascade_max_depth:
            return []

        cascaded: list[str] = []
        downstream = self._node_repo.get_downstream_nodes(completed_node_id, session=session)

        for dep in downstream:
            # Recalculate precondition score for this downstream node
            node_id = dep.from_node_id
            node = self._node_repo.get_by_node_id(node_id, session=session)
            if node is None:
                continue

            # Build score (synchronous within the same session)
            deps = self._node_repo.get_dependencies(node_id, session=session)
            if not deps:
                score = 100
            else:
                total_weight = 0.0
                earned_weight = 0.0
                has_strong_unsatisfied = False
                for d in deps:
                    w = float(d.weight)
                    total_weight += w
                    dn = self._node_repo.get_by_node_id(d.to_node_id, session=session)
                    if dn is None:
                        continue
                    if dn.status == NodeStatus.COMPLETED.value:
                        coeff = 1.0
                    elif dn.status == NodeStatus.IN_PROGRESS.value:
                        coeff = 0.8
                    else:
                        coeff = 0.0
                    if d.required and dn.status != NodeStatus.COMPLETED.value:
                        has_strong_unsatisfied = True
                    earned_weight += w * coeff
                score = int((earned_weight / total_weight) * 100) if total_weight > 0 else 100
                if has_strong_unsatisfied:
                    score = min(score, 99)

            self._node_repo.update_precondition_score(node_id, score, session=session)

            # Auto-start if score == 100 and node is NOT_STARTED
            if score >= 100 and node.status == NodeStatus.NOT_STARTED.value and self._auto_start_enabled:
                self._node_repo.update_status(
                    node_id, NodeStatus.IN_PROGRESS.value,
                    actual_start_date=datetime.now(BEIJING_TZ).isoformat(),
                    session=session,
                )
                cascaded.append(node_id)
                # Recurse
                cascaded.extend(self._cascade_update(node_id, session=session, depth=depth + 1))

        return cascaded

    # ========================================================================
    #  Stage Progress
    # ========================================================================

    def _recalc_stage_progress(self, stage_id: int, *, session) -> None:
        """Recalculate and persist stage-level progress."""
        nodes = self._node_repo.list_by_stage(stage_id, session=session)
        total = len(nodes)
        completed = sum(1 for n in nodes if n.status == NodeStatus.COMPLETED.value)
        self._stage_repo.update_progress(stage_id, total, completed, session=session)

    async def get_stage_progress(self, stage_id: int) -> Optional[StageProgress]:
        stage = await asyncio.to_thread(self._stage_repo.get_by_id, stage_id)
        if stage is None:
            return None
        cp = []
        try:
            cp = json.loads(stage.critical_path)
        except (json.JSONDecodeError, TypeError):
            pass
        return StageProgress(
            stage_id=stage.stage_id,
            stage_name=stage.stage_name,
            status=stage.status,
            total_nodes=stage.total_nodes,
            completed_nodes=stage.completed_nodes,
            progress=stage.progress,
            critical_path=cp,
        )

    async def get_overall_progress(self) -> OverallProgress:
        stages = await asyncio.to_thread(self._stage_repo.list_all)
        stage_progresses = []
        total_nodes = 0
        completed_nodes = 0
        for s in stages:
            sp = StageProgress(
                stage_id=s.stage_id, stage_name=s.stage_name, status=s.status,
                total_nodes=s.total_nodes, completed_nodes=s.completed_nodes, progress=s.progress,
            )
            stage_progresses.append(sp)
            total_nodes += s.total_nodes
            completed_nodes += s.completed_nodes

        overall_progress = int(completed_nodes / total_nodes * 100) if total_nodes > 0 else 0
        return OverallProgress(
            total_nodes=total_nodes, completed_nodes=completed_nodes,
            progress=overall_progress, stages=stage_progresses,
        )

    # ========================================================================
    #  Node Info
    # ========================================================================

    async def get_node_info(self, node_id: str) -> Optional[NodeInfo]:
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        deps = await asyncio.to_thread(self._node_repo.get_dependencies, node_id)
        downstream = await asyncio.to_thread(self._node_repo.get_downstream_nodes, node_id)
        deliverables = await asyncio.to_thread(self._node_repo.get_deliverables, node_id)

        return NodeInfo(
            node_id=node.node_id,
            node_name=node.node_name,
            stage_id=node.stage_id,
            node_type=node.node_type,
            status=node.status,
            precondition_score=node.precondition_score,
            risk_level=node.risk_level,
            is_milestone=node.is_milestone,
            dependencies=[d.to_node_id for d in deps],
            deliverables=[d.deliverable_name for d in deliverables],
            downstream=[d.from_node_id for d in downstream],
        )

    async def list_nodes(self, *, stage_id: Optional[int] = None,
                         status: Optional[str] = None) -> list[NodeInfo]:
        if stage_id is not None:
            nodes = await asyncio.to_thread(self._node_repo.list_by_stage, stage_id)
        elif status is not None:
            nodes = await asyncio.to_thread(self._node_repo.list_by_status, status)
        else:
            nodes = await asyncio.to_thread(self._node_repo.list_all)

        result = []
        for n in nodes:
            result.append(NodeInfo(
                node_id=n.node_id, node_name=n.node_name, stage_id=n.stage_id,
                node_type=n.node_type, status=n.status,
                precondition_score=n.precondition_score, risk_level=n.risk_level,
                is_milestone=n.is_milestone,
            ))
        return result

    async def get_audit_logs(self, query: AuditLogQuery) -> list[dict]:
        target_type = query.target_type or None
        target_id = query.target_id or None
        logs = await asyncio.to_thread(
            self._audit_repo.query_logs,
            target_type=target_type, target_id=target_id,
            limit=query.limit, offset=query.offset,
        )
        result = []
        for log in logs:
            result.append({
                "id": log.id,
                "timestamp": log.timestamp,
                "operator": log.operator,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "action": log.action,
                "reason": log.reason,
                "risk_marked": log.risk_marked,
                "created_at": log.created_at,
            })
        return result

    # ========================================================================
    #  Helpers
    # ========================================================================

    @staticmethod
    def _format_status_change_reply(node_id: str, from_s: str, to_s: str, cascaded: list[str]) -> str:
        msg = f"节点 {node_id} 状态变更：{from_s or 'NOT_STARTED'} → {to_s}"
        if cascaded:
            msg += f"，级联更新：{', '.join(cascaded[:5])}"
            if len(cascaded) > 5:
                msg += f" 等 {len(cascaded)} 个节点"
        return msg

    # ========================================================================
    #  Phase C — Session-Agent 集成
    # ========================================================================

    async def query_sm_status(self, *, node_id: str = "", stage_id: int = 0,
                              keyword: str = "", limit: int = 10) -> dict:
        """给 Agent 调用的状态查询接口。

        返回匹配节点的状态摘要，供 LLM 基于结果做自然语言回复。
        """
        if node_id:
            info = await self.get_node_info(node_id)
            if info is None:
                return {"success": False, "reply": f"节点 '{node_id}' 不存在"}
            return {
                "success": True,
                "node": {
                    "node_id": info.node_id,
                    "node_name": info.node_name,
                    "status": info.status,
                    "stage_id": info.stage_id,
                    "precondition_score": info.precondition_score,
                    "risk_level": info.risk_level,
                    "is_milestone": info.is_milestone,
                    "dependencies": info.dependencies,
                    "downstream": info.downstream,
                },
                "reply": (
                    f"节点 {info.node_id}「{info.node_name}」"
                    f"——状态：{info.status}，前置满足度：{info.precondition_score}%"
                    f"（依赖：{', '.join(info.dependencies) if info.dependencies else '无'}）"
                ),
            }

        nodes = await self.list_nodes(stage_id=stage_id if stage_id > 0 else None)
        if keyword:
            nodes = [n for n in nodes if keyword in n.node_name or keyword in n.node_id]

        matched = nodes[:limit]
        lines = []
        for n in matched:
            lines.append(f"{n.node_id}「{n.node_name}」[{n.status}] 满足度={n.precondition_score}%")
        status_msg = "\n".join(lines) if lines else "未找到匹配节点"
        overall = await self.get_overall_progress()
        return {
            "success": True,
            "nodes": [{
                "node_id": n.node_id, "node_name": n.node_name,
                "status": n.status, "stage_id": n.stage_id,
                "precondition_score": n.precondition_score, "risk_level": n.risk_level,
            } for n in matched],
            "matched_count": len(matched),
            "overall_progress": overall.progress,
            "reply": f"项目进度 {overall.progress}%（{overall.completed_nodes}/{overall.total_nodes}）\n{status_msg}",
        }

    async def try_match_and_complete(self, event_title: str, event_type: str = "",
                                     project_id: str = "") -> dict:
        """事件录入后尝试匹配全景节点并自动完成。

        使用事件标题做关键词匹配，查找 sm_nodes 中名称最相关的
        IN_PROGRESS 节点（相似度最高优先），将其状态变更为 COMPLETED
        并触发级联更新。
        """
        import difflib

        with get_session() as session:
            # 1. 搜名字最匹配的 IN_PROGRESS 节点
            in_progress = self._node_repo.list_by_status("IN_PROGRESS", session=session)
            best_match = None
            best_score = 0.0
            for n in in_progress:
                score = difflib.SequenceMatcher(None, event_title, n.node_name).ratio()
                if score > max(best_score, 0.35):  # 阈值 0.35 以上才考虑
                    best_score = score
                    best_match = n

            if best_match is None or best_score < 0.35:
                return {
                    "success": True,
                    "matched": False,
                    "reply": f"未匹配到相关节点（最佳相似度={best_score:.2f}），仅记录事件",
                }

            # 2. 执行状态变更
            try:
                from datetime import datetime, timezone, timedelta
                BEIJING_TZ = timezone(timedelta(hours=8))
                now_str = datetime.now(BEIJING_TZ).isoformat()

                node = best_match
                current_status = node.status
                target = NodeStatus.COMPLETED

                if not is_valid_transition(NodeStatus(current_status), target):
                    return {
                        "success": True,
                        "matched": True,
                        "node_id": node.node_id,
                        "node_name": node.node_name,
                        "completed": False,
                        "reply": (
                            f"匹配到节点 {node.node_id}「{node.node_name}」"
                            f"（相似度={best_score:.2f}），但当前状态为'{current_status}'"
                            f"无法自动完成"
                        ),
                    }

                self._node_repo.update_status(
                    node.node_id, target.value,
                    actual_end_date=now_str,
                    session=session,
                )

                # 审计日志
                snapshot = {
                    "node_id": node.node_id, "node_name": node.node_name,
                    "status": target.value,
                }
                self._audit_repo.write_status_history(
                    node.node_id, current_status, target.value,
                    operator_id="agent", reason=f"Agent 事件录入触发：{event_title[:200]}",
                    snapshot=snapshot, session=session,
                )

                # 级联更新
                cascaded = self._cascade_update(node.node_id, session=session)

                # 更新阶段进度
                if node.stage_id:
                    self._recalc_stage_progress(node.stage_id, session=session)

                return {
                    "success": True,
                    "matched": True,
                    "node_id": node.node_id,
                    "node_name": node.node_name,
                    "score": round(best_score, 2),
                    "completed": True,
                    "cascaded_nodes": cascaded,
                    "reply": (
                        f"✅ 自动匹配：节点 {node.node_id}「{node.node_name}」"
                        f"（相似度={best_score:.2f}）已完成"
                        + (f"，级联影响 {len(cascaded)} 个下游节点" if cascaded else "")
                    ),
                }
            except Exception as e:
                logger.warning("try_match_and_complete 失败: %s", e)
                return {"success": False, "error": str(e), "reply": f"自动完成失败：{e}"}
