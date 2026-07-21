"""MonitorService —— 监控数据异步编排层。

参照模式：services/node_service.py（async def + asyncio.to_thread 包裹 sync repo）。
编排三类数据源：
  1. Session 池状态（内存，直接读取）
  2. DB 数据（sync repo，asyncio.to_thread 包裹）
  3. Docker 容器状态（aiohttp 异步调用）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..repositories.monitor_repo import MonitorRepository
from ..infrastructure.docker.client import get_container_status, get_im_accounts

logger = logging.getLogger("emily.service.monitor")


class MonitorService:
    """监控数据编排服务。

    通过 get_core() 延迟获取 SessionPoolManager，避免循环导入。
    """

    def __init__(self, core=None):
        self._core = core

    def _get_pool(self):
        """获取 SessionPoolManager。"""
        if self._core is not None:
            return self._core._session_pool
        # 延迟获取
        try:
            from api.server import get_core
            core = get_core()
            return core._session_pool
        except Exception as e:
            logger.warning("get SessionPool failed: %s", e, exc_info=True)
            return None

    # ── 容器状态 ──

    async def get_containers(self) -> list[dict]:
        """获取受监控容器运行状态。"""
        return await get_container_status()

    # ── IM 账号状态 ──

    async def get_im_accounts(self) -> list[dict]:
        """获取 IM 账号状态列表。"""
        return await get_im_accounts()

    # ── Session 池 ──

    async def get_session_pool(self) -> dict:
        """获取 Session 池状态。"""
        pool = self._get_pool()
        if pool is None:
            return {"total": 0, "uptime_seconds": 0, "sessions": []}
        # get_status() 是 sync，但在同进程内直接调用（非阻塞，纯内存读取）
        return pool.get_status()

    # ── 会话消息 ──

    async def get_session_messages(self, conversation_id: str, limit: int = 5) -> list[dict]:
        """获取指定会话最近 N 条消息。"""
        return await asyncio.to_thread(
            MonitorRepository.list_recent_messages,
            conversation_id=conversation_id,
            limit=limit,
        )

    # ── 全景节点 ──

    async def list_nodes(
        self,
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询全景节点列表（仅业务字段）。"""
        return await asyncio.to_thread(
            MonitorRepository.list_nodes,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )

    async def get_node_detail(self, node_id: str) -> Optional[dict]:
        """查询单节点完整业务字段。"""
        return await asyncio.to_thread(
            MonitorRepository.get_node_detail,
            node_id=node_id,
        )

    # ── 管控文件 ──

    async def list_files(
        self,
        project_id: Optional[str] = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询管控文件列表。"""
        return await asyncio.to_thread(
            MonitorRepository.list_files,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )

    # ── 人员列表 ──

    async def list_users(
        self,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """查询人员列表。"""
        return await asyncio.to_thread(
            MonitorRepository.list_users,
            limit=limit,
            offset=offset,
        )
