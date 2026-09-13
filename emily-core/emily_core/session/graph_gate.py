# emily-core/emily_core/session/graph_gate.py
"""M6 门禁判定收敛 —— 三处判定收敛为图内单一判定点（计划 M6 / PRD US-06、US-10）。

定位：
  - 现状三处并行：图内 Hook 桥接、能力装配时的工具裁剪（`build_tool_specs` fail-closed）、
    执行期兜底。三处各自为政，行为难以预测。
  - 本次：编排侧只保留**一个**判定点（图内 gate 节点），判定结论由既有通道给出：
      ① 能力可见性（fail-closed）：能力必须在该操作者当前可见能力集内；
      ② 既有授权通道（可选注入）：把判定委托给既有权限引擎，**不在此重造判定规则**。
  - 判定点的存在不减少治理强度：可见性判定保持 fail-closed，未知能力一律拒绝。

边界：本模块不做权限语义（分级、可见范围、密级）设计，只做"收敛与调用"。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("emily.session.graph_gate")

DENY_NOT_VISIBLE = "该操作不在你当前可用的能力范围内，我无法执行。"
DENY_BY_AUTHORIZER = "该操作无法执行，您可能没有相应权限。"


def build_gate_evaluator(
    *,
    visible_provider: Callable[[], Any],
    authorizer: "Callable[[str, dict], Any] | None" = None,
):
    """构建图内门禁判定：可见性 fail-closed + 既有授权通道（可选）。

    Args:
        visible_provider: 返回当前操作者可见能力名的可调用对象（复用既有裁剪通道）。
        authorizer: `async (capability, params) -> (allowed: bool, reason: str)`；
            由既有权限引擎适配而来，未注入时仅做可见性判定。
    """

    def _visible() -> set:
        try:
            return {str(x) for x in (visible_provider() or ()) if str(x)}
        except Exception as e:  # noqa: BLE001 — 取不到可见集时保守拒绝
            logger.error("gate visible_provider failed: %s", e, exc_info=True)
            return set()

    async def _evaluate(capability: str, params: dict) -> dict:
        name = str(capability or "")
        if not name or name not in _visible():
            return {"decision": "deny", "reason": DENY_NOT_VISIBLE, "source": "visibility"}
        if authorizer is not None:
            try:
                allowed, reason = await authorizer(name, dict(params or {}))
            except Exception as e:  # noqa: BLE001 — 授权通道异常按拒绝处理（fail-closed）
                logger.error("gate authorizer failed: %s", e, exc_info=True)
                return {"decision": "deny", "reason": f"{DENY_BY_AUTHORIZER}（判定异常）",
                        "source": "authorizer_error"}
            if not allowed:
                return {"decision": "deny",
                        "reason": str(reason or DENY_BY_AUTHORIZER), "source": "authorizer"}
        return {"decision": "allow", "source": "visibility"}

    return _evaluate


def describe_convergence() -> dict:
    """收敛说明（供文档与运维核对）。"""
    return {
        "single_decision_point": "graph gate node",
        "checks": ["capability_visibility(fail-closed)", "existing_authorizer(optional)"],
        "not_reimplemented": ["等级与可见范围语义", "密级与行级过滤"],
    }
