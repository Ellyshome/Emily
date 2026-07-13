"""鉴权中间件（占位，蓝图 §2.2 middleware/auth.py）。

emily-core 仅监听内网（astrbot_network），当前默认放行。
真实的请求级鉴权（如插件 ↔ Core 的共享密钥校验）属后续增强。
监控路由（/api/v1/monitor/）始终放行，不受 EMILY_API_TOKEN 约束。
"""

from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("emily.api.auth")


class AuthMiddleware(BaseHTTPMiddleware):
    """请求鉴权中间件（占位）。

    若设置环境变量 EMILY_API_TOKEN，则校验请求头 X-Emily-Token；否则放行。
    健康检查端点 + 监控路由始终放行。
    """

    # 始终放行的路径前缀
    _PUBLIC_PREFIXES = ("/health", "/api/v1/monitor/", "/assets/", "/")

    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("EMILY_API_TOKEN", "")

        # 始终放行的路由
        path = request.url.path
        for prefix in self._PUBLIC_PREFIXES:
            if path == prefix or path.startswith(prefix):
                return await call_next(request)

        # 需要 Token 校验的路由
        if expected:
            token = request.headers.get("X-Emily-Token", "")
            if token != expected:
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "unauthorized"}, status_code=401)

        return await call_next(request)
