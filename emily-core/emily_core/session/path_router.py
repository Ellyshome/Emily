"""会话路径分派（M8）—— 双轨灰度开关 + SOP 准入清单。

定位（计划 M8 / PRD US-09、D6）：
  - 全局开关（`session_loop_enabled`，默认 **关**）决定入口走新会话主循环还是旧链路；
  - 按 SOP 准入清单（`session_loop_sop_allowlist`）控制哪些 SOP 能力对新循环可见
    （灰度迁移顺序：查询能力 → 写类 SOP 逐个放开）；
  - 由于 R2/D6 禁止回合开始的显式分类，消息入口**无法**按 SOP 分流，故清单实现为
    "能力目录准入清单"（放开 = 该 SOP 能力对新循环可见）。见计划 M8「灰度语义」。

开关关闭时，新模块不被实例化或不被调用，旧链路行为完全不变。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("emily.session.path_router")


class SessionPathRouter:
    """会话路径分派器。"""

    def __init__(self, config=None) -> None:
        self._config = config

    def use_loop(self) -> bool:
        """是否启用新会话主循环（默认 False）。"""
        return bool(getattr(self._config, "session_loop_enabled", False))

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
            "use_loop": self.use_loop(),
            "allowed_sops": "all" if allowed is None else sorted(allowed),
        }


def build_router(config=None) -> SessionPathRouter:
    """构造分派器（供 EmilyCore 注入）。"""
    return SessionPathRouter(config=config)
