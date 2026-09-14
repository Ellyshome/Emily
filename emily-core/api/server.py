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
from pathlib import Path

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

    # Embedding 后端启动探测（auto 模式：本地优先、远程 API 兜底）。
    # 提前解析可让启动日志明确当前生效后端，并避免首次检索才付探测成本。
    _embedding_client = getattr(_core, "_tei_client", None)
    if _embedding_client is not None and hasattr(_embedding_client, "resolve"):
        try:
            logger.info("Embedding backend resolved: %s", await _embedding_client.resolve())
        except Exception as e:
            logger.warning("Embedding backend resolve failed: %s", e)
    # 图检查点启动恢复：验证 Postgres 可达 + 幂等建表 + 清扫超期残留
    try:
        # 注：图是懒构建的（首次 handle_message 才建），须先触发初始化，
        #     否则 _workitem_graph 尚未存在，startup_recovery 会静默空转。
        _core._ensure_initialized()
        from emily_core.workitem.langgraph_engine.checkpointer import startup_recovery
        await startup_recovery(_core)
    except Exception as e:
        logger.warning("Checkpointer startup recovery failed: %s", e)
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

# 进化管理 API
from .routes import evolution  # noqa: E402

app.include_router(evolution.router)

# 元认知管理 API
from .routes import meta_cognition  # noqa: E402

app.include_router(meta_cognition.router)

# Agent 追踪查询 API（D1：trace 闭环）
from .routes import trace as trace_routes  # noqa: E402

app.include_router(trace_routes.router, prefix="/api/v1")

# emy-console — 脚本执行控制台（独立前端页面）
from .routes import scripts_runner  # noqa: E402

app.include_router(scripts_runner.router, prefix="/api/v1")

# emy-console 资源清单 API（四组资源 + 用户权限过滤）
from .routes import console_resources  # noqa: E402

app.include_router(console_resources.router, prefix="/api/v1")

# 脚本控制台静态文件（挂 /console/ 前缀，已加入 AuthMiddleware 白名单）
_console_static = Path(__file__).resolve().parent.parent / "static" / "scripts_tool"
if _console_static.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/console", StaticFiles(directory=str(_console_static), html=True), name="console")

# 群列表同步 API
from .routes import groups  # noqa: E402

app.include_router(groups.router, prefix="/api/v1")
