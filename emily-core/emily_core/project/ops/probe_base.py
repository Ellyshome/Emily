"""Probe 抽象基类 + ProbeFinding + TickContext。

Probe 接口体系：
  • Probe — 抽象基类，定义 name()/run() 两个抽象方法
  • ProbeFinding — 单条发现结果 dataclass
  • TickContext — 单次 Tick 的上下文信息
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProbeFinding:
    """探针单次发现结果。

    Attributes:
        finding_type: 发现类型标识（如 STALE_NODE / MILESTONE_WARNING / MAIL_COMMAND）
        severity: 严重程度（INFO / WARNING / CRITICAL）
        target_id: 关联目标 ID（如 node_id / mail_uid）
        message: 人类可读的发现描述
        metadata: 额外元数据（JSON 可序列化）
    """
    finding_type: str
    severity: str
    target_id: str
    message: str
    metadata: dict = field(default_factory=dict)


@dataclass
class TickContext:
    """单次 Tick 的上下文信息。

    由 OpsScheduler.run_tick() 在每轮开始前创建，传递给所有 Probe。
    内部记录各 Probe 的上次运行时间，供 should_run() 冷却判断。
    """
    tick_id: str
    tick_number: int
    start_time: datetime
    _last_runs: dict[str, datetime] = field(default_factory=dict, init=False)

    def get_last_run_time(self, name: str) -> datetime | None:
        """获取指定 Probe 的上次运行时间。"""
        return self._last_runs.get(name)

    def set_last_run_time(self, name: str, dt: datetime) -> None:
        """记录指定 Probe 的运行时间。"""
        self._last_runs[name] = dt


class Probe(ABC):
    """运维探针抽象基类。

    每个 Probe 代表一项运维检查任务。新增运维检查只需实现本接口，
    注册到 OpsScheduler 即可，无需修改核心调度代码。

    关键设计：
      • name() / run() 是 @abstractmethod —— 子类必须实现
      • enabled() / interval_seconds() / should_run() 是普通方法 —— 有默认实现
    """

    @abstractmethod
    def name(self) -> str:
        """返回探针唯一名称。"""
        ...

    @abstractmethod
    def run(self, ctx: TickContext) -> list[ProbeFinding]:
        """执行一次探针检查，返回发现结果列表。"""
        ...

    def enabled(self) -> bool:
        """是否启用此探针。子类可覆盖以实现条件启用。"""
        return True

    def interval_seconds(self) -> int:
        """此探针的建议执行间隔（秒）。默认 300（5 分钟）。"""
        return 300

    def should_run(self, ctx: TickContext) -> bool:
        """判断此探针在本轮 Tick 是否应该运行。

        默认逻辑：enabled 为 True 且距上次运行超过 interval_seconds。
        """
        if not self.enabled():
            return False
        last = ctx.get_last_run_time(self.name())
        if last is None:
            return True
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed >= self.interval_seconds()
