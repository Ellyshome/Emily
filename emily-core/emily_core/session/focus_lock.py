"""FocusLock —— 交互优先级调度器（蓝图 §4.3.4）。

本质：不是硬锁，是优先级队列 + 上下文感知的调度决策。

调度规则（蓝图 §4.3.4）：
  1. 用户最新消息指向的主题 → 自动提升为当前焦点（priority boost）
  2. 未完成的 WorkItem 待确认项 → 按紧急性排队
  3. 用户显式切换话题（如"先不管 X，说 Y"）→ 手动焦点切换
  4. 同主题多条消息 → 合并序列，不打断
  5. 旧焦点未确认超时 → 温和提醒，不强制阻塞新消息

本期为占位最小实现：维护"当前焦点 WorkItem"，提供设置/切换/清除。
完整的话题关联判定（消息 → WorkItem 匹配）属 Phase B/C。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.focus_lock")

# 显式焦点切换关键词（蓝图规则 3）
_SWITCH_HINTS = ("先不管", "先处理", "等一下", "先说", "改成先")


class FocusLock:
    """交互优先级调度器（软锁）。"""

    def __init__(self):
        self._current_focus: str | None = None  # 当前焦点 WorkItem id

    @property
    def current_focus(self) -> str | None:
        """当前焦点 WorkItem id（无焦点返回 None）。"""
        return self._current_focus

    def set_focus(self, workitem_id: str) -> None:
        """设置当前焦点（priority boost）。"""
        if self._current_focus != workitem_id:
            logger.debug("FocusLock: focus %s → %s", self._current_focus, workitem_id)
            self._current_focus = workitem_id

    def clear_focus(self) -> None:
        """清除当前焦点（焦点交互完成）。"""
        self._current_focus = None

    @staticmethod
    def wants_switch(content: str) -> bool:
        """判断用户消息是否显式要求切换焦点（蓝图规则 3）。"""
        text = (content or "").strip()
        return any(hint in text for hint in _SWITCH_HINTS)
