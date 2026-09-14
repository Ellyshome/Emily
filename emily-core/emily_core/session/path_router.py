"""会话路径分派（M8）—— SOP 准入清单。

定位（计划 M8 / PRD US-09、D6）：
  - 按 SOP 准入清单（`session_loop_sop_allowlist`）控制哪些 SOP 能力对会话主循环可见
    （灰度迁移顺序：查询能力 → 写类 SOP 逐个放开）；
  - 由于 R2/D6 禁止回合开始的显式分类，消息入口**无法**按 SOP 分流，故清单实现为
    "能力目录准入清单"（放开 = 该 SOP 能力对主循环可见）。见计划 M8「灰度语义」。

原全局开关（`session_loop_enabled` / `use_loop()`）已于退役中移除：会话池为唯一入站
渠道，"关闭即回退旧链路"的语义不复存在（见 需求/LangGraph编排内核化/..._退役记录_V1.md）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("emily.session.path_router")


class SessionPathRouter:
    """会话路径分派器。"""

    def __init__(self, config=None) -> None:
        self._config = config

    def allowed_sops(self) -> set | None:
        """SOP 能力准入清单；None = 全部放开。"""
        raw = getattr(self._config, "session_loop_sop_allowlist", "") or ""
        items = {s.strip() for s in str(raw).split(",") if s.strip()}
        return items or None

    def is_sop_allowed(self, sop_id: str) -> bool:
        """单个 SOP 是否被放开。"""
        allowed = self.allowed_sops()
        if allowed is None:
            return True
        return sop_id in allowed

    def describe(self) -> dict:
        """分派状态摘要（供日志/运维核对）。"""
        allowed = self.allowed_sops()
        return {
            "allowed_sops": "all" if allowed is None else sorted(allowed),
        }


def build_router(config=None) -> SessionPathRouter:
    """构造分派器（供 EmilyCore 注入）。"""
    return SessionPathRouter(config=config)
