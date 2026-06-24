"""ConfirmQueue —— 待确认任务队列（蓝图 §4.3.3）。

多个 WorkItem 需要用户交互时，按优先级排队，由 Session-Agent 逐项向用户呈现。

    待确认队列 (PriorityQueue)
        ├── WorkItem-A 需要用户确认参数 → 排队序号 #1
        ├── WorkItem-B 执行完成需审核   → 排队序号 #2
        └── WorkItem-C 遇到异常需决策   → 排队序号 #3

本期为占位最小实现：优先级排序 + FIFO 取出。完整的焦点匹配 / 投递回对应
WorkItem 属 Phase B/C。
"""

from __future__ import annotations

import heapq
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.confirm_queue")


@dataclass(order=True)
class _Entry:
    """优先级队列条目（priority 小者先出，同优先级 FIFO）。"""
    priority: int
    seq: int
    workitem_id: str = field(compare=False)
    prompt: str = field(compare=False)
    payload: Any = field(default=None, compare=False)


class ConfirmQueue:
    """待确认任务优先级队列。"""

    def __init__(self):
        self._heap: list[_Entry] = []
        self._counter = itertools.count()

    def add(self, workitem_id: str, prompt: str, priority: int = 1, payload: Any = None) -> None:
        """加入一个待确认项（priority 0 最高）。"""
        entry = _Entry(
            priority=priority,
            seq=next(self._counter),
            workitem_id=workitem_id,
            prompt=prompt,
            payload=payload,
        )
        heapq.heappush(self._heap, entry)
        logger.debug("ConfirmQueue add: WI=%s priority=%d (size=%d)",
                     workitem_id, priority, len(self._heap))

    def pop(self) -> _Entry | None:
        """取出优先级最高的待确认项（队列空返回 None）。"""
        if not self._heap:
            return None
        return heapq.heappop(self._heap)

    def peek(self) -> _Entry | None:
        """查看队首但不取出。"""
        return self._heap[0] if self._heap else None

    def remove(self, workitem_id: str) -> bool:
        """移除指定 WorkItem 的待确认项（用户已响应）。

        特殊值 "__all__" 清空整个队列。
        """
        if workitem_id == "__all__":
            self._heap.clear()
            return True
        before = len(self._heap)
        self._heap = [e for e in self._heap if e.workitem_id != workitem_id]
        heapq.heapify(self._heap)
        return len(self._heap) < before

    def clear(self) -> None:
        """清空所有待确认项。"""
        self._heap.clear()

    @property
    def is_empty(self) -> bool:
        return not self._heap

    def __len__(self) -> int:
        return len(self._heap)
