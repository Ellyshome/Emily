"""留痕上下文中间件 —— console 入口统一注入。

职责边界（见 `issues/操作留痕治理/操作留痕治理_计划_V1.md` §2.4）：
- **只注入**：绑定 `source=ops` / `channel_account=console`，一处覆盖全部 console 端点；
- **不记录**：留痕由动作类 service 层的挂载点产生，入口层不写留痕；
- **不拦截、不分流**：不改变任何请求路径（约束「入口不合并、不隔离」）。

console 模拟对话（`/console/chat/send`）在路由内显式覆盖为 `source=test`，
故此处绑定的 `ops` 会被其覆盖 —— 这是设计意图：模拟对话走的是真实 IM 入口，
但它是测试流量，必须与真实用户行为可区分。
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("emily.api.audit")

# 需要注入留痕上下文的路径前缀
_CONSOLE_PREFIXES = ("/api/v1/console",)


class AuditContextMiddleware(BaseHTTPMiddleware):
    """为 console 入口注入留痕上下文（source=ops / channel_account=console）。"""

    async def dispatch(self, request, call_next):
        path = request.url.path
        token = None
        if any(path == prefix or path.startswith(prefix) for prefix in _CONSOLE_PREFIXES):
            from emily_core.infrastructure.logging.audit import (
                SOURCE_OPS,
                bind_audit_context,
                reset_audit_context,
            )

            token = bind_audit_context(source=SOURCE_OPS, channel_account="console")
            try:
                return await call_next(request)
            finally:
                reset_audit_context(token)
        return await call_next(request)
