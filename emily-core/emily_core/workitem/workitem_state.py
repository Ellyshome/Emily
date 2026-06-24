"""WorkItemState —— WorkItem 状态机（蓝图 §5.5）。

WorkItem 是最小任务执行单元。状态机定义了合法转换；调度器在岔路口决策。

    CREATED → PLANNING → EXECUTING → DONE
                            │
                            ├── WAITING_CONFIRM → EXECUTING（用户回复后恢复）
                            └── FAILED（不可恢复错误，终态）

注：状态机细节按蓝图说明"待后续根据使用需求逐步完善"，此处为基础骨架。
"""

from __future__ import annotations

from enum import Enum


class WorkItemState(Enum):
    """WorkItem 状态枚举（蓝图 §5.5）。"""

    CREATED = "CREATED"                  # 已创建，加入 Session-Scheduler 队列
    PLANNING = "PLANNING"                # Node 1+2：意图分析 + 制定计划
    EXECUTING = "EXECUTING"              # Node 3：在公共 Pipeline BUS 上执行 + 验收
    WAITING_CONFIRM = "WAITING_CONFIRM"  # 挂起，等 Session-Agent 代理交互
    DONE = "DONE"                        # Node 4：成果总结完成（终态）
    FAILED = "FAILED"                    # 不可恢复错误（终态）


# 合法状态转换表
TRANSITIONS: dict[WorkItemState, list[WorkItemState]] = {
    WorkItemState.CREATED:         [WorkItemState.PLANNING, WorkItemState.FAILED],
    WorkItemState.PLANNING:        [WorkItemState.EXECUTING, WorkItemState.FAILED],
    WorkItemState.EXECUTING:       [
        WorkItemState.DONE,
        WorkItemState.WAITING_CONFIRM,
        WorkItemState.FAILED,
    ],
    WorkItemState.WAITING_CONFIRM: [WorkItemState.EXECUTING, WorkItemState.FAILED],
    WorkItemState.DONE:            [],   # 终态
    WorkItemState.FAILED:          [],   # 终态
}

# 终态集合
TERMINAL_STATES = frozenset({WorkItemState.DONE, WorkItemState.FAILED})
