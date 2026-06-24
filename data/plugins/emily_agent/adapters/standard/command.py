"""Command 对象 —— M3/M4 各意图的参数封装。

由 Application 层从 RouteResult 构建，传给 Service 层消费。
"""

from dataclasses import dataclass


@dataclass
class EventCommand:
    """事件录入命令。"""
    project_id: str | None = None
    project_name: str | None = None
    title: str = ""
    event_type: str = "general"
    category: str = "待分类"
    description: str = ""
    event_date: str | None = None
    creator_id: str = ""
    source_message_id: str = ""
    related_event_ids: list[str] | None = None  # M8a: 关联事件编号列表


@dataclass
class TaskCommand:
    """任务创建命令。"""
    project_id: str | None = None
    project_name: str | None = None
    title: str = ""
    description: str = ""
    assignee_text: str = ""        # LLM 提取的负责人文本
    due_date: str | None = None
    due_text: str = ""             # LLM 提取的原始截止日期文本
    creator_id: str = ""
    source_message_id: str = ""


@dataclass
class MeetingCommand:
    """会议记录命令。"""
    project_id: str | None = None
    project_name: str | None = None
    title: str = ""
    summary: str = ""
    attendees: list[str] | None = None
    creator_id: str = ""
    source_message_id: str = ""


@dataclass
class FileCommand:
    """文件归档命令。"""
    project_id: str | None = None
    project_name: str | None = None
    filename: str = ""
    file_type: str = ""
    file_size: int = 0
    storage_path: str = ""
    uploaded_by: str = ""
    source_message_id: str = ""


# ── M5 查询命令 ──


@dataclass
class QueryCommand:
    """结构化查询命令。"""
    query_type: str = "event"          # event|task|meeting|file|message|conversation|user|project|summary
    project_id: str | None = None
    project_name: str | None = None
    time_range: str = "all"            # today|this_week|this_month|all
    status_filter: str | None = None   # pending|confirmed|todo|done|active 等
    assignee: str | None = None        # 按负责人筛选（task）
    sender_name: str | None = None     # 按发送者筛选（message）
    keyword: str | None = None         # 关键词搜索（message）
    intent: str | None = None          # 按意图筛选（message）
    file_type: str | None = None       # 文件类型（file）
    conversation_id: str | None = None # 会话 ID（message）
    limit: int = 50
