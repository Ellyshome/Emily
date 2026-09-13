"""服务启动入口 —— 单服务器。

  - 18080: 业务 API（api.server:app），绑定 0.0.0.0

> 运维看板已于 2026-09-13 退役，原 18081 监控端口（双服务器）一并移除，
> 统一由 18080 提供业务 API 与 emy-console（/console/）。
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

logger = logging.getLogger("emily.run")


async def main():
    """启动业务 API 服务。"""
    config = uvicorn.Config(
        "api.server:app",
        host="0.0.0.0",
        port=18080,
        log_level="info",
    )
    server = uvicorn.Server(config)

    logger.info("Starting server: :18080 (business)")

    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
