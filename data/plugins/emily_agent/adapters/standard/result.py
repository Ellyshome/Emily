"""RouteResult + HandlerResult + AgentResult —— 契约对象。

RouteResult：结构化参数载体，由工具从 MasterAgent 的 tool arguments 构建，
            传给 Application Handler。
HandlerResult：Handler 输出，描述处理结果和回复内容。
AgentResult：MasterAgent 输出，描述 ReAct 循环的执行结果。
AgentStep：MasterAgent 执行过程中的单步记录。
"""

from dataclasses import dataclass, field


@dataclass
class RouteResult:
    """结构化参数载体，由工具从 tool arguments 构建后传给 Application Handler。

    Attributes:
        intent: 业务操作类型（event_record / task_record / meeting_record / file_record / query / chat）
        project_id: 项目 UUID（可能为空，待确认）
        project_name: 项目名称（从消息或上下文提取）
        confidence: 置信度 0.0-1.0
        data: 操作相关的结构化参数（如事件的 title/event_type/description 等）
    """

    intent: str
    project_id: str | None = None
    project_name: str | None = None
    confidence: float = 0.0
    data: dict = field(default_factory=dict)


@dataclass
class HandlerResult:
    """Handler 的处理结果。

    由各 Handler（EventApplication 等）返回，供 MessageApplication 生成回复。

    Attributes:
        success: 是否处理成功
        object_type: 创建的对象类型（"event" / "task" 等）
        object_id: 创建的对象 UUID
        reply: 回复文本（发送给用户的消息）
        error_code: 错误码（处理失败时）
        pending_confirmation: 是否需要用户确认（pending 状态）
    """

    success: bool
    object_type: str | None = None
    object_id: str | None = None
    reply: str | None = None
    error_code: str | None = None
    pending_confirmation: bool = False


# ── M7: MasterAgent 结果类型 ──


@dataclass
class AgentStep:
    """MasterAgent 执行过程中的一个步骤。

    用于日志记录和调试，不直接发送给用户。

    Attributes:
        step_index: 步骤序号（从 0 开始）
        type: 步骤类型 — "think" | "tool_call" | "tool_result" | "respond"
        detail: 步骤详情（JSON 字符串或自然语言描述）
        timestamp: ISO 时间戳
    """

    step_index: int
    type: str
    detail: str
    timestamp: str = ""


@dataclass
class AgentResult:
    """MasterAgent 的最终输出。

    Attributes:
        success: 是否处理成功
        reply: 发送给用户的回复文本
        steps: 执行步骤记录（调试用）
        error_code: 错误码（success=False 时）
    """

    success: bool
    reply: str
    steps: list[AgentStep] = field(default_factory=list)
    error_code: str | None = None
