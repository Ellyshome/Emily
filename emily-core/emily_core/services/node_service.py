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
    MountChildCommand,
    UnmountChildCommand,
    NodeOperationResult,
    CycleCheckResult,
    StateTransitionResult,
)
from .node_state_machine import (
    NOT_ACTIVATED,
    CONDITIONS_NOT_MET,
    IN_PROGRESS,
    COMPLETED,
    NODE_TYPE_MILESTONE,
    NODE_TYPE_TASK,
    NodeSnapshot,
    DependencySnapshot,
    DeliverableSnapshot,
    ChildSnapshot,
    calc_dependency_satisfaction,
    determine_node_status,
    detect_cycle,
)
from ..repositories.node_repo import (
    ProjectNodeRepo,
    NodeDependencyRepo,
    NodeDeliverableRepo,
    NodeEventRepo,
    NodeParticipantCompanyRepo,
    NodeParticipantRepo,
    NodeAccessibleFileRepo,
    _parse_decimal,
    _to_decimal_str,
)
from ..repositories.file_repo import FileRepository
from ..infrastructure.database.models import _new_id
from ..infrastructure.logging.audit import audited

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

# 单父节点子节点数量上限（需求 §3.4 父子节点层级）
MAX_CHILDREN_PER_PARENT = 100
# 树深度安全上限（仅防止脏数据导致无限递归/成环；两层制下里程碑可任意嵌套，
# 结构约束由"有子节点即里程碑、无子节点即任务"的派生规则自动保证）
MAX_TREE_DEPTH = 10


