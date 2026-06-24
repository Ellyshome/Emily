"""鉴权中间件（占位，蓝图 §2.2 middleware/auth.py）。

emily-core 仅监听内网（astrbot_network），当前默认放行。
真实的请求级鉴权（如插件 ↔ Core 的共享密钥校验）属后续增强。
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
    健康检查端点始终放行。
    """

    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("EMILY_API_TOKEN", "")
        if expected and not request.url.path.endswith("/health"):
            token = request.headers.get("X-Emily-Token", "")
            if token != expected:
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
