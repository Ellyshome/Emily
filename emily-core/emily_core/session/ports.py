# emily-core/emily_core/session/ports.py
"""M8 外壳端口 —— 会话编排内核不持有外部实例（计划 M8 / PRD US-08）。

定位：
  - 内核只做"决策与编排"，运行时状态、事件、文件、检索四类外部能力经端口访问；
  - 端口由宿主装配（本模块的 `build_ports`），内核侧只接收端口对象，不 import 具体实现。
  - 端口未装配时，内核按"能力缺失"降级（如无事件端口则不上报进度），不报错。

边界：本模块不实现四类能力本体，只做端口形状与装配。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.session.ports")


@dataclass
class GraphPorts:
    """内核可用的外壳端口（任一可为 None，表示该能力未装配）。

    注：检索与文件访问**不设端口**——它们已由能力通道（业务工具）承载，
    内核经能力调用访问，故此处只保留事件与运行时两类。
    """

    events: Any = None        # 出站与进度：`emit(kind, payload)`
    runtime: Any = None       # 会话运行时状态：过期与清扫
    meta: dict = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "events": self.events is not None,
            "runtime": self.runtime is not None,
            **dict(self.meta or {}),
        }


def build_ports(*, core: Any = None, events: Any = None, conversation_id: str = "") -> GraphPorts:
    """装配端口：优先复用宿主既有基础设施，不新建外部依赖。"""

    runtime = None
    if core is not None:
        try:
            from .loop import SessionLoopPool
            runtime = getattr(core, "_session_loop_pool", None) or getattr(core, "_session_pool", None)
            if runtime is not None and not hasattr(runtime, "sweep_expired"):
                runtime = None
        except Exception as e:  # noqa: BLE001
            logger.debug("runtime port unavailable: %s", e)

    ports = GraphPorts(
        events=events, runtime=runtime,
        meta={"conversation_id": str(conversation_id or "")},
    )
    logger.debug("ports assembled: %s", ports.describe())
    return ports
