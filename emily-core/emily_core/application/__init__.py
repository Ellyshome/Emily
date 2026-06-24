"""application —— 应用层编排包。

注：旧的 MessageApplication（M15 8 阶段 PipelineScheduler 编排）已被
Session 主线编排层（session/ + workitem/）取代，不再属于本包。
本包仅保留按实体划分的业务编排（事件/任务/会议/文件/查询），供工具层复用。
"""

from .event_app import EventApplication
from .task_app import TaskApplication
from .meeting_app import MeetingApplication
from .file_app import FileApplication
from .query_app import QueryApplication

__all__ = [
    "EventApplication",
    "TaskApplication",
    "MeetingApplication",
    "FileApplication",
    "QueryApplication",
]
