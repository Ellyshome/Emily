"""Stage status enumeration for the global state machine.

Stages (stage 1-7) have a simpler state model than individual nodes.
They represent the macro-level project progress view.

Source: 需求文件/全局状态机/全局状态机需求-架构师完善版.md §4.2
"""

from enum import Enum


class StageStatus(Enum):
    """Three states for project stages (macro view)."""
    NOT_STARTED = "NOT_STARTED"       # 未启动
    IN_PROGRESS = "IN_PROGRESS"       # 进行中
    COMPLETED  = "COMPLETED"          # 已完成

    def __str__(self) -> str:
        return self.value


# Allowed stage progression (simpler than nodes — no BLOCKED/DELAYED at stage level)
STAGE_TRANSITIONS: dict[StageStatus, list[StageStatus]] = {
    StageStatus.NOT_STARTED:  [StageStatus.IN_PROGRESS],
    StageStatus.IN_PROGRESS:  [StageStatus.COMPLETED],
    StageStatus.COMPLETED:    [],  # terminal
}

STAGE_TERMINAL_STATES = frozenset({StageStatus.COMPLETED})

STAGE_LABELS: dict[int, str] = {
    1: "项目投资立项与合规研判",
    2: "设计成果交付",
    3: "规划报批与行政许可",
    4: "工程招标与开工筹备",
    5: "全域施工建造与过程管控",
    6: "专项核验与竣工联合验收",
    7: "交付整改与业主确权交房",
}

STAGE_BOUNDARIES: dict[int, dict[str, str]] = {
    1: {"entry": "地块摘牌/意向拿地完成", "exit": "取得《建设项目备案证》"},
    2: {"entry": "立项备案办结、勘测定界完成", "exit": "取得《施工图审查合格书》"},
    3: {"entry": "立项备案办结", "exit": "取得《建设工程规划许可证》、完成施工图审查备案、完成招标控制价编制"},
    4: {"entry": "设计成果交付完成", "exit": "取得《建筑工程施工许可证》、施工单位正式进场"},
    5: {"entry": "施工单位正式进场动工", "exit": "施工单位完成竣工自评报告"},
    6: {"entry": "工程施工自评完工", "exit": "取得《房屋建筑工程竣工验收备案表》"},
    7: {"entry": "竣工备案办结", "exit": "项目完成集中交付，办结不动产初始登记"},
}
