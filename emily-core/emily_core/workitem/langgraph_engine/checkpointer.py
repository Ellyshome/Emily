# emily-core/emily_core/workitem/langgraph_engine/checkpointer.py
"""图检查点持久化工厂 —— 把 LangGraph checkpointer 从进程内存搬到 PostgreSQL。

问题背景（Pi 对比报告 §6-1）：
    原实现 `compile(checkpointer=MemorySaver())` 把断点存进程内存，进程重启即丢，
    WAITING_FOR_INPUT 的 WorkItem 无法 resume（用户回复撞 resume 异常）。

设计：
    - 默认 `langgraph_checkpointer="postgres"`：懒加载 AsyncPostgresSaver，复用
      emily-postgres 容器（与业务库同生命周期，运维零新增）。
    - 懒加载：Core 初始化是同步的（`EmilyCore.__init__`），而 AsyncPostgresSaver
      需要事件循环与连接；故首次异步调用时才建连 + `setup()` 幂等建表。
    - 连接失败或显式 `"memory"` → 回退 MemorySaver + WARNING，不阻断启动。

注意：图状态可跨重启持久化，但 WorkItem 业务对象当前**不落库**（无表/无 repo），
故"重启后自动续跑"仍依赖上层在会话层重建 WorkItem；本模块只保证图断点与
已执行节点不丢失。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Iterator

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger("emily.langgraph.checkpointer")

# 未显式指定 database_url 时的默认连接参数（与 infrastructure/database/session.py 一致）
_DEFAULT_PG_HOST = "emily-postgres"
_DEFAULT_PG_PORT = 5432
_DEFAULT_PG_DB = "emily"
_DEFAULT_PG_USER = "emily"
_DEFAULT_PG_PASSWORD = "emily_secret_2026"


def _default_conn_string() -> str:
    return (
        f"postgresql://{_DEFAULT_PG_USER}:{_DEFAULT_PG_PASSWORD}"
        f"@{_DEFAULT_PG_HOST}:{_DEFAULT_PG_PORT}/{_DEFAULT_PG_DB}"
    )


def _with_connect_timeout(conn_string: str, timeout: int = 10) -> str:
    """补齐 connect_timeout（psycopg 无超时时连接可能长时间挂起）。"""
    if "connect_timeout" in conn_string:
        return conn_string
    sep = "&" if "?" in conn_string else "?"
    return f"{conn_string}{sep}connect_timeout={timeout}"


def resolve_conn_string(config) -> str:
    """从 Config 解析 checkpointer 连接串；返回空串表示不可用。"""
    url = getattr(config, "database_url", "") or ""
    if not url:
        url = _default_conn_string()
    return _with_connect_timeout(url)


class LazyPostgresCheckpointer(BaseCheckpointSaver):
    """懒加载 Postgres checkpointer 代理。

    首次异步调用时才建立真实连接与 saver，并在无法连接时回退 MemorySaver。
    之所以用代理而非在 Core 初始化时直接建连：Core 初始化是同步的，此处没有
    运行中的事件循环，而 psycopg 异步连接必须绑定事件循环。
    """

    def __init__(self, conn_string: str, *, use_pool: bool = True) -> None:
        super().__init__()
        self._conn_string = conn_string
        self._use_pool = use_pool
        self._inner: BaseCheckpointSaver | None = None
        self._pool = None
        self._conn = None
        self._ready = False
        self._degraded = False          # True 表示已回退 MemorySaver
        self._init_lock: asyncio.Lock | None = None

    # ── 懒初始化 ──

    async def _ensure(self) -> BaseCheckpointSaver:
        if self._ready:
            return self._inner  # type: ignore[return-value]
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._ready:
                return self._inner  # type: ignore[return-value]
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
                from psycopg.rows import dict_row
                import psycopg

                if self._use_pool:
                    from psycopg_pool import AsyncConnectionPool
                    pool = AsyncConnectionPool(
                        self._conn_string, min_size=1, max_size=5, open=False,
                        kwargs={"autocommit": True, "prepare_threshold": 0,
                                "row_factory": dict_row},
                    )
                    await pool.open()
                    await pool.wait(timeout=15)
                    self._pool = pool
                    saver = AsyncPostgresSaver(conn=pool)
                else:
                    # 单连接模式（本地/测试环境：Windows Proactor 下 pool worker 不可用）
                    conn = await psycopg.AsyncConnection.connect(
                        self._conn_string, autocommit=True,
                        prepare_threshold=0, row_factory=dict_row,
                    )
                    self._conn = conn
                    saver = AsyncPostgresSaver(conn=conn)

                await saver.setup()   # 幂等建表（checkpoints / checkpoint_writes / ...）
                self._inner = saver
                self._ready = True
                logger.info("Checkpointer: AsyncPostgresSaver ready (pool=%s)", self._use_pool)
                return saver
            except Exception as e:
                logger.warning(
                    "Checkpointer: Postgres unavailable (%s) — falling back to MemorySaver "
                    "(断点将不跨进程持久化)", e,
                )
                from langgraph.checkpoint.memory import MemorySaver
                self._inner = MemorySaver()
                self._ready = True
                self._degraded = True
                return self._inner

    @property
    def degraded(self) -> bool:
        """是否已回退内存实现（供健康检查/日志）。"""
        return self._degraded

    async def aclose(self) -> None:
        """关闭连接/连接池（进程退出或测试清理）。"""
        try:
            if self._pool is not None:
                await self._pool.close()
            if self._conn is not None:
                await self._conn.close()
        except Exception as e:
            logger.debug("Checkpointer aclose failed: %s", e)

    # ── 异步接口（生产路径：graph.ainvoke / aget_state / adelete_thread） ──

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        saver = await self._ensure()
        return await saver.aget_tuple(config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        saver = await self._ensure()
        return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path: str = ""):
        saver = await self._ensure()
        return await saver.aput_writes(config, writes, task_id, task_path)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        saver = await self._ensure()
        async for item in saver.alist(config, filter=filter, before=before, limit=limit):
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        saver = await self._ensure()
        if hasattr(saver, "adelete_thread"):
            await saver.adelete_thread(thread_id)

    # ── 同步接口 ──
    # 仅作兼容占位：图中所有状态读取已改为 await graph.aget_state(...)，
    # 不提供同步实现以避免在事件循环线程内阻塞或跨 loop 复用连接。

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError(
            "LazyPostgresCheckpointer 仅支持异步接口，请使用 await graph.aget_state(...)"
        )

    def put(self, config, checkpoint, metadata, new_versions):
        raise NotImplementedError("use aput")

    def put_writes(self, config, writes, task_id, task_path: str = ""):
        raise NotImplementedError("use aput_writes")

    def list(self, config: RunnableConfig | None, **kwargs) -> Iterator[CheckpointTuple]:
        raise NotImplementedError("use alist")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("use adelete_thread")


def build_checkpointer(config, *, use_pool: bool | None = None) -> BaseCheckpointSaver:
    """构建 checkpointer：postgres（默认，懒加载+失败回退）或 memory（显式）。

    Args:
        config: Config 实例。
        use_pool: 覆盖连接方式。None=默认 True（AsyncConnectionPool，生产推荐）；
                  False=单 AsyncConnection（Windows 本地验证：Proactor/Selector 下
                  psycopg_pool worker 不可用）。
    """
    mode = (getattr(config, "langgraph_checkpointer", "postgres") or "postgres").strip().lower()

    if mode in ("memory", "inmemory", "in-memory"):
        logger.info("Checkpointer: MemorySaver (explicitly configured)")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    conn_string = resolve_conn_string(config)
    if not conn_string:
        logger.warning("Checkpointer: no connection string — falling back to MemorySaver")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()

    logger.info("Checkpointer: LazyPostgresCheckpointer (mode=%s)", mode)
    return LazyPostgresCheckpointer(
        conn_string, use_pool=True if use_pool is None else use_pool,
    )


async def startup_recovery(core) -> dict:
    """启动恢复：急切初始化 checkpointer 并清扫超期残留检查点。

    做两件事：
      1. 急切 `_ensure()`：启动即验证 Postgres 可达并幂等建表，避免首次 WorkItem
         执行时才暴露连接问题（失败则回退 MemorySaver + WARNING）。
      2. 清扫：枚举全部 thread，删除最后检查点时间早于
         `checkpoint_resume_window_seconds` 的残留（覆盖进程崩溃、未走终态清理的
         WorkItem），防 checkpoints 表无界增长。

    局限（已知）：WorkItem 业务对象当前不落库（无表/repo），进程重启后 Session 池
    重建，无人持有挂起的 WorkItem，故无法"按 WI 标记失败"；本函数只能保证图断点
    不残留膨胀。真正的"重启续跑"需上层先补齐 WorkItem 持久化。

    Returns:
        {"ready": bool, "degraded": bool, "swept": int, "scanned": int}
    """
    from datetime import datetime, timedelta, timezone

    stats = {"ready": False, "degraded": False, "swept": 0, "scanned": 0}
    graph = getattr(core, "_workitem_graph", None) if core else None
    checkpointer = getattr(graph, "checkpointer", None) if graph else None
    if checkpointer is None:
        return stats

    # 1) 急切初始化
    if isinstance(checkpointer, LazyPostgresCheckpointer):
        try:
            await checkpointer._ensure()
        except Exception as e:
            logger.warning("Checkpointer startup init failed: %s", e)
        stats["degraded"] = checkpointer.degraded
    stats["ready"] = True

    # 2) 清扫超期残留
    window = getattr(getattr(core, "config", None), "checkpoint_resume_window_seconds", 1800)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window)
    try:
        latest: dict[str, str] = {}
        async for tup in checkpointer.alist(None):
            cfg = tup.config or {}
            tid = (cfg.get("configurable") or {}).get("thread_id", "")
            ts = (tup.checkpoint or {}).get("ts", "") if isinstance(tup.checkpoint, dict) else ""
            if tid and ts and (tid not in latest or ts > latest[tid]):
                latest[tid] = ts
        stats["scanned"] = len(latest)
        for tid, ts in latest.items():
            try:
                parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue
            if parsed < cutoff:
                try:
                    await checkpointer.adelete_thread(tid)
                    stats["swept"] += 1
                except Exception as e:
                    logger.debug("sweep delete failed for thread=%s: %s", tid, e)
        logger.info("Checkpointer startup recovery: scanned=%d swept=%d degraded=%s",
                    stats["scanned"], stats["swept"], stats["degraded"])
    except Exception as e:
        logger.warning("Checkpointer startup sweep skipped: %s", e)
    return stats
