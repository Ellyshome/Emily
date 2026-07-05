"""Emily Core API Server —— FastAPI 容器入口（蓝图 §2.6）。

emily-core 容器的 HTTP/SSE 入口。启动时 bootstrap EmilyCore（读环境变量），
对外暴露：
  · POST /api/v1/message/send       —— 薄插件转发入站消息
  · POST /api/v1/session/terminate  —— 强制终止 Session
  · GET  /api/v1/events/outbound    —— SSE 出站事件流
  · GET  /api/v1/health             —— 健康检查

业务逻辑全部在 EmilyCore（emily_core 包）内，本层仅做协议适配。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger("emily.api")

# 全局 EmilyCore 实例（lifespan 内初始化）
_core = None


def get_core():
    """依赖注入：获取已初始化的 EmilyCore 实例。"""
    if _core is None:
        raise RuntimeError("EmilyCore not initialized")
    return _core


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时 bootstrap EmilyCore，关闭时清理。"""
    global _core
    from emily_core import bootstrap

    logger.info("Emily Core API starting — bootstrapping EmilyCore...")
    _core = bootstrap.init()
    logger.info("Emily Core API ready")
    yield
    logger.info("Emily Core API shutting down")
    _core = None


app = FastAPI(title="Emily Core API", version="1.0", lifespan=lifespan)

# 注册中间件
from .middleware.auth import AuthMiddleware  # noqa: E402

app.add_middleware(AuthMiddleware)

# 注册路由
from .routes import health, message, session, permission, skills  # noqa: E402
from .sse import outbound  # noqa: E402

app.include_router(health.router, prefix="/api/v1")
app.include_router(message.router, prefix="/api/v1")
app.include_router(session.router, prefix="/api/v1")
app.include_router(permission.router, prefix="/api/v1")
app.include_router(skills.router, prefix="/api/v1")
app.include_router(outbound.router, prefix="/api/v1")

# 全景节点图 V2 路由 + SSE 事件端点（Phase 1-3）
from .routes import node as node_routes  # noqa: E402
from .sse import node_events  # noqa: E402

app.include_router(node_routes.router, prefix="/api/v1")
app.include_router(node_routes.cross_router, prefix="/api/v1")
app.include_router(node_events.router, prefix="/api/v1")
