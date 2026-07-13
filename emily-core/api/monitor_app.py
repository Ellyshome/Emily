"""监控专用 FastAPI 应用 —— 运行在 18081 端口。

仅挂载 /api/v1/monitor/* 路由 + 静态文件（前端看板）。
不挂载任何业务路由（message/session/permission 等）。

与 api/server.py 共享同一个 EmilyCore 实例（通过 get_core()）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

logger = logging.getLogger("emily.api.monitor")


@asynccontextmanager
async def monitor_lifespan(app: FastAPI):
    """监控应用生命周期——确保 EmilyCore 已初始化。"""
    # 复用 server.py 的 lifespan 已初始化的 EmilyCore
    # 此处仅确保 get_core() 可用
    try:
        from api.server import get_core
        core = get_core()
        logger.info("Monitor app: EmilyCore reference acquired")
    except RuntimeError:
        # server.py 可能还未完成 lifespan，等待首次请求时 lazy init
        logger.info("Monitor app: EmilyCore not yet ready, will lazy-init on request")
    yield
    logger.info("Monitor app shutting down")


app = FastAPI(
    title="Emily Monitor API",
    version="1.0",
    lifespan=monitor_lifespan,
)

# 注册监控路由
from .routes import monitor  # noqa: E402

app.include_router(monitor.router, prefix="/api/v1")

# 静态文件（前端看板）
# 优先使用容器内 /app/static，开发环境回退到 emily-core/static/
_static_dir = Path("/app/static")
if not _static_dir.exists():
    _static_dir = Path(__file__).resolve().parent.parent / "static"

if _static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(_static_dir)), name="assets")

    @app.get("/")
    async def serve_index():
        """提供前端看板主页。"""
        index_file = _static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Emily Monitor — static files not found", "static_dir": str(_static_dir)}
else:
    @app.get("/")
    async def no_static():
        return {"message": "Emily Monitor — static directory not found"}
