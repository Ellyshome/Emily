"""全景节点图 V2 Service 层 —— 核心业务逻辑。

职责：
  - 节点/成果/依赖的 CRUD 编排
  - 调用状态机引擎 + 写入 DB
  - 循环依赖检测前置（BFS）
  - 事件记录（状态流转、操作审计）

基于需求文档 §4.1–§4.5。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from .node_commands import (
    CreateNodeCommand,
    UpdateNodeCommand,
    DiscardNodeCommand,
    ActivateNodeCommand,
    CreateDeliverableCommand,
    UpdateDeliverableProgressCommand,
    AddDependencyCommand,
    RemoveDependencyCommand,
    AssignNodeCommand,
    SubmitNodeDeliverableCommand,
    ConfirmNodeDeliverableCommand,
    ReturnNodeDeliverableCommand,
    ResubmitNodeDeliverableCommand,
    AddParticipantCompanyCommand,
    RemoveParticipantCompanyCommand,
    SetParticipantCompaniesCommand,
    NodeOperationResult,
    CycleCheckResult,
    StateTransitionResult,
)
from .node_state_machine import (
    NOT_ACTIVATED,
    CONDITIONS_NOT_MET,
    IN_PROGRESS,
    COMPLETED,
    NodeSnapshot,
    DependencySnapshot,
    DeliverableSnapshot,
    ChildSnapshot,
    calc_dependency_satisfaction,
    calc_deliverable_completion,
    determine_node_status,
    detect_cycle,
)
from ..repositories.node_repo import (
    ProjectNodeRepo,
    NodeDependencyRepo,
    NodeDeliverableRepo,
    NodeEventRepo,
    NodeParticipantCompanyRepo,
    _parse_decimal,
    _to_decimal_str,
)
from ..infrastructure.database.models import _new_id

if TYPE_CHECKING:
    from ..infrastructure.database.models import (
        ProjectNode,
        NodeDependency,
        NodeDeliverable,
    )

logger = logging.getLogger("emily.node_service")

BEIJING_TZ = timezone(timedelta(hours=8))


def _derive_related_company_from_participants(
    participant_company_ids: list[str],
    default: str = "建设单位",
) -> str:
    """从参与单位列表中推导关联单位（优先取管理单位，否则取首项，兜底 default）。"""
    if not participant_company_ids:
        return default
    # 尝试找到管理单位（CompanyInfo.is_admin=True）
    try:
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import CompanyInfo
        with get_session() as session:
            admin_company = (
                session.query(CompanyInfo)
                .filter(
                    CompanyInfo.id.in_(participant_company_ids),
                    CompanyInfo.is_admin == True,
                )
                .first()
            )
            if admin_company:
                return admin_company.id
    except Exception:
        pass
    return participant_company_ids[0]


# ══════════════════════════════════════════════════════════════════════════════
# NodeService
# ══════════════════════════════════════════════════════════════════════════════

class NodeService:
    """全景节点图核心业务 Service。"""

    def __init__(
        self,
        node_repo: ProjectNodeRepo | None = None,
        dependency_repo: NodeDependencyRepo | None = None,
        deliverable_repo: NodeDeliverableRepo | None = None,
        event_repo: NodeEventRepo | None = None,
        npc_repo: NodeParticipantCompanyRepo | None = None,
        user_repo=None,
        outbound_bus=None,
    ):
        self._node_repo = node_repo or ProjectNodeRepo()
        self._dep_repo = dependency_repo or NodeDependencyRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()
        self._event_repo = event_repo or NodeEventRepo()
        self._npc_repo = npc_repo or NodeParticipantCompanyRepo()
        self._user_repo = user_repo
        self._outbound_bus = outbound_bus

    # ── 辅助方法 ──

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _record_event(self, node_id: str, event_type: str,
                      old_value: str = "", new_value: str = "",
                      operator_id: str = "", remark: str = "") -> None:
        """记录事件（同步写入，fire-and-forget）。"""
        try:
            self._event_repo.create(
                event_id=_new_id("EVT"),
                node_id=node_id,
                event_type=event_type,
                old_value=old_value,
                new_value=new_value,
                operator_id=operator_id,
                remark=remark,
            )
        except Exception:
            logger.exception("Failed to record event for node %s", node_id)

    # ── 节点 CRUD ──

    async def create_node(self, cmd: CreateNodeCommand) -> NodeOperationResult:
        """创建节点。

        权限要求：仅建设单位（company_type == "建设单位"）人员可创建。
        管理员快捷通道：创建人 level >= 5 时自动激活，跳过审批。
        """
        # ── 责任人默认值 + FK 校验 ──
        responsible_user_id = getattr(cmd, 'responsible_user_id', '') or cmd.creator_id
        if self._user_repo:
            user = await asyncio.to_thread(self._user_repo.get_user, responsible_user_id)
            if user is None:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"责任人 {responsible_user_id} 不存在于用户表中",
                    error_code="40002",
                )

        # ── 权限校验 + 管理员级别检测 ──
        is_admin = False
        if cmd.creator_id and self._user_repo:
            user = await asyncio.to_thread(self._user_repo.get_user, cmd.creator_id)
            if user:
                # 管理员（Level 5/6）跳过审批
                is_admin = getattr(user, "level", 0) >= 5
                # 非管理员：仅建设单位人员可创建节点
                if not is_admin:
                    company_type = await asyncio.to_thread(
                        self._get_creator_company_type, cmd.creator_id,
                    )
                    if company_type and company_type != "建设单位":
                        return NodeOperationResult(
                            success=False, node_id=cmd.node_id,
                            message="仅建设单位人员可创建全景节点",
                            error_code="40301",
                        )

        # 管理员直接以 CONDITIONS_NOT_MET 创建，普通用户以 NOT_ACTIVATED 创建
        initial_status = CONDITIONS_NOT_MET if is_admin else NOT_ACTIVATED

        # ── 关联单位从参与单位中推导：优先取管理单位，否则取首项，兜底"建设单位" ──
        participant_company_ids = getattr(cmd, 'participant_company_ids', None) or []
        related_company_id = _derive_related_company_from_participants(
            participant_company_ids, default="建设单位"
        )

        node = await asyncio.to_thread(
            self._node_repo.create,
            project_id=cmd.project_id,
            node_id=cmd.node_id,
            node_name=cmd.node_name,
            owner_dept_id=cmd.owner_dept_id,
            related_company_id=related_company_id,
            deadline=cmd.deadline,
            creator_id=cmd.creator_id,
            remark=cmd.remark,
            status=initial_status,
            responsible_user_id=responsible_user_id,
            node_type=getattr(cmd, 'node_type', 'WORK_PACKAGE'),
        )

        # ── 写入参与单位（多对多关联）──
        if participant_company_ids:
            await asyncio.to_thread(
                self._npc_repo.replace_all,
                cmd.node_id, participant_company_ids, cmd.creator_id,
            )

        now_iso = self._now_iso()

        if is_admin:
            # ── 管理员自动激活 ──
            # 记录审批信息（创建人即审批人）
            await asyncio.to_thread(
                self._node_repo.update_fields,
                cmd.node_id,
                approver_id=cmd.creator_id,
                approved_at=now_iso,
            )

            self._record_event(
                node_id=cmd.node_id,
                event_type="node_created_auto_activated",
                new_value=json.dumps({
                    "node_name": cmd.node_name,
                    "project_id": cmd.project_id,
                    "owner_dept_id": cmd.owner_dept_id,
                    "creator_id": cmd.creator_id,
                    "auto_activated": True,
                }),
                operator_id=cmd.creator_id,
                remark=f"管理员创建节点，自动激活（跳过审批）",
            )

            # 通知：管理员创建并自动激活
            if self._outbound_bus:
                try:
                    self._outbound_bus.publish("node_auto_activated", {
                        "node_id": cmd.node_id,
                        "node_name": cmd.node_name,
                        "project_id": cmd.project_id,
                        "owner_dept_id": cmd.owner_dept_id,
                        "creator_id": cmd.creator_id,
                    })
                except Exception:
                    logger.exception("Failed to publish node_auto_activated event")

            logger.info("Node created (auto-activated by admin): %s (project=%s, dept=%s)",
                        cmd.node_id, cmd.project_id, cmd.owner_dept_id)
            return NodeOperationResult(
                success=True,
                node_id=cmd.node_id,
                status=CONDITIONS_NOT_MET,
                progress=_parse_decimal(node.progress),
                message=f"节点「{cmd.node_name}」已创建并激活",
            )
        else:
            # ── 普通用户：待审批 ──
            self._record_event(
                node_id=cmd.node_id,
                event_type="node_created_pending_approval",
                new_value=json.dumps({
                    "node_name": cmd.node_name,
                    "project_id": cmd.project_id,
                    "owner_dept_id": cmd.owner_dept_id,
                    "creator_id": cmd.creator_id,
                }),
                operator_id=cmd.creator_id,
                remark=f"节点创建，待「{cmd.owner_dept_id}」部门负责人审批",
            )

            # 通知：通过 OutboundEventBus 发布待审批事件
            if self._outbound_bus:
                try:
                    self._outbound_bus.publish("node_pending_approval", {
                        "node_id": cmd.node_id,
                        "node_name": cmd.node_name,
                        "project_id": cmd.project_id,
                        "owner_dept_id": cmd.owner_dept_id,
                        "creator_id": cmd.creator_id,
                    })
                except Exception:
                    logger.exception("Failed to publish node_pending_approval event")

            logger.info("Node created (pending approval): %s (project=%s, dept=%s)",
                        cmd.node_id, cmd.project_id, cmd.owner_dept_id)
            return NodeOperationResult(
                success=True,
                node_id=cmd.node_id,
                status=node.status,
                progress=_parse_decimal(node.progress),
                message=f"节点「{cmd.node_name}」已创建，待「{cmd.owner_dept_id}」部门负责人审批后启用",
            )

    async def update_node(self, cmd: UpdateNodeCommand) -> NodeOperationResult:
        """更新节点字段。"""
        updates = {}
        if cmd.node_name is not None:
            updates["node_name"] = cmd.node_name
        if cmd.deadline is not None:
            updates["deadline"] = cmd.deadline
        if cmd.owner_dept_id is not None:
            updates["owner_dept_id"] = cmd.owner_dept_id
        if cmd.remark is not None:
            updates["remark"] = cmd.remark

        if not updates:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="无更新字段")

        old_node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if old_node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        node = await asyncio.to_thread(self._node_repo.update_fields, cmd.node_id, **updates)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="更新失败")

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_updated",
            old_value=json.dumps({"node_name": old_node.node_name, "deadline": old_node.deadline}),
            new_value=json.dumps(updates),
            operator_id=cmd.operator_id,
            remark="节点字段更新",
        )

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
            message="节点更新成功",
        )

    async def discard_node(self, cmd: DiscardNodeCommand) -> NodeOperationResult:
        """废弃节点。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        await asyncio.to_thread(self._node_repo.discard, cmd.node_id)

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_discarded",
            old_value=json.dumps({"status": node.status}),
            operator_id=cmd.operator_id,
            remark="节点废弃",
        )

        return NodeOperationResult(success=True, node_id=cmd.node_id, message="节点已废弃")

    # ── 节点激活（审批）──

    async def activate_node(self, cmd: ActivateNodeCommand) -> NodeOperationResult:
        """激活节点 —— 部门负责人审批通过/拒绝。

        审批通过：NOT_ACTIVATED → CONDITIONS_NOT_MET，正式纳入全景图。
        审批拒绝：节点废弃（is_discarded=True）。

        权限要求：
          - 审批人必须是该节点 owner_dept_id 对应部门的负责人，或 L5+ 管理员
        """
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # 状态校验：仅 NOT_ACTIVATED 可审批
        if node.status != NOT_ACTIVATED:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"节点状态为「{node.status}」，非待审批状态，无法审批",
            )

        # 权限校验：审批人须为部门负责人或 L5+ 管理员
        if cmd.approver_id and self._user_repo:
            is_authorized = await asyncio.to_thread(
                self._check_approver_permission,
                cmd.approver_id, node.owner_dept_id,
            )
            if not is_authorized:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"仅「{node.owner_dept_id}」部门负责人或管理员（L5+）可审批此节点",
                    error_code="40302",
                )

        now_iso = self._now_iso()

        if cmd.approved:
            # 审批通过：NOT_ACTIVATED → CONDITIONS_NOT_MET
            await asyncio.to_thread(self._node_repo.update_status, cmd.node_id, CONDITIONS_NOT_MET)
            # 记录审批信息
            await asyncio.to_thread(
                self._node_repo.update_fields,
                cmd.node_id,
                approver_id=cmd.approver_id,
                approved_at=now_iso,
            )

            self._record_event(
                node_id=cmd.node_id,
                event_type="node_activated",
                old_value=json.dumps({"status": NOT_ACTIVATED}),
                new_value=json.dumps({"status": CONDITIONS_NOT_MET, "approver_id": cmd.approver_id}),
                operator_id=cmd.approver_id,
                remark=f"审批通过：{cmd.remark}" if cmd.remark else "审批通过",
            )

            logger.info("Node activated: %s by %s", cmd.node_id, cmd.approver_id)
            return NodeOperationResult(
                success=True,
                node_id=cmd.node_id,
                status=CONDITIONS_NOT_MET,
                progress=_parse_decimal(node.progress),
                message=f"节点「{node.node_name}」审批通过，已启用",
            )
        else:
            # 审批拒绝：废弃节点
            await asyncio.to_thread(self._node_repo.discard, cmd.node_id)

            self._record_event(
                node_id=cmd.node_id,
                event_type="node_activation_rejected",
                old_value=json.dumps({"status": NOT_ACTIVATED}),
                new_value=json.dumps({"is_discarded": True, "approver_id": cmd.approver_id}),
                operator_id=cmd.approver_id,
                remark=f"审批拒绝：{cmd.remark}" if cmd.remark else "审批拒绝",
            )

            logger.info("Node activation rejected: %s by %s", cmd.node_id, cmd.approver_id)
            return NodeOperationResult(
                success=True,
                node_id=cmd.node_id,
                status="DISCARDED",
                progress=0.0,
                message=f"节点「{node.node_name}」审批拒绝，已废弃",
            )

    # ── 权限辅助 ──

    def _get_creator_company_type(self, creator_id: str) -> str:
        """通过 creator_id 查询其所属单位的 company_type。"""
        if self._user_repo is None:
            return ""
        user = self._user_repo.get_user(creator_id)
        if user is None:
            return ""
        company = self._user_repo.get_company(user.company) if user.company else None
        return company.type if company else ""

    def _check_approver_permission(self, approver_id: str, owner_dept_id: str) -> bool:
        """检查审批人是否有权审批指定部门的节点。

        L5+ 管理员可审批任何部门；部门负责人可审批本部门。
        """
        if self._user_repo is None:
            return True  # 无 user_repo 时放行

        user = self._user_repo.get_user(approver_id)
        if user is None:
            return False

        # L5+ 管理员：可审批任何部门
        if user.level >= 5:
            return True

        # 部门负责人：user.department 包含 owner_dept_id
        if user.department and owner_dept_id:
            import json as _json
            try:
                depts = _json.loads(user.department) if isinstance(user.department, str) else user.department
                if isinstance(depts, list) and owner_dept_id in depts:
                    return True
            except (_json.JSONDecodeError, TypeError):
                pass
            # 直接字符串匹配作为兜底
            if user.department == owner_dept_id:
                return True

        return False

    def _check_submission_permission(self, submitter_id: str, node) -> bool:
        """检查提交人是否有权提交节点成果。

        规则：提交人必须是节点责任人或同部门（owner_dept_id）人员。
        """
        if not submitter_id:
            return True  # 无提交人信息时放行（由 API 层把关）
        if self._user_repo is None:
            return True

        # 节点责任人可直接提交
        resp_id = getattr(node, 'responsible_user_id', '')
        if resp_id and submitter_id == resp_id:
            return True

        # 同部门人员可提交
        user = self._user_repo.get_user(submitter_id)
        if user is None:
            return False
        owner_dept = getattr(node, 'owner_dept_id', '')
        if user.department and owner_dept:
            if user.department == owner_dept:
                return True
            import json as _json
            try:
                depts = _json.loads(user.department) if isinstance(user.department, str) else user.department
                if isinstance(depts, list) and owner_dept in depts:
                    return True
            except (_json.JSONDecodeError, TypeError):
                pass

        return False

    # ── 成果管理 ──

    async def create_deliverable(self, cmd: CreateDeliverableCommand) -> NodeOperationResult:
        """为节点新增成果。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        seq = await asyncio.to_thread(self._deliv_repo.get_next_seq, cmd.node_id)
        deliverable_id = self._deliv_repo.generate_deliverable_id(cmd.node_id, seq)

        await asyncio.to_thread(
            self._deliv_repo.create,
            deliverable_id=deliverable_id,
            node_id=cmd.node_id,
            deliverable_name=cmd.deliverable_name,
            target_amount=_to_decimal_str(cmd.target_amount, precision=2),
            unit=cmd.unit,
            is_required=cmd.is_required,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="deliverable_updated",
            new_value=json.dumps({"deliverable_id": deliverable_id, "name": cmd.deliverable_name}),
            operator_id=cmd.operator_id,
            remark=f"新增成果：{cmd.deliverable_name}",
        )

        # 新增成果可能改变完成度，触发状态重算
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message=f"成果「{cmd.deliverable_name}」创建成功",
        )

    async def update_deliverable_progress(self, cmd: UpdateDeliverableProgressCommand) -> NodeOperationResult:
        """更新成果进度——核心入口，触发状态流转。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(
                success=False, node_id="",
                message=f"成果 {cmd.deliverable_id} 不存在",
            )

        old_amount = deliv.current_amount
        amount_str = _to_decimal_str(cmd.current_amount, precision=2)

        await asyncio.to_thread(
            self._deliv_repo.update_progress,
            cmd.deliverable_id,
            amount_str,
            cmd.file_id,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_updated",
            old_value=json.dumps({"current_amount": old_amount}),
            new_value=json.dumps({"current_amount": amount_str, "file_id": cmd.file_id}),
            operator_id=cmd.operator_id,
            remark=f"成果进度更新：{old_amount} → {amount_str}",
        )

        # 关键：成果进度更新 → 触发状态重算
        result = await self._recalc_node_status(deliv.node_id)
        return result

    # ── 依赖管理 ──

    async def add_dependency(self, cmd: AddDependencyCommand) -> NodeOperationResult:
        """添加依赖——含循环检测前置。"""
        # 1. 查上游成果所属节点
        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, cmd.depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"成果 {cmd.depends_on_deliverable_id} 不存在",
            )

        upstream_node_id = dep_deliv.node_id

        # 2. 禁止自己依赖自己
        if upstream_node_id == cmd.node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="节点不能依赖自己的成果",
                error_code="40001",
            )

        # 3. BFS 循环检测
        cycle_result = await self._check_cycle(cmd.node_id, cmd.depends_on_deliverable_id)
        if cycle_result.has_cycle:
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message=f"循环依赖：{' → '.join(cycle_result.cycle_path)}",
                error_code="40001",
            )

        # 4. 检查重复
        if await asyncio.to_thread(
            self._dep_repo.exists, cmd.node_id, cmd.depends_on_deliverable_id,
        ):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="该依赖关系已存在",
            )

        # 5. 创建依赖
        weight_str = _to_decimal_str(cmd.weight, precision=4)
        await asyncio.to_thread(
            self._dep_repo.create,
            node_id=cmd.node_id,
            depends_on_deliverable_id=cmd.depends_on_deliverable_id,
            depends_on_node_id=upstream_node_id,
            weight=weight_str,
            dependency_type=cmd.dependency_type,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="dependency_added",
            new_value=json.dumps({
                "depends_on_deliverable_id": cmd.depends_on_deliverable_id,
                "depends_on_node_id": upstream_node_id,
                "weight": weight_str,
            }),
            operator_id=cmd.operator_id,
            remark=f"新增依赖：{cmd.depends_on_deliverable_id} (权重{weight_str})",
        )

        if cmd.weight >= 999.0:
            self._record_event(
                node_id=cmd.node_id,
                event_type="BLOCKING_CONDITION_ADDED",
                new_value=json.dumps({"deliverable_id": cmd.depends_on_deliverable_id}),
                operator_id=cmd.operator_id,
                remark="人工阻塞条件",
            )

        # 6. 依赖变更 → 重新计算状态
        await self._recalc_node_status(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message="依赖添加成功",
        )

    async def remove_dependency(self, cmd: RemoveDependencyCommand) -> NodeOperationResult:
        """移除依赖。"""
        dep = await asyncio.to_thread(self._dep_repo.get_by_id, cmd.dependency_id)
        if dep is None:
            return NodeOperationResult(success=False, message="依赖不存在")

        node_id = dep.node_id
        is_blocking = _parse_decimal(dep.weight) >= 999.0

        await asyncio.to_thread(self._dep_repo.delete, cmd.dependency_id)

        self._record_event(
            node_id=node_id,
            event_type="dependency_removed",
            old_value=json.dumps({"depends_on_deliverable_id": dep.depends_on_deliverable_id}),
            operator_id=cmd.operator_id,
            remark="移除依赖",
        )

        if is_blocking:
            self._record_event(
                node_id=node_id,
                event_type="BLOCKING_CONDITION_REMOVED",
                operator_id=cmd.operator_id,
                remark="解除阻塞条件",
            )

        # 依赖移除 → 重新计算状态
        await self._recalc_node_status(node_id)

        return NodeOperationResult(success=True, node_id=node_id, message="依赖已移除")

    # ── 责任人管理 ──

    async def assign_node(self, cmd: AssignNodeCommand) -> NodeOperationResult:
        """变更节点责任人。需权限校验。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # FK 校验
        if self._user_repo:
            user = await asyncio.to_thread(self._user_repo.get_user, cmd.responsible_user_id)
            if user is None:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"目标责任人 {cmd.responsible_user_id} 不存在于用户表中",
                    error_code="40002",
                )

        # 权限校验：部门负责人或 L5+
        if cmd.operator_id and self._user_repo:
            is_authorized = await asyncio.to_thread(
                self._check_approver_permission, cmd.operator_id, node.owner_dept_id,
            )
            if not is_authorized:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"仅「{node.owner_dept_id}」部门负责人或管理员（L5+）可变更责任人",
                    error_code="40302",
                )

        await asyncio.to_thread(
            self._node_repo.update_fields, cmd.node_id,
            responsible_user_id=cmd.responsible_user_id,
        )

        self._record_event(
            node_id=cmd.node_id,
            event_type="responsible_user_changed",
            new_value=json.dumps({"responsible_user_id": cmd.responsible_user_id}),
            operator_id=cmd.operator_id,
            remark=f"责任人变更",
        )

        return NodeOperationResult(success=True, node_id=cmd.node_id, message="责任人变更成功")

    # ── 参与单位管理 ──

    async def add_participant_company(self, cmd: AddParticipantCompanyCommand) -> NodeOperationResult:
        """添加节点参与单位。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # 检查是否已存在（唯一约束在 DB 层兜底）
        existing = await asyncio.to_thread(self._npc_repo.find_company_ids_by_node, cmd.node_id)
        if cmd.company_id in existing:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="该单位已是参与单位")

        await asyncio.to_thread(self._npc_repo.add, cmd.node_id, cmd.company_id, cmd.operator_id)
        return NodeOperationResult(success=True, node_id=cmd.node_id, message="参与单位添加成功")

    async def remove_participant_company(self, cmd: RemoveParticipantCompanyCommand) -> NodeOperationResult:
        """移除节点参与单位。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        removed = await asyncio.to_thread(self._npc_repo.remove, cmd.node_id, cmd.company_id)
        if not removed:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="该单位不是参与单位")
        return NodeOperationResult(success=True, node_id=cmd.node_id, message="参与单位移除成功")

    async def set_participant_companies(self, cmd: SetParticipantCompaniesCommand) -> NodeOperationResult:
        """全量设置节点参与单位。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        await asyncio.to_thread(self._npc_repo.replace_all, cmd.node_id, cmd.company_ids, cmd.operator_id)
        return NodeOperationResult(success=True, node_id=cmd.node_id, message=f"参与单位已更新（{len(cmd.company_ids)} 个）")

    # ── 成果提交确认工作流 ──

    async def submit_deliverable(self, cmd: SubmitNodeDeliverableCommand) -> NodeOperationResult:
        """提交节点成果（PENDING → SUBMITTED）。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(success=False, node_id="", message=f"成果 {cmd.deliverable_id} 不存在")

        if deliv.submission_status not in ("PENDING", "RETURNED"):
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"成果当前状态为「{deliv.submission_status}」，无法提交",
            )

        # 节点必须 IN_PROGRESS
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, deliv.node_id)
        if node is None or node.status != "IN_PROGRESS":
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"节点状态为「{getattr(node, 'status', '未知')}」，非「IN_PROGRESS」",
            )

        # 权限校验：提交人必须是节点责任人或同部门人员
        if not self._check_submission_permission(cmd.submitted_by, node):
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message="仅节点责任人或同部门人员可提交成果",
                error_code="40303",
            )

        await asyncio.to_thread(
            self._deliv_repo.update_submission_status,
            cmd.deliverable_id,
            "SUBMITTED",
            submitted_by=cmd.submitted_by,
            attachment_file_id=cmd.attachment_file_id,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_submitted",
            new_value=json.dumps({"deliverable_id": cmd.deliverable_id}),
            operator_id=cmd.submitted_by,
            remark="成果提交",
        )

        return NodeOperationResult(success=True, node_id=deliv.node_id, message="成果提交成功")

    async def confirm_deliverable(self, cmd: ConfirmNodeDeliverableCommand) -> NodeOperationResult:
        """确认节点成果（SUBMITTED → CONFIRMED）。触发进度重算。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(success=False, node_id="", message=f"成果 {cmd.deliverable_id} 不存在")

        if deliv.submission_status != "SUBMITTED":
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"成果当前状态为「{deliv.submission_status}」，非「SUBMITTED」",
            )

        await asyncio.to_thread(
            self._deliv_repo.update_submission_status,
            cmd.deliverable_id,
            "CONFIRMED",
            confirmed_by=cmd.confirmed_by,
        )

        # 确认驱动：CONFIRMED 时自动将 current_amount 设为 target_amount
        await asyncio.to_thread(
            self._deliv_repo.update_progress,
            cmd.deliverable_id,
            deliv.target_amount,
            file_id=getattr(deliv, 'attachment_file_id', ''),
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_confirmed",
            new_value=json.dumps({"deliverable_id": cmd.deliverable_id}),
            operator_id=cmd.confirmed_by,
            remark="成果确认",
        )

        # 触发进度重算
        await self._recalc_node_status(deliv.node_id)

        return NodeOperationResult(success=True, node_id=deliv.node_id, message="成果确认成功")

    async def return_deliverable(self, cmd: ReturnNodeDeliverableCommand) -> NodeOperationResult:
        """退回节点成果（SUBMITTED → RETURNED）。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(success=False, node_id="", message=f"成果 {cmd.deliverable_id} 不存在")

        if deliv.submission_status != "SUBMITTED":
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"成果当前状态为「{deliv.submission_status}」，非「SUBMITTED」",
            )

        if not cmd.reason:
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message="退回必须填写原因",
            )

        await asyncio.to_thread(
            self._deliv_repo.update_submission_status,
            cmd.deliverable_id,
            "RETURNED",
            return_reason=cmd.reason,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_returned",
            new_value=json.dumps({"deliverable_id": cmd.deliverable_id, "reason": cmd.reason}),
            operator_id=cmd.returned_by,
            remark=f"成果退回：{cmd.reason}",
        )

        return NodeOperationResult(success=True, node_id=deliv.node_id, message="成果已退回")

    async def resubmit_deliverable(self, cmd: ResubmitNodeDeliverableCommand) -> NodeOperationResult:
        """重新提交节点成果（RETURNED → SUBMITTED）。"""
        deliv = await asyncio.to_thread(self._deliv_repo.get_by_deliverable_id, cmd.deliverable_id)
        if deliv is None:
            return NodeOperationResult(success=False, node_id="", message=f"成果 {cmd.deliverable_id} 不存在")

        if deliv.submission_status != "RETURNED":
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"成果当前状态为「{deliv.submission_status}」，非「RETURNED」",
            )

        await asyncio.to_thread(
            self._deliv_repo.update_submission_status,
            cmd.deliverable_id,
            "SUBMITTED",
            submitted_by=cmd.submitted_by,
            attachment_file_id=cmd.attachment_file_id,
        )

        self._record_event(
            node_id=deliv.node_id,
            event_type="deliverable_resubmitted",
            new_value=json.dumps({"deliverable_id": cmd.deliverable_id}),
            operator_id=cmd.submitted_by,
            remark="成果重新提交",
        )

        return NodeOperationResult(success=True, node_id=deliv.node_id, message="成果重新提交成功")

    # ── 截止时间查询（供调度器 handler 调用）──

    async def find_near_deadline(self, before_minutes: int = 60, limit: int = 100) -> list:
        """查询即将到期的节点。"""
        return await asyncio.to_thread(
            self._node_repo.find_near_deadline, before_minutes, limit,
        )

    async def find_overdue(self, limit: int = 100) -> list:
        """查询已超期的节点。"""
        return await asyncio.to_thread(self._node_repo.find_overdue, limit)

    # ── 查询方法 ──

    async def get_node_detail(self, node_id: str) -> dict | None:
        """查询节点详情（含成果、依赖）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)
        participant_company_ids = await asyncio.to_thread(self._npc_repo.find_company_ids_by_node, node_id)

        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "project_id": node.project_id,
            "status": node.status,
            "deadline": node.deadline,
            "owner_dept_id": node.owner_dept_id,
            "related_company_id": _derive_related_company_from_participants(participant_company_ids, default=node.related_company_id),
            "participant_company_ids": participant_company_ids,
            "remark": node.remark,
            "is_discarded": node.is_discarded,
            "created_at": node.created_at,
            "deliverables": [
                {
                    "deliverable_id": d.deliverable_id,
                    "deliverable_name": d.deliverable_name,
                    "target_amount": _parse_decimal(d.target_amount),
                    "current_amount": _parse_decimal(d.current_amount),
                    "unit": d.unit,
                    "is_required": d.is_required,
                    "file_id": d.file_id,
                    "completed_at": d.completed_at,
                }
                for d in delivs
            ],
            "dependencies": [
                {
                    "id": d.id,
                    "depends_on_deliverable_id": d.depends_on_deliverable_id,
                    "depends_on_node_id": d.depends_on_node_id,
                    "weight": _parse_decimal(d.weight),
                    "dependency_type": d.dependency_type,
                }
                for d in deps
            ],
        }

    # ── 状态重算核心 ──

    async def _recalc_node_status(self, node_id: str) -> NodeOperationResult:
        """重新计算节点状态（文件上传/成果更新/依赖变更时触发）。

        流程：
        1. 加载节点快照（含成果、依赖）
        2. 构建 deliverable_file_status 映射
        3. 调用引擎 determine_node_status
        4. 如有变更，写入 DB + 记录事件
        """
        snap = await self._build_snapshot(node_id)
        if snap is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")

        # NOT_ACTIVATED 节点不参与正常三态流转计算
        if snap.status == NOT_ACTIVATED:
            return NodeOperationResult(
                success=True, node_id=node_id, status=NOT_ACTIVATED, progress=0.0,
                message="节点尚未启用，不进行状态计算",
            )

        # 构建 deliverable_file_status
        file_status = {}
        for dep in snap.dependencies:
            # 检查依赖的成果文件是否已上传完成
            dep_deliv = await asyncio.to_thread(
                self._deliv_repo.get_by_deliverable_id, dep.depends_on_deliverable_id,
            )
            if dep_deliv:
                current = _parse_decimal(dep_deliv.current_amount)
                target = max(_parse_decimal(dep_deliv.target_amount), 0.001)
                file_status[dep.depends_on_deliverable_id] = (current >= target)

        old_status = snap.status
        old_progress = snap.progress

        # 调用引擎
        new_status = determine_node_status(
            snap.dependencies, snap.deliverables, file_status, snap.children,
        )

        # 进度计算
        new_progress = calc_deliverable_completion(snap.deliverables) * 100.0

        # 检查是否需要更新
        status_changed = (new_status != old_status)
        progress_changed = abs(new_progress - old_progress) > 0.001

        if not status_changed and not progress_changed:
            return NodeOperationResult(
                success=True, node_id=node_id, status=old_status, progress=old_progress,
                message="状态无变化",
            )

        # 写入 DB
        await asyncio.to_thread(self._node_repo.update_progress, node_id, new_progress)
        if status_changed:
            await asyncio.to_thread(self._node_repo.update_status, node_id, new_status)

            # 记录状态变更事件
            self._record_event(
                node_id=node_id,
                event_type="status_changed",
                old_value=json.dumps({"status": old_status}),
                new_value=json.dumps({"status": new_status, "progress": new_progress}),
                remark="状态自动流转",
            )
            if new_status == COMPLETED:
                self._record_event(
                    node_id=node_id,
                    event_type="auto_triggered",
                    remark="节点已完成",
                )

        logger.info(
            "Node %s recalc: status %s->%s, progress %.2f->%.2f",
            node_id, old_status, new_status, old_progress, new_progress,
        )

        return NodeOperationResult(
            success=True,
            node_id=node_id,
            status=new_status,
            progress=new_progress,
            message=f"状态重算完成：{old_status} → {new_status}",
        )

    async def _build_snapshot(self, node_id: str) -> NodeSnapshot | None:
        """构建节点快照（供引擎计算）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return None

        snap = NodeSnapshot(
            node_id=node.node_id,
            status=node.status,
            progress=_parse_decimal(node.progress),
        )

        # 加载依赖
        deps = await asyncio.to_thread(self._dep_repo.find_by_node, node_id)
        snap.dependencies = [
            DependencySnapshot(
                depends_on_deliverable_id=d.depends_on_deliverable_id,
                depends_on_node_id=d.depends_on_node_id,
                weight=_parse_decimal(d.weight),
                dependency_type=d.dependency_type,
            )
            for d in deps
        ]

        # 加载成果
        delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, node_id)
        snap.deliverables = [
            DeliverableSnapshot(
                deliverable_id=d.deliverable_id,
                target_amount=_parse_decimal(d.target_amount),
                current_amount=_parse_decimal(d.current_amount),
                is_required=d.is_required,
                file_id=d.file_id,
            )
            for d in delivs
        ]

        return snap

    # ── 循环检测辅助 ──

    async def _check_cycle(self, node_id: str, depends_on_deliverable_id: str) -> CycleCheckResult:
        """BFS 循环依赖检测。"""
        # 查询成果所属上游节点
        dep_deliv = await asyncio.to_thread(
            self._deliv_repo.get_by_deliverable_id, depends_on_deliverable_id,
        )
        if dep_deliv is None:
            return CycleCheckResult(has_cycle=False)

        upstream_node = dep_deliv.node_id
        if upstream_node == node_id:
            return CycleCheckResult(has_cycle=True, cycle_path=[node_id, node_id],
                                   message="节点不能依赖自己的成果")

        # 获取 node_id 的项目上下文
        node_obj = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node_obj is None:
            return CycleCheckResult(has_cycle=False)

        # 构建 {deliverable_id: node_id} 映射（项目范围内）
        all_nodes = await asyncio.to_thread(self._node_repo.find_by_project, node_obj.project_id)
        all_node_ids = [n.node_id for n in all_nodes]

        deliverable_to_node: dict[str, str] = {}
        for nid in all_node_ids:
            delivs = await asyncio.to_thread(self._deliv_repo.find_by_node, nid)
            for d in delivs:
                deliverable_to_node[d.deliverable_id] = d.node_id

        # 构建 {node_id: [upstream_node_id]} — node 依赖了哪些上游节点
        node_deps: dict[str, list[str]] = {}
        for nid in all_node_ids:
            deps = await asyncio.to_thread(self._dep_repo.find_by_node, nid)
            upstream_ids = list(set(d.depends_on_node_id for d in deps))
            node_deps[nid] = upstream_ids

        has_cycle, path = detect_cycle(
            node_id, depends_on_deliverable_id, deliverable_to_node, node_deps,
        )

        return CycleCheckResult(
            has_cycle=has_cycle,
            cycle_path=path,
            message=" → ".join(path) if has_cycle else "",
        )
