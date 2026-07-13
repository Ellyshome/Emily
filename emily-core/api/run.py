"""双服务器启动入口 ——

  - 18080: 业务 API（api.server:app），绑定 0.0.0.0
  - 18081: 监控 API + 静态页面（api.monitor_app:app），绑定 0.0.0.0

两个 uvicorn 实例共享同一个 Python 进程和 EmilyCore 实例。
docker-compose 中通过端口映射控制访问范围：
  - 127.0.0.1:18080 → 仅宿主机
  - 0.0.0.0:18081  → 局域网
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

logger = logging.getLogger("emily.run")


async def main():
    """同时启动业务 API 和监控 API。"""
    config_main = uvicorn.Config(
        "api.server:app",
        host="0.0.0.0",
        port=18080,
        log_level="info",
    )
    config_monitor = uvicorn.Config(
        "api.monitor_app:app",
        host="0.0.0.0",
        port=18081,
        log_level="info",
    )

    server_main = uvicorn.Server(config_main)
    server_monitor = uvicorn.Server(config_monitor)

    logger.info("Starting dual servers: :18080 (business) + :18081 (monitor)")

    await asyncio.gather(
        server_main.serve(),
        server_monitor.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