class NodeService:
    """全景节点图核心业务 Service。"""

    def __init__(
        self,
        node_repo: ProjectNodeRepo | None = None,
        dependency_repo: NodeDependencyRepo | None = None,
        deliverable_repo: NodeDeliverableRepo | None = None,
        event_repo: NodeEventRepo | None = None,
        npc_repo: NodeParticipantCompanyRepo | None = None,
        participant_repo: NodeParticipantRepo | None = None,
        naf_repo: NodeAccessibleFileRepo | None = None,
        user_repo=None,
        outbound_bus=None,
    ):
        self._node_repo = node_repo or ProjectNodeRepo()
        self._dep_repo = dependency_repo or NodeDependencyRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()
        self._event_repo = event_repo or NodeEventRepo()
        self._npc_repo = npc_repo or NodeParticipantCompanyRepo()
        self._participant_repo = participant_repo or NodeParticipantRepo()
        self._naf_repo = naf_repo or NodeAccessibleFileRepo()
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
            # 双写：同步累积到统一项目事件（project_events，NODE_EVENT）
            try:
                from ..services.project_event_accumulator import ProjectEventAccumulator
                ProjectEventAccumulator.record_node_event(
                    title=remark or event_type,
                    node_id=node_id,
                    event_type=event_type,
                    actor_id=operator_id or None,
                    occurred_at=self._now_iso(),
                    old_value=old_value,
                    new_value=new_value,
                    remark=remark,
                )
            except Exception as e:
                logger.debug("ProjectEvent double-write failed: %s", e)
        except Exception:
            logger.exception("Failed to record event for node %s", node_id)

    # ── 节点 CRUD ──

    @audited(category="node", action="created", target_type="node", actor_arg="cmd.creator_id", target_arg="cmd.node_id")
    async def create_node(self, cmd: CreateNodeCommand) -> NodeOperationResult:
        """创建节点（入库即生效，无审批阻断）。

        权限要求：仅建设单位（company_type == "建设单位"）人员可创建（管理员不限）。
        所有节点创建后即为 CONDITIONS_NOT_MET（信息先记录、后认可，PRD US-04），
        不再存在"待审批才生效"的阻断态。
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

        # ── 权限校验 ──
        if cmd.creator_id and self._user_repo:
            user = await asyncio.to_thread(self._user_repo.get_user, cmd.creator_id)
            if user:
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

        # 入库即生效：所有节点初始状态均为 CONDITIONS_NOT_MET（不再阻断）
        initial_status = CONDITIONS_NOT_MET

        # ── 单位引用统一解析（口径见 docs/Spec/项目归属与可见范围_Spec.md）──
        # 中文单位名/类型只是输入写法，必须解析为 company_info.id 后才落库。
        from .company_resolver import CompanyResolver

        raw_participants = getattr(cmd, 'participant_company_ids', None) or []
        participant_company_ids = [
            cid for cid in (CompanyResolver.resolve(x, cmd.project_id) for x in raw_participants) if cid
        ]

        related_ref = str(getattr(cmd, 'related_company_id', '') or "").strip()
        related_company_id = CompanyResolver.resolve(related_ref, cmd.project_id) if related_ref else None

        # 参与单位兜底顺序：显式传入 → 关联单位 → 创建人所属企业
        # （归属口径：可见范围由「企业参与节点」推导；不登记则所有人都看不到该项目态势）
        if not participant_company_ids:
            if related_company_id:
                participant_company_ids = [related_company_id]
            else:
                participant_company_ids = await asyncio.to_thread(
                    self._resolve_creator_company_ids, cmd.creator_id,
                )

        # 关联单位兜底：未显式指定时从参与单位推导（管理单位优先）
        if not related_company_id:
            related_company_id = _derive_related_company_from_participants(
                participant_company_ids, default=""
            )

        node = await asyncio.to_thread(
            self._node_repo.create,
            project_id=cmd.project_id,
            node_id=cmd.node_id,
            node_name=cmd.node_name,
            related_company_id=related_company_id,
            deadline=cmd.deadline,
            creator_id=cmd.creator_id,
            remark=cmd.remark,
            status=initial_status,
            responsible_user_id=responsible_user_id,
            node_type=getattr(cmd, 'node_type', NODE_TYPE_TASK),
        )

        # ── 写入参与单位（多对多关联）──
        if participant_company_ids:
            await asyncio.to_thread(
                self._npc_repo.replace_all,
                cmd.node_id, participant_company_ids, cmd.creator_id,
            )

        self._record_event(
            node_id=cmd.node_id,
            event_type="node_created",
            new_value=json.dumps({
                "node_name": cmd.node_name,
                "project_id": cmd.project_id,
                "creator_id": cmd.creator_id,
                "status": initial_status,
            }),
            operator_id=cmd.creator_id,
            remark="节点创建（入库即生效，无需审批）",
        )

        logger.info("Node created: %s (project=%s, status=%s)",
                    cmd.node_id, cmd.project_id, initial_status)
        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            status=initial_status,
            progress=_parse_decimal(node.progress),
            message=f"节点「{cmd.node_name}」已创建并入库（状态：条件未满足）",
        )

    @audited(category="node", action="updated", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
    async def update_node(self, cmd: UpdateNodeCommand) -> NodeOperationResult:
        """更新节点字段。"""
        updates = {}
        if cmd.node_name is not None:
            updates["node_name"] = cmd.node_name
        if cmd.deadline is not None:
            updates["deadline"] = cmd.deadline
        if cmd.remark is not None:
            updates["remark"] = cmd.remark

        if not updates and cmd.related_company_id is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="无更新字段")

        old_node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if old_node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        # 关联单位：中文写法需解析为 company_info.id；解析不出即失败（不静默存标签/不静默忽略）
        if cmd.related_company_id is not None:
            from .company_resolver import CompanyResolver
            ref = str(cmd.related_company_id or "").strip()
            resolved = CompanyResolver.resolve(ref, getattr(old_node, "project_id", "")) if ref else ""
            if ref and not resolved:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message=f"关联单位「{ref}」无法唯一解析为单位 ID，请改用单位全称或 ID",
                )
            updates["related_company_id"] = resolved or ""

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

    @audited(category="node", action="discarded", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
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

    def _check_operator_level(self, operator_id: str, min_level: int) -> bool:
        """检查操作人等级是否达到要求（替代原"部门负责人"判定，PRD R1）。

        部门维度已移除：不再以"是否本部门负责人"判定，改以等级门槛判定。
        无 user_repo 时放行（由上层把关）。
        """
        if self._user_repo is None:
            return True
        user = self._user_repo.get_user(operator_id)
        if user is None:
            return False
        return getattr(user, "level", 0) >= min_level

    def _check_submission_permission(self, submitter_id: str, node) -> bool:
        """检查提交人是否有权提交节点成果。

        规则（去部门化后）：提交人须为节点责任人、管理员（L5+），
        或节点参与单位的人员（以「企业归属」替代原「同部门」，PRD R1）。
        """
        if not submitter_id:
            return True  # 无提交人信息时放行（由 API 层把关）
        if self._user_repo is None:
            return True

        # 节点责任人可直接提交
        resp_id = getattr(node, 'responsible_user_id', '')
        if resp_id and submitter_id == resp_id:
            return True

        user = self._user_repo.get_user(submitter_id)
        if user is None:
            return False

        # 管理员放行
        if getattr(user, "level", 0) >= 5:
            return True

        # 节点参与单位人员可提交（企业归属，非部门）
        if user.company:
            try:
                participant_ids = self._npc_repo.find_company_ids_by_node(
                    getattr(node, 'node_id', '')
                )
                if user.company in participant_ids:
                    return True
            except Exception:
                logger.warning("submit permission: participant lookup failed node=%s",
                               getattr(node, 'node_id', ''))

        return False

    # ── 成果管理 ──

    @audited(category="node", action="deliverable_created", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
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
        await self._recalc_ancestors(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message=f"成果「{cmd.deliverable_name}」创建成功",
        )

    @audited(category="node", action="deliverable_progress_updated", target_type="deliverable", actor_arg="cmd.operator_id", target_arg="cmd.deliverable_id")
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

        # 关键：成果进度更新 → 触发状态重算（并向上传播到父级里程碑）
        result = await self._recalc_node_status(deliv.node_id)
        await self._recalc_ancestors(deliv.node_id)
        return result

    # ── 依赖管理 ──

    @audited(category="node", action="dependency_added", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
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
        await self._recalc_ancestors(cmd.node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.node_id,
            message="依赖添加成功",
        )

    @audited(category="node", action="dependency_removed", target_type="dependency", actor_arg="cmd.operator_id", target_arg="cmd.dependency_id")
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
        await self._recalc_ancestors(node_id)

        return NodeOperationResult(success=True, node_id=node_id, message="依赖已移除")

    # ── 父子节点挂载 ──

    @audited(category="node", action="child_mounted", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.parent_node_id")
    async def mount_child(self, cmd: MountChildCommand) -> NodeOperationResult:
        """挂载子节点。

        校验：数量上限、树深度安全上限、循环依赖（parent 不能是 child 的后代）。
        通过后设置 child 的 parent_node_id + child_weight，刷新两端节点类型，
        并触发父节点及其祖先的状态重算。
        """
        # 1. 数量上限检查
        count = await asyncio.to_thread(self._node_repo.count_children, cmd.parent_node_id)
        if count >= MAX_CHILDREN_PER_PARENT:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message=f"子节点数量已达上限（{MAX_CHILDREN_PER_PARENT}）",
                error_code="40002",
            )

        # 2. 深度检查 + 循环检查共用 parent 祖先链（一次查询）
        #    深度：仅作安全上限，防止脏数据成环；两层制下里程碑可任意嵌套
        parent_ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.parent_node_id, max_depth=MAX_TREE_DEPTH,
        )
        if len(parent_ancestors) >= MAX_TREE_DEPTH - 1:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message=f"嵌套深度已达安全上限（{MAX_TREE_DEPTH}层），无法继续挂载子节点",
            )
        parent_ancestor_ids = {a.node_id for a in parent_ancestors}

        # 3. 循环检查：parent 与 child 不能已是祖先-后代关系（任一方向）
        #    - parent 已是 child 的后代 → 挂载后形成 parent→child→...→parent 循环
        #    - child 已是 parent 的祖先 → 挂载后形成 child→parent→...→child 循环
        # 3a. parent 不能等于 child（自挂载）
        if cmd.parent_node_id == cmd.child_node_id:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message="节点不能挂载为自身的子节点",
                error_code="40001",
            )
        # 3b. child 若是 parent 的祖先 → 反向挂载形成循环
        if cmd.child_node_id in parent_ancestor_ids:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message="不能将祖先节点挂载为子节点（会形成循环）",
                error_code="40001",
            )
        # 3c. parent 若是 child 的后代 → 正向挂载形成循环
        child_ancestors = await asyncio.to_thread(
            self._node_repo.get_ancestor_chain, cmd.child_node_id, max_depth=MAX_TREE_DEPTH,
        )
        child_descendants_check = cmd.parent_node_id in {a.node_id for a in child_ancestors}
        if child_descendants_check:
            return NodeOperationResult(
                success=False, node_id=cmd.parent_node_id,
                message="不能将后代节点挂载为父节点（会形成循环）",
                error_code="40001",
            )

        # 4. 更新子节点的 parent_node_id + child_weight
        weight_str = _to_decimal_str(cmd.child_weight, precision=4)
        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id=cmd.parent_node_id,
            child_weight=weight_str,
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_mounted",
            new_value=json.dumps({"parent_node_id": cmd.parent_node_id, "child_weight": weight_str}),
            operator_id=cmd.operator_id,
            remark=f"挂载到父节点 {cmd.parent_node_id}",
        )

        # 父子关系变更 → 刷新两端类型（父因有子节点变为里程碑），并自底向上重算状态
        await self._refresh_node_type(cmd.parent_node_id)
        await self._refresh_node_type(cmd.child_node_id)
        await self._recalc_node_status(cmd.parent_node_id)
        await self._recalc_ancestors(cmd.parent_node_id)

        logger.info("Node %s mounted under %s (weight=%s)",
                    cmd.child_node_id, cmd.parent_node_id, weight_str)
        return NodeOperationResult(
            success=True,
            node_id=cmd.child_node_id,
            message=f"子节点「{cmd.child_node_id}」已挂载到 {cmd.parent_node_id}",
        )

    @audited(category="node", action="child_unmounted", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.parent_node_id")
    async def unmount_child(self, cmd: UnmountChildCommand) -> NodeOperationResult:
        """移除子节点（清空 parent_node_id + child_weight）。"""
        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.child_node_id,
            parent_node_id="",
            child_weight="1.0000",
        )

        self._record_event(
            node_id=cmd.child_node_id,
            event_type="child_node_unmounted",
            old_value=json.dumps({"parent_node_id": cmd.parent_node_id}),
            operator_id=cmd.operator_id,
            remark=f"从父节点 {cmd.parent_node_id} 移除",
        )

        await self._recalc_node_status(cmd.parent_node_id)
        await self._refresh_node_type(cmd.parent_node_id)
        await self._recalc_ancestors(cmd.parent_node_id)

        return NodeOperationResult(
            success=True,
            node_id=cmd.child_node_id,
            message=f"子节点「{cmd.child_node_id}」已从 {cmd.parent_node_id} 移除",
        )

    # ── 责任人管理 ──

    @audited(category="node", action="assigned", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
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

        # 权限校验（去部门化）：管理员（L5+）、节点创建人或现责任人可变更责任人
        if cmd.operator_id and self._user_repo:
            if not await asyncio.to_thread(self._check_operator_level, cmd.operator_id, 5):
                is_owner = cmd.operator_id in (
                    getattr(node, "creator_id", ""),
                    getattr(node, "responsible_user_id", ""),
                )
                if not is_owner:
                    return NodeOperationResult(
                        success=False, node_id=cmd.node_id,
                        message="仅管理员（L5+）、节点创建人或现责任人可变更责任人",
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

    @staticmethod
    def _resolve_creator_company_ids(creator_id: str) -> list[str]:
        """创建人所属企业（用于"参与单位默认兜底"，见归属口径 Spec）。"""
        if not creator_id:
            return []
        try:
            from ..repositories.user_repo import UserRepository
            user = UserRepository.get_by_id(creator_id)
            company_id = getattr(user, "company", "") if user is not None else ""
            return [company_id] if company_id else []
        except Exception as e:
            logger.warning("resolve creator company failed user=%s: %s", creator_id, e)
            return []

    @audited(category="node", action="participant_company_added", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
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

    @audited(category="node", action="participant_company_removed", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
    async def remove_participant_company(self, cmd: RemoveParticipantCompanyCommand) -> NodeOperationResult:
        """移除节点参与单位。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        removed = await asyncio.to_thread(self._npc_repo.remove, cmd.node_id, cmd.company_id)
        if not removed:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="该单位不是参与单位")
        return NodeOperationResult(success=True, node_id=cmd.node_id, message="参与单位移除成功")

    @audited(category="node", action="participant_companies_set", target_type="node", actor_arg="cmd.operator_id", target_arg="cmd.node_id")
    async def set_participant_companies(self, cmd: SetParticipantCompaniesCommand) -> NodeOperationResult:
        """全量设置节点参与单位。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        await asyncio.to_thread(self._npc_repo.replace_all, cmd.node_id, cmd.company_ids, cmd.operator_id)
        return NodeOperationResult(success=True, node_id=cmd.node_id, message=f"参与单位已更新（{len(cmd.company_ids)} 个）")

    # ── 节点参与人（自然人）增删 ──

    @audited(category="node", action="participant_added", target_type="node", actor_arg="operator_id", target_arg="node_id")
    async def add_node_participant(self, node_id: str, user_id: str,
                                   operator_id: str, role: str = "participant") -> NodeOperationResult:
        """添加节点参与人（单个用户）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")
        if self._user_repo:
            try:
                user = await asyncio.to_thread(self._user_repo.get_user, user_id)
                if user is None:
                    return NodeOperationResult(success=False, node_id=node_id, message="参与人不存在")
            except Exception:
                pass
        existing = await asyncio.to_thread(self._participant_repo.find, node_id, user_id)
        if existing is not None:
            return NodeOperationResult(success=False, node_id=node_id, message="该用户已是节点参与人")
        await asyncio.to_thread(self._participant_repo.add, node_id, user_id,
                                role or "participant", operator_id)
        self._record_event(node_id, "participant_added", operator_id=operator_id,
                           remark=f"添加参与人：{user_id}（{role or 'participant'}）")
        return NodeOperationResult(success=True, node_id=node_id, message="参与人添加成功")

    @audited(category="node", action="participant_removed", target_type="node", actor_arg="operator_id", target_arg="node_id")
    async def remove_node_participant(self, node_id: str, user_id: str,
                                      operator_id: str) -> NodeOperationResult:
        """移除节点参与人。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")
        removed = await asyncio.to_thread(self._participant_repo.remove, node_id, user_id)
        if not removed:
            return NodeOperationResult(success=False, node_id=node_id, message="该用户不是节点参与人")
        self._record_event(node_id, "participant_removed", operator_id=operator_id,
                           remark=f"移除参与人：{user_id}")
        return NodeOperationResult(success=True, node_id=node_id, message="参与人移除成功")

    # ── 节点共享文件增删 ──

    async def _check_node_file_permission(self, node_id: str, operator_id: str,
                                          action: str = "add") -> tuple[bool, str]:
        """节点共享文件增删授权（缺口 G-6/G-7：服务层单一判定点）。

        - `add`：节点责任人 / L5+ 管理员 / 节点参与单位人员（与成果提交同口径）
        - `remove`：仅 L5+（删除属高危操作，不对一线开放）
        - fail-closed：无操作人信息、用户或节点不存在、权限服务不可用一律拒绝
        """
        if not operator_id:
            return False, "缺少操作人信息，已拒绝（fail-closed）"
        if self._user_repo is None:
            return False, "权限服务未就绪，已拒绝（fail-closed）"

        user = await asyncio.to_thread(self._user_repo.get_user, operator_id)
        if user is None:
            return False, "操作人不存在，已拒绝"

        level = getattr(user, "level", 0) or 0
        if action == "remove":
            if level >= 5:
                return True, ""
            return False, "仅 L5/L6 管理员可移除节点共享文件"

        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return False, "节点不存在"
        if getattr(node, "responsible_user_id", "") == operator_id or level >= 5:
            return True, ""

        company = getattr(user, "company", "") or ""
        if company:
            try:
                participant_ids = await asyncio.to_thread(
                    self._npc_repo.find_company_ids_by_node, node_id)
                if company in participant_ids:
                    return True, ""
            except Exception:
                logger.warning("node file permission: participant lookup failed node=%s", node_id)

        return False, "仅节点责任人、L5+ 管理员或节点参与单位人员可增加节点共享文件"

    @audited(category="node", action="file_added", target_type="node", actor_arg="operator_id", target_arg="node_id")
    async def add_node_file(self, node_id: str, file_id: str,
                            operator_id: str) -> NodeOperationResult:
        """添加节点共享文件（可见范围）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")

        allowed, reason = await self._check_node_file_permission(node_id, operator_id, action="add")
        if not allowed:
            return NodeOperationResult(success=False, node_id=node_id, message=reason)

        file_record = await asyncio.to_thread(FileRepository.get_by_id, file_id)
        if file_record is None or file_record.is_deleted:
            return NodeOperationResult(success=False, node_id=node_id, message="文件不存在")
        if await asyncio.to_thread(self._naf_repo.exists, node_id, file_id):
            return NodeOperationResult(success=False, node_id=node_id, message="该文件已是节点共享文件")
        await asyncio.to_thread(self._naf_repo.create,
                                node_id=node_id, file_id=file_id, added_by=operator_id)
        self._record_event(node_id, "file_added", operator_id=operator_id,
                           remark=f"添加共享文件：{file_id}")
        return NodeOperationResult(success=True, node_id=node_id, message="共享文件添加成功")

    @audited(category="node", action="file_removed", target_type="node", actor_arg="operator_id", target_arg="node_id")
    async def remove_node_file(self, node_id: str, file_id: str,
                               operator_id: str) -> NodeOperationResult:
        """移除节点共享文件（仅 L5+）。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=node_id, message="节点不存在")

        allowed, reason = await self._check_node_file_permission(node_id, operator_id, action="remove")
        if not allowed:
            return NodeOperationResult(success=False, node_id=node_id, message=reason)

        removed = await asyncio.to_thread(self._naf_repo.remove, node_id, file_id)
        if not removed:
            return NodeOperationResult(success=False, node_id=node_id, message="该文件不是节点共享文件")
        self._record_event(node_id, "file_removed", operator_id=operator_id,
                           remark=f"移除共享文件：{file_id}")
        return NodeOperationResult(success=True, node_id=node_id, message="共享文件移除成功")

    # ── 成果提交确认工作流 ──

    @audited(category="node", action="deliverable_submitted", target_type="deliverable", actor_arg="cmd.submitted_by", target_arg="cmd.deliverable_id")
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

        # 门禁：只要节点未终结即可上报（首报即开工，未启动的任务允许首报）
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, deliv.node_id)
        if node is None or node.status == COMPLETED:
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message=f"节点状态为「{getattr(node, 'status', '未知')}」，已完成节点不可再提交成果",
            )

        # 权限校验：提交人必须是节点责任人或同单位人员
        if not self._check_submission_permission(cmd.submitted_by, node):
            return NodeOperationResult(
                success=False, node_id=deliv.node_id,
                message="仅节点责任人、管理员或同单位人员可提交成果",
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

    @audited(category="node", action="deliverable_confirmed", target_type="deliverable", actor_arg="cmd.confirmed_by", target_arg="cmd.deliverable_id")
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

        # 触发状态重算（并向上传播到父级里程碑）
        await self._recalc_node_status(deliv.node_id)
        await self._recalc_ancestors(deliv.node_id)

        return NodeOperationResult(success=True, node_id=deliv.node_id, message="成果确认成功")

    @audited(category="node", action="deliverable_returned", target_type="deliverable", actor_arg="cmd.returned_by", target_arg="cmd.deliverable_id")
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

    @audited(category="node", action="deliverable_resubmitted", target_type="deliverable", actor_arg="cmd.submitted_by", target_arg="cmd.deliverable_id")
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

        ack_by = getattr(node, "acknowledged_by", "") or ""
        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "project_id": node.project_id,
            "status": node.status,
            "deadline": node.deadline,
            "related_company_id": _derive_related_company_from_participants(participant_company_ids, default=node.related_company_id),
            "participant_company_ids": participant_company_ids,
            "remark": node.remark,
            "is_discarded": node.is_discarded,
            "created_at": node.created_at,
            # 签认状态（替代原审批，PRD US-05/US-06）
            "acknowledged": bool(ack_by),
            "acknowledged_by": ack_by,
            "acknowledged_at": getattr(node, "acknowledged_at", "") or "",
            "acknowledged_level": getattr(node, "acknowledged_level", 0) or 0,
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

        # 调用引擎
        new_status = determine_node_status(
            snap.dependencies, snap.deliverables, file_status, snap.children,
        )

        if new_status == old_status:
            return NodeOperationResult(
                success=True, node_id=node_id, status=old_status, progress=snap.progress,
                message="状态无变化",
            )

        # 写入 DB（进度不再由系统计算写库；progress 列保留为存量只读字段）
        await asyncio.to_thread(self._node_repo.update_status, node_id, new_status)

        # 记录状态变更事件
        self._record_event(
            node_id=node_id,
            event_type="status_changed",
            old_value=json.dumps({"status": old_status}),
            new_value=json.dumps({"status": new_status}),
            remark="状态自动流转",
        )
        if new_status == COMPLETED:
            self._record_event(
                node_id=node_id,
                event_type="auto_triggered",
                remark="节点已完成",
            )

        logger.info("Node %s recalc: status %s->%s", node_id, old_status, new_status)

        return NodeOperationResult(
            success=True,
            node_id=node_id,
            status=new_status,
            progress=snap.progress,
            message=f"状态重算完成：{old_status} → {new_status}",
        )

    async def recalc_all_statuses(self, project_id: str = "", limit: int = 5000) -> dict:
        """自底向上重算全部节点状态（维护用途）。

        用于批量导入、状态语义变更后的存量对齐：按树深度从深到浅逐层重算，
        保证父节点聚合时其子节点状态已是新值。

        Returns:
            {"total": 节点总数, "changed": 状态发生变化的节点数}
        """
        if project_id:
            nodes = await asyncio.to_thread(
                self._node_repo.find_by_project, project_id, None, limit,
            )
        else:
            nodes = await asyncio.to_thread(self._node_repo.find_all, limit)

        id_to_parent = {n.node_id: (getattr(n, "parent_node_id", "") or "") for n in nodes}
        ids = set(id_to_parent)

        def _depth(node_id: str) -> int:
            d, cur = 0, id_to_parent.get(node_id, "")
            while cur and cur in ids and d < MAX_TREE_DEPTH:
                d += 1
                cur = id_to_parent.get(cur, "")
            return d

        changed = 0
        for node_id in sorted(ids, key=_depth, reverse=True):
            result = await self._recalc_node_status(node_id)
            if result.success and result.message.startswith("状态重算完成"):
                changed += 1

        logger.info("recalc_all_statuses: total=%d changed=%d", len(ids), changed)
        return {"total": len(ids), "changed": changed}

    async def _recalc_ancestors(self, node_id: str) -> None:
        """自底向上重算祖先链状态。

        里程碑状态由直接子节点聚合而来，因此子节点状态变化必须向上传播；
        某层状态未变化时，其上游也必然不变，可提前终止。
        """
        current = node_id
        for _ in range(MAX_TREE_DEPTH):
            node = await asyncio.to_thread(self._node_repo.get_by_node_id, current)
            parent_id = getattr(node, "parent_node_id", "") if node else ""
            if not parent_id:
                return

            parent = await asyncio.to_thread(self._node_repo.get_by_node_id, parent_id)
            if parent is None:
                return
            old_status = parent.status

            await self._recalc_node_status(parent_id)

            parent_after = await asyncio.to_thread(self._node_repo.get_by_node_id, parent_id)
            current = parent_id
            if parent_after is None or parent_after.status == old_status:
                return

    async def _refresh_node_type(self, node_id: str) -> None:
        """按结构刷新节点类型（单向提升）。

        - 有子节点 → 必为里程碑（自动提升）
        - 无子节点 → 任务，或"尚未分解的里程碑"（由创建时显式声明，不下沉）

        保留"无子节点的里程碑"是业务必需：里程碑登记后可能尚未分解任务，
        其状态即为「未启动」。"已退场类型 WORK_PACKAGE"在此一并修正为任务。
        """
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return

        current = getattr(node, "node_type", "")
        child_count = await asyncio.to_thread(self._node_repo.count_children, node_id)
        if child_count > 0:
            expected = NODE_TYPE_MILESTONE
        else:
            expected = current if current in (NODE_TYPE_MILESTONE, NODE_TYPE_TASK) else NODE_TYPE_TASK

        if current == expected:
            return

        await asyncio.to_thread(self._node_repo.update_fields, node_id, node_type=expected)
        logger.info("Node %s type refreshed: %s -> %s", node_id, current, expected)

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

        # 加载直接子节点（里程碑状态由其聚合而来）
        children = await asyncio.to_thread(self._node_repo.find_children, node_id)
        snap.children = [
            ChildSnapshot(
                node_id=c.node_id,
                status=c.status,
                progress=_parse_decimal(c.progress),
            )
            for c in children
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
