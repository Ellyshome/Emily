"""SignoffService —— 签认机制（替代"审批"，PRD US-04 / US-06）。

设计要点：
  - 签认表达"被谁认可"，**不阻断入库**（入库由 NodeService 直接完成）
  - 按业务对象类型设定**等级门槛**（SIGNOFF_REQUIREMENTS，唯一设定）
  - 签认**留痕**：签认人 / 签认时间 / 签认时的等级
  - **幂等**：重复签认不产生冲突（接受并刷新留痕）
  - 等级不足：返回**明确拒绝**（而非运行时异常）

分层：Service 层；内部对 Repository 的 sync 调用统一用 asyncio.to_thread 包裹（C6）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from ..infrastructure.logging.audit import audited

logger = logging.getLogger("emily.signoff")


# ══════════════════════════════════════════════════════════════════════════════
# 签认等级要求（每类需要签认的业务对象，有且仅有一个要求值 —— AC-US-06.1）
# ══════════════════════════════════════════════════════════════════════════════
SIGNOFF_REQUIREMENTS: dict[str, int] = {
    # 业务对象类型 → 需几级签认
    "node": 4,   # 全景节点：L4（建设主管）及以上可签认
}


def requirement_of(object_type: str) -> int | None:
    """返回对象类型的签认等级要求；未登记返回 None。"""
    return SIGNOFF_REQUIREMENTS.get(object_type)


class SignoffService:
    """签认服务 —— 等级门槛校验 + 留痕 + 幂等。"""

    def __init__(self, node_repo=None, user_repo=None, event_repo=None):
        if node_repo is None:
            from ..repositories.node_repo import ProjectNodeRepo
            node_repo = ProjectNodeRepo()
        if event_repo is None:
            from ..repositories.node_repo import NodeEventRepo
            event_repo = NodeEventRepo()
        if user_repo is None:
            from ..repositories.permission_repo import PermissionRepository
            user_repo = PermissionRepository()
        self._node_repo = node_repo
        self._event_repo = event_repo
        self._user_repo = user_repo

    # ========================================================================
    #  核心入口
    # ========================================================================

    @audited(
        category="node",
        action="acknowledged",
        target_type="node",
        actor_arg="user_id",
        target_arg="object_id",
    )
    async def acknowledge(self, object_type: str, object_id: str,
                          user_id: str, remark: str = "") -> dict:
        """签认一条业务对象。

        Args:
            object_type: 业务对象类型（当前支持 "node"）
            object_id: 业务对象编号（节点业务主键 node_id）
            user_id: 签认人 UUID
            remark: 备注

        Returns:
            {"success": bool, "message": str, "acknowledged": bool,
             "acknowledged_by": str, "acknowledged_at": str, "acknowledged_level": int}
        """
        required = requirement_of(object_type)
        if required is None:
            return self._fail(f"未知的签认对象类型：{object_type}")

        if not user_id:
            return self._fail("缺少签认人身份，无法签认")

        # ── 签认人等级校验 ──
        user = await asyncio.to_thread(self._user_repo.get_user, user_id)
        if user is None:
            return self._fail(f"签认人 {user_id} 不存在，无法签认")
        user_level = getattr(user, "level", 0) or 0
        if user_level < required:
            return self._fail(
                f"等级不足（当前 L{user_level}，需 L{required}），无法签认"
            )

        if object_type == "node":
            return await self._acknowledge_node(object_id, user_id, user_level, remark)

        return self._fail(f"未实现的签认对象类型：{object_type}")

    # ========================================================================
    #  节点签认
    # ========================================================================

    async def _acknowledge_node(self, node_id: str, user_id: str,
                                user_level: int, remark: str) -> dict:
        node = await asyncio.to_thread(self._node_repo.get_by_node_id, node_id)
        if node is None:
            return self._fail(f"节点 {node_id} 不存在")

        already = bool(getattr(node, "acknowledged_by", "") or "")
        now_iso = datetime.now(timezone.utc).isoformat()

        # 幂等：重复签认接受并刷新留痕（AC-US-06.4）
        await asyncio.to_thread(
            self._node_repo.update_fields,
            node_id,
            acknowledged_by=user_id,
            acknowledged_at=now_iso,
            acknowledged_level=user_level,
        )

        try:
            await asyncio.to_thread(
                self._record_event, node, user_id, user_level, already, remark,
            )
        except Exception:
            logger.exception("signoff event record failed node=%s", node_id)

        action = "已更新签认" if already else "签认成功"
        logger.info("Node signoff: %s by %s (L%s)", node_id, user_id, user_level)
        return {
            "success": True,
            "message": f"节点「{getattr(node, 'node_name', node_id)}」{action}（L{user_level}）",
            "acknowledged": True,
            "acknowledged_by": user_id,
            "acknowledged_at": now_iso,
            "acknowledged_level": user_level,
        }

    def _record_event(self, node, user_id: str, user_level: int,
                      already: bool, remark: str) -> None:
        import json as _json
        from ..infrastructure.database.models import _new_id
        self._event_repo.create(
            event_id=_new_id("EVT"),
            node_id=getattr(node, "node_id", ""),
            event_type="node_acknowledged",
            old_value=_json.dumps({"acknowledged": already}),
            new_value=_json.dumps({
                "acknowledged_by": user_id,
                "acknowledged_level": user_level,
            }),
            operator_id=user_id,
            remark=remark or "节点签认",
        )

    # ========================================================================
    #  辅助
    # ========================================================================

    @staticmethod
    def _fail(message: str) -> dict:
        return {
            "success": False,
            "message": message,
            "acknowledged": False,
            "acknowledged_by": "",
            "acknowledged_at": "",
            "acknowledged_level": 0,
        }
