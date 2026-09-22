"""archive_handler_registry.py —— 归档后处理器注册表（C11 注册通道）。

**为什么需要它**：归档收口（`FileManager.archive_upload` / `FileApplication.handle_file`）
不得内联业务逻辑（C11 禁止裸调用/硬编码接线）。归档后的分析能力（模板锚定门禁、归属判定、
整树装配）经本注册表接入，归档收口只做**一处**薄调用 `dispatch(...)`。

口径（PRD §4.4-8 / 12）：
  · 处理器在**进程内异步任务**中执行，不新增常驻进程或通道
  · 处理器**逐个隔离**：任一处理器异常只记日志，不影响其它处理器、不影响归档主链路（fail-open）
  · 新增处理器 = 在 `services/archive_handlers.register_archive_handlers()` 加一行；
    归档收口无需改动
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger("emily.archive_handler_registry")

ArchiveHandler = Callable[..., Awaitable[object]]


class ArchiveHandlerRegistry:
    """归档后处理器注册表（进程内单例注册）。"""

    _handlers: list[tuple[str, ArchiveHandler]] = []

    @classmethod
    def register(cls, name: str, handler: ArchiveHandler) -> None:
        """注册（异步）处理器；同名覆盖，便于测试替换。"""
        if handler is None:
            raise ValueError("handler 不能为空")
        for i, (n, _) in enumerate(cls._handlers):
            if n == name:
                cls._handlers[i] = (name, handler)
                logger.debug("ArchiveHandler replaced: %s", name)
                return
        cls._handlers.append((name, handler))
        logger.info("ArchiveHandler registered: %s", name)

    @classmethod
    def names(cls) -> list[str]:
        return [n for n, _ in cls._handlers]

    @classmethod
    def clear(cls) -> None:
        """清空注册（测试用）。"""
        cls._handlers = []

    @classmethod
    async def dispatch(cls, *, file_id: str, project_id: str = "",
                       actor_id: str = "", filename: str = "") -> dict:
        """把一份已归档文件交给全部已注册处理器（逐个隔离，fail-open）。

        Returns:
            {"dispatched": [处理器名...], "failed": [{"handler":..., "reason":...}]}
        """
        dispatched: list[str] = []
        failed: list[dict] = []
        for name, handler in list(cls._handlers):
            try:
                await handler(file_id=file_id, project_id=project_id,
                              actor_id=actor_id, filename=filename)
                dispatched.append(name)
            except Exception as e:
                # 单个处理器失败不阻断其它处理器，也不冒泡到归档主链路（PRD §4.4-12）
                failed.append({"handler": name, "reason": str(e)})
                logger.warning("ArchiveHandler failed: %s file=%s err=%s", name, file_id, e)
        if failed:
            logger.info("Archive dispatch done with %d failure(s): %s",
                        len(failed), [f["handler"] for f in failed])
        return {"dispatched": dispatched, "failed": failed}
