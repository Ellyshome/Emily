"""node_container_service.py —— 收容容器与迁正归位（US-15 / US-16）。

两层收容结构（均为**真实节点**、按需懒创建、项目唯一）：

    {项目根图}
      └── TEMP_MILESTONE   临时里程碑层（项目唯一）——承载无归属的临时节点
            ├── TEMP_TASK          临时任务（档C 落点：无里程碑级归属）→ 标「无归属」
            └── UNCLASSIFIED_SINK  未归类收容节点（项目唯一）——承载未归类业务事件
    {某正式里程碑}
      └── TEMP_TASK        临时任务（档B 落点：有里程碑级归属）→ 标「待认领」

口径（规格约束）：
  · 懒创建：空项目不产生任何容器节点（AC-US-15.3）
  · 不设超时：无自动清理 / 自动迁正路径（AC-US-15.4）
  · 迁正不换编号：只改 parent_node_id + node_role，node_id 恒定（AC-US-16.3 / §4.4-11）
  · 留痕：迁正写节点事件（操作人 + 时间 + 由何处转入何处）；归位写事件自身 reassign_history
  · 容器不是业务节点：不要求必需成果、不计完成度、不产生提醒、不得作为依赖目标

参照模式：emily_core/services/node_service.py（Service + repo 注入 + @audited）
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..repositories.node_repo import ProjectNodeRepo, NodeDeliverableRepo
from .node_commands import (
    CreateNodeCommand, PromoteNodeCommand, ReassignEventCommand, NodeOperationResult,
)
from .node_state_machine import (
    CONDITIONS_NOT_MET,
    NODE_TYPE_MILESTONE,
    NODE_TYPE_TASK,
    NODE_ROLE_BUSINESS,
    NODE_ROLE_TEMP_MILESTONE,
    NODE_ROLE_TEMP_TASK,
    NODE_ROLE_SINK,
    CONTAINER_NODE_ROLES,
)

logger = logging.getLogger("emily.node_container_service")

# 容器节点名称（固定 → 生成的 node_id 确定，配合部分唯一索引保证幂等）
NAME_TEMP_MILESTONE = "临时收容（未迁正节点）"
NAME_SINK = "待归类事件（收容节点）"
NAME_TEMP_TASK_PREFIX = "临时任务-"

# 容器规格（单一定义处：角色 → 名称 / 类型 / 父角色 / 说明）
_CONTAINER_SPECS: dict[str, dict] = {
    NODE_ROLE_TEMP_MILESTONE: {
        "name": NAME_TEMP_MILESTONE,
        "node_type": NODE_TYPE_MILESTONE,
        "parent_role": "",
        "remark": "临时里程碑层（收容未迁正节点；容器按需懒创建）",
    },
    NODE_ROLE_SINK: {
        "name": NAME_SINK,
        "node_type": NODE_TYPE_TASK,
        "parent_role": NODE_ROLE_TEMP_MILESTONE,
        "remark": "未归类收容节点（承载未归类业务事件；容器按需懒创建）",
    },
}


def is_container_role(node_role: str) -> bool:
    """容器识别的**唯一判定入口**（`node_role != BUSINESS`）。"""
    return (node_role or NODE_ROLE_BUSINESS) in CONTAINER_NODE_ROLES


@dataclass
class ContainedNode:
    """收容区条目（用于呈现与认领）。"""

    node_id: str
    node_name: str
    node_role: str
    parent_node_id: str = ""
    parent_node_name: str = ""
    origin: str = ""          # NO_OWNERSHIP 无归属 / PENDING_CLAIM 待认领 / SINK 未归类收容
    creator_id: str = ""
    created_at: str = ""
    deliverable_count: int = 0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "node_role": self.node_role,
            "parent_node_id": self.parent_node_id,
            "parent_node_name": self.parent_node_name,
            "origin": self.origin,
            "origin_label": {
                "NO_OWNERSHIP": "无归属",
                "PENDING_CLAIM": "待认领",
                "SINK": "待归类收容",
            }.get(self.origin, self.origin),
            "creator_id": self.creator_id,
            "created_at": self.created_at,
            "deliverable_count": self.deliverable_count,
            "extra": self.extra,
        }


class NodeContainerService:
    """收容容器服务：懒创建 + 迁正 + 事件归位 + 收容区查询。"""

    def __init__(
        self,
        node_service=None,
        node_repo: Optional[ProjectNodeRepo] = None,
        deliverable_repo: Optional[NodeDeliverableRepo] = None,
    ):
        self._node_service = node_service
        self._node_repo = node_repo or ProjectNodeRepo()
        self._deliv_repo = deliverable_repo or NodeDeliverableRepo()

    @property
    def node_service(self):
        """惰性取 NodeService（未注入时按既有模式自建，供归档链路等无 core 引用的调用方使用）。"""
        if self._node_service is None:
            from ..repositories.permission_repo import PermissionRepository
            from .node_service import NodeService

            self._node_service = NodeService(user_repo=PermissionRepository())
        return self._node_service

    @staticmethod
    def is_container(node_role: str) -> bool:
        return is_container_role(node_role)

    # ══════════════════════════════════════════════════════════════════════
    # 懒创建（幂等：查不到则建，唯一索引冲突则重查）
    #
    # 单份同步实现 `ensure_container_sync`：async 调用方用 asyncio.to_thread 包装，
    # **同步**事件写入链路（ProjectEventAccumulator）直接调用——避免两份实现。
    # ══════════════════════════════════════════════════════════════════════

    def ensure_container_sync(self, project_id: str, node_role: str,
                              operator_id: str = "") -> str:
        """确保项目内指定角色的容器节点存在，返回其 node_id（同步、幂等）。

        - `TEMP_MILESTONE`：根图下的临时里程碑层（项目唯一）
        - `UNCLASSIFIED_SINK`：未归类收容节点（项目唯一，挂临时里程碑层下）
        """
        if not project_id:
            raise ValueError("项目 ID 不能为空（容器节点按项目唯一）")
        spec = _CONTAINER_SPECS.get(node_role)
        if spec is None:
            raise ValueError(f"不支持的容器角色：{node_role}")

        existing = self._find_container(project_id, node_role)
        if existing:
            return existing

        parent_node_id = ""
        if spec["parent_role"]:
            parent_node_id = self.ensure_container_sync(
                project_id, spec["parent_role"], operator_id)

        from .node_batch import generate_node_id

        node_id = generate_node_id(spec["name"], project_id)
        try:
            self._node_repo.create(
                project_id=project_id,
                node_id=node_id,
                node_name=spec["name"],
                deadline="",           # 容器无截止时间（不产生到期提醒）
                creator_id=operator_id,
                responsible_user_id=operator_id,
                remark=spec["remark"],
                status=CONDITIONS_NOT_MET,
                node_type=spec["node_type"],
                node_role=node_role,
                parent_node_id=parent_node_id,
            )
        except Exception as e:
            # 并发下部分唯一索引冲突 → 重查返回既有（幂等）
            again = self._find_container(project_id, node_role)
            if again:
                return again
            raise RuntimeError(f"容器节点创建失败：{node_role} @ {project_id}（{e}）") from e

        self._record_container_event(node_id, node_role, operator_id)
        logger.info("Container node created: %s role=%s project=%s",
                    node_id, node_role, project_id)
        return node_id

    async def get_or_create_temp_milestone(self, project_id: str, operator_id: str = "") -> str:
        """项目唯一「临时里程碑层」（根图下）。空项目不产生（懒创建）。"""
        return await asyncio.to_thread(
            self.ensure_container_sync, project_id, NODE_ROLE_TEMP_MILESTONE, operator_id)

    async def get_or_create_sink(self, project_id: str, operator_id: str = "") -> str:
        """项目唯一「未归类收容节点」（挂临时里程碑层下）。未归类事件的归属落点。"""
        return await asyncio.to_thread(
            self.ensure_container_sync, project_id, NODE_ROLE_SINK, operator_id)

    def _find_container(self, project_id: str, node_role: str) -> str:
        node = self._node_repo.find_container(project_id, node_role)
        return node.node_id if node else ""

    def _record_container_event(self, node_id: str, node_role: str,
                                operator_id: str) -> None:
        try:
            import json as _json

            from .project_event_accumulator import ProjectEventAccumulator

            ProjectEventAccumulator.record_node_event(
                title="收容容器懒创建",
                node_id=node_id,
                event_type="container_created",
                actor_id=operator_id,
                new_value=_json.dumps({"node_role": node_role}, ensure_ascii=False),
                remark=f"按需创建容器节点（{node_role}；容器为承载容器，不计完成度/不提醒/不可作依赖目标）",
            )
        except Exception as e:
            logger.debug("容器创建留痕失败 node=%s: %s", node_id, e)

    # ══════════════════════════════════════════════════════════════════════
    # 临时任务（档B / 档C 落点）
    # ══════════════════════════════════════════════════════════════════════

    async def create_temp_task(self, project_id: str, node_name: str,
                               operator_id: str = "", parent_node_id: str = "",
                               remark: str = "", template_ref_id: str = "",
                               deliverables: Optional[list[dict]] = None,
                               node_type: str = NODE_TYPE_TASK) -> str:
        """落一个临时任务。

        · 档B（有里程碑级归属）：`parent_node_id` = 目标正式里程碑 → origin=待认领
        · 档C（无里程碑级归属）：`parent_node_id` 留空 → 挂临时里程碑层 → origin=无归属
        """
        from .node_batch import generate_node_id

        if not parent_node_id:
            parent_node_id = await self.get_or_create_temp_milestone(project_id, operator_id)
        node_id = generate_node_id(f"{NAME_TEMP_TASK_PREFIX}{node_name}", project_id)
        res = await self.node_service.create_node(CreateNodeCommand(
            project_id=project_id,
            node_id=node_id,
            node_name=node_name,
            creator_id=operator_id,
            responsible_user_id=operator_id,
            remark=remark or "临时任务（认领/挂载后迁正；系统落点）",
            node_type=node_type,
            node_role=NODE_ROLE_TEMP_TASK,
            template_ref_id=template_ref_id,
            deliverables=deliverables or [],
        ))
        if not res.success:
            raise RuntimeError(f"临时任务创建失败：{node_name}（{res.message}）")
        await asyncio.to_thread(
            self._node_repo.update_fields, node_id, parent_node_id=parent_node_id)
        logger.info("Temp task created: %s project=%s parent=%s",
                    node_id, project_id, parent_node_id or "(temp milestone)")
        return node_id

    # ══════════════════════════════════════════════════════════════════════
    # 迁正（认领 / 挂载）
    # ══════════════════════════════════════════════════════════════════════

    async def promote(self, cmd: PromoteNodeCommand) -> NodeOperationResult:
        """认领迁正：临时节点转入正式归属位置，临时标记消失，**编号不变**。"""
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, cmd.node_id)
        if node is None:
            return NodeOperationResult(success=False, node_id=cmd.node_id, message="节点不存在")

        role = node.node_role or NODE_ROLE_BUSINESS
        if not is_container_role(role):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="该节点不是临时节点，无需迁正")
        if role in (NODE_ROLE_TEMP_MILESTONE, NODE_ROLE_SINK):
            return NodeOperationResult(
                success=False, node_id=cmd.node_id,
                message="收容层容器不可迁正——请迁正其下的临时任务")

        allowed, reason = await asyncio.to_thread(
            self._check_claim_permission, node, cmd.operator_id)
        if not allowed:
            return NodeOperationResult(success=False, node_id=cmd.node_id,
                                       message=reason, error_code="40301")

        target = (cmd.target_parent_id or "").strip()
        if target:
            parent = await asyncio.to_thread(self._node_repo.get_by_node_id, target)
            if parent is None:
                return NodeOperationResult(success=False, node_id=cmd.node_id,
                                           message=f"目标父节点 {target} 不存在")
            if (parent.project_id or "") != (node.project_id or ""):
                return NodeOperationResult(success=False, node_id=cmd.node_id,
                                           message="目标父节点不属于同一项目")
            if parent.node_role == NODE_ROLE_SINK:
                return NodeOperationResult(success=False, node_id=cmd.node_id,
                                           message="未归类收容节点不能作为迁正目标")
            if node.node_type == NODE_TYPE_MILESTONE and parent.node_type == NODE_TYPE_TASK:
                return NodeOperationResult(
                    success=False, node_id=cmd.node_id,
                    message="里程碑不得挂在任务之下（层级约束）", error_code="40004")

        # 迁正：只改归属位置与角色标记，**不动 node_id**（挂载其上的成果/事件/参与关系随之生效）
        before_parent = node.parent_node_id or ""
        await asyncio.to_thread(
            self._node_repo.update_fields,
            cmd.node_id,
            parent_node_id=target,
            node_role=NODE_ROLE_BUSINESS,
        )
        await asyncio.to_thread(
            self._record_promotion_event,
            cmd.node_id, before_parent, target, cmd.operator_id, cmd.remark,
        )
        logger.info("Node promoted: %s role=%s %s -> %s", cmd.node_id, role,
                    before_parent or "(root)", target or "(root)")
        return NodeOperationResult(
            success=True, node_id=cmd.node_id, status=node.status or "",
            message=f"节点「{node.node_name}」已迁正（编号不变：{cmd.node_id}）")

    @staticmethod
    def _check_claim_permission(node, operator_id: str) -> tuple[bool, str]:
        """认领授权：L4+ / 创建者本人 / 责任人（fail-closed）。"""
        if not operator_id:
            return False, "缺少操作人信息，已拒绝（fail-closed）"
        if operator_id in ((node.creator_id or ""), (node.responsible_user_id or "")):
            return True, ""
        try:
            from ..repositories.permission_repo import PermissionRepository

            user = PermissionRepository.get_user(operator_id)
            if user is not None and int(getattr(user, "level", 0) or 0) >= 4:
                return True, ""
        except Exception as e:
            logger.warning("认领权限判定失败 operator=%s: %s", operator_id, e)
            return False, "权限服务不可用，已拒绝（fail-closed）"
        return False, "认领临时节点需要 L4 及以上权限，或为该节点创建者 / 责任人"

    def _record_promotion_event(self, node_id: str, before: str, after: str,
                                operator_id: str, remark: str) -> None:
        """迁正留痕（节点事件：操作人 + 时间 + 由何处转入何处）。"""
        try:
            import json as _json

            from .project_event_accumulator import ProjectEventAccumulator

            ProjectEventAccumulator.record_node_event(
                title="临时节点认领迁正",
                node_id=node_id,
                event_type="node_promoted",
                actor_id=operator_id,
                old_value=_json.dumps({"parent_node_id": before}, ensure_ascii=False),
                new_value=_json.dumps({"parent_node_id": after,
                                       "node_role": NODE_ROLE_BUSINESS}, ensure_ascii=False),
                remark=remark or f"认领迁正：由「{before or '临时区'}」转入「{after or '根图'}」（编号不变）",
            )
        except Exception as e:
            logger.warning("迁正留痕失败 node=%s: %s", node_id, e)

    # ══════════════════════════════════════════════════════════════════════
    # 事件归位
    # ══════════════════════════════════════════════════════════════════════

    async def reassign_event(self, cmd: ReassignEventCommand) -> NodeOperationResult:
        """事件归位：把未归类事件改挂到具体节点（不改事件编号，不改收容节点自身）。"""
        from ..repositories.project_event_repo import ProjectEventRepository

        evt = await asyncio.to_thread(ProjectEventRepository.get_by_id, cmd.event_id)
        if evt is None:
            return NodeOperationResult(success=False, node_id=cmd.target_node_id,
                                       message="事件不存在")
        target = await asyncio.to_thread(
            self._node_repo.get_by_node_id, cmd.target_node_id)
        if target is None:
            return NodeOperationResult(success=False, node_id=cmd.target_node_id,
                                       message=f"目标节点 {cmd.target_node_id} 不存在")
        if (target.project_id or "") != (evt.project_id or ""):
            return NodeOperationResult(success=False, node_id=cmd.target_node_id,
                                       message="目标节点与事件不属于同一项目")
        allowed, reason = await asyncio.to_thread(
            self._check_reassign_permission, evt, cmd.operator_id)
        if not allowed:
            return NodeOperationResult(success=False, node_id=cmd.target_node_id,
                                       message=reason, error_code="40301")

        res = await asyncio.to_thread(
            ProjectEventRepository.reassign_node,
            cmd.event_id, cmd.target_node_id, cmd.operator_id, cmd.remark,
        )
        if not res.get("ok"):
            return NodeOperationResult(success=False, node_id=cmd.target_node_id,
                                       message=res.get("reason") or "改挂失败")
        return NodeOperationResult(
            success=True, node_id=cmd.target_node_id,
            message=(f"事件已归位：由「{res.get('before') or '未归类'}」"
                     f"改挂到「{cmd.target_node_id}」（事件编号不变）"))

    @staticmethod
    def _check_reassign_permission(evt, operator_id: str) -> tuple[bool, str]:
        """归位授权：L4+ / 事件记录人（fail-closed）。"""
        if not operator_id:
            return False, "缺少操作人信息，已拒绝（fail-closed）"
        if operator_id == (getattr(evt, "actor_id", "") or ""):
            return True, ""
        try:
            from ..repositories.permission_repo import PermissionRepository

            user = PermissionRepository.get_user(operator_id)
            if user is not None and int(getattr(user, "level", 0) or 0) >= 4:
                return True, ""
        except Exception as e:
            logger.warning("归位权限判定失败 operator=%s: %s", operator_id, e)
            return False, "权限服务不可用，已拒绝（fail-closed）"
        return False, "事件归位需要 L4 及以上权限，或为该事件记录人"

    # ══════════════════════════════════════════════════════════════════════
    # 收容区查询
    # ══════════════════════════════════════════════════════════════════════

    async def list_contained(self, project_id: str, viewer_id: str = "",
                             level: int = 0) -> dict:
        """收容区清单：区分「无归属」与「待认领」，并给出未归类事件数。

        可见范围 = L4+ 或创建者本人（AC-US-15.5）；低于 L4 的非创建者看不到收容内容。
        """
        if not project_id:
            return {"nodes": [], "sink_event_count": 0, "visible": False}
        rows = await asyncio.to_thread(self._node_repo.find_by_project_role, project_id, "")
        nodes: list[ContainedNode] = []
        for n in rows:
            role = n.node_role or NODE_ROLE_BUSINESS
            if not is_container_role(role):
                continue
            if not self._can_view(n, viewer_id, level):
                continue
            parent = await asyncio.to_thread(self._node_repo.get_by_node_id, n.parent_node_id) \
                if n.parent_node_id else None
            parent_is_business = bool(parent and (parent.node_role or NODE_ROLE_BUSINESS) == NODE_ROLE_BUSINESS)
            if role == NODE_ROLE_SINK:
                origin = "SINK"
            elif role == NODE_ROLE_TEMP_MILESTONE:
                origin = "NO_OWNERSHIP"
            else:
                origin = "PENDING_CLAIM" if parent_is_business else "NO_OWNERSHIP"
            deliverable_count = await asyncio.to_thread(
                self._deliv_repo.count_by_node, n.node_id)
            nodes.append(ContainedNode(
                node_id=n.node_id,
                node_name=n.node_name or "",
                node_role=role,
                parent_node_id=n.parent_node_id or "",
                parent_node_name=(parent.node_name if parent else ""),
                origin=origin,
                creator_id=n.creator_id or "",
                created_at=n.created_at or "",
                deliverable_count=deliverable_count,
                extra={"status": n.status or "", "template_ref_id": n.template_ref_id or ""},
            ))

        sink_id = await asyncio.to_thread(self._find_container, project_id, NODE_ROLE_SINK)
        sink_event_count = 0
        # 未归类事件数同样受可见范围约束（L4+ 或收容节点创建者），不向低权限泄露
        sink_visible = bool(sink_id) and (
            int(level or 0) >= 4 or not viewer_id)
        if sink_id and sink_visible:
            try:
                from ..infrastructure.database.models import ProjectEvent
                from ..infrastructure.database.session import get_session

                def _count():
                    with get_session() as session:
                        return session.query(ProjectEvent).filter(
                            ProjectEvent.node_id == sink_id).count()

                sink_event_count = await asyncio.to_thread(_count)
            except Exception as e:
                logger.warning("sink event count failed: %s", e)

        return {
            "nodes": [n.to_dict() for n in nodes],
            "sink": {"node_id": sink_id if sink_visible else "",
                     "event_count": sink_event_count},
            "visible": bool(level or 0) >= 4 or any(
                (n.creator_id or "") == viewer_id for n in nodes),
        }

    @staticmethod
    def _can_view(node, viewer_id: str, level: int) -> bool:
        """L4+ 全项目容器可见；创建者本人始终可见自己的容器（AC-US-15.5）。"""
        if viewer_id and (node.creator_id or "") == viewer_id:
            return True
        return int(level or 0) >= 4
