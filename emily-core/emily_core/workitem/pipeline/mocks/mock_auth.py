"""MockAuthEngine — 始终返回 ALLOW。

Phase 0 占位。总线跑通后由真实 AuthEngine 替换。
"""

from __future__ import annotations

from typing import Any

from ..interfaces.auth import AuthEngine, AuthResult, AuthDecision


class MockAuthEngine(AuthEngine):
    """Mock 鉴权引擎 — 始终返回 ALLOW。"""

    async def authorize(self, user_id: str, route_decision: Any) -> AuthResult:
        """始终放行所有请求。

        Args:
            user_id: 用户 ID
            route_decision: 路由决策

        Returns:
            AuthResult: 始终 ALLOW
        """
        return AuthResult(
            decision=AuthDecision.ALLOW,
            reason="",
            matched_roles=["all"],
            _source="mock",
        )
