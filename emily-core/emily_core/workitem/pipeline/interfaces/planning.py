"""执行计划接口。

定义执行计划和步骤的数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlanStep:
    """执行计划中的单个步骤。

    Attributes:
        step_id: 步骤唯一标识（如 "step-01"）
        description: 步骤描述（给人看）
        tool_name: 预计使用的工具名（可为 None）
        tool_params: handler 调用的参数（Phase C）
        expected_output: 预期产出（给守护 Agent 对照）
        depends_on: 依赖的前置步骤 ID 列表
    """
    step_id: str
    description: str
    tool_name: str | None = None
    tool_params: dict = field(default_factory=dict)
    expected_output: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class ExecutionPlan:
    """执行计划。

    Attributes:
        risk_level: 风险等级（L1/L2/L3）
        steps: 执行步骤列表
        acceptance_criteria: 验收标准（供守护 Agent 使用）
        estimated_steps: 预估步骤数
    """
    risk_level: str = "L2"         # "L1" | "L2" | "L3"
    steps: list[PlanStep] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    estimated_steps: int = 0

    # 额外元数据
    _source: str = ""
