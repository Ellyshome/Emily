"""监控 API Pydantic Schemas —— 响应体定义。

参照模式：api/routes/node_schemas.py（ApiResponse + BaseModel + Field）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# 通用响应
# ══════════════════════════════════════════════════════════════════════════════

class MonitorApiResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: dict | list | None = None


# ══════════════════════════════════════════════════════════════════════════════
# 容器状态
# ══════════════════════════════════════════════════════════════════════════════

class ContainerItem(BaseModel):
    name: str = Field(..., description="容器名")
    status: str = Field(..., description="running / stopped / unknown")
    image: str = Field(default="", description="镜像名")


class IMAccountItem(BaseModel):
    platform: str = Field(..., description="IM 平台标识")
    label: str = Field(..., description="显示名")
    status: str = Field(..., description="connected / disconnected / no_account")
    webui_available: bool = Field(default=False, description="WebUI 是否可用")
    webui_token: str = Field(default="", description="WebUI 免密登录 token")
    qq_number: str = Field(default="", description="QQ 号码（已登录时）")
    qq_nickname: str = Field(default="", description="QQ 昵称（已登录时）")


# ══════════════════════════════════════════════════════════════════════════════
# Session 池
# ══════════════════════════════════════════════════════════════════════════════

class SessionItem(BaseModel):
    conversation_id: str = Field(..., description="会话 ID")
    last_active_ts: float = Field(..., description="最后活跃时间戳")
    idle_seconds: int = Field(..., description="空闲秒数")


class SessionPoolResponse(BaseModel):
    total: int = Field(0, description="活跃 Session 数")
    uptime_seconds: int = Field(0, description="池运行时长")
    sessions: list[SessionItem] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 消息
# ══════════════════════════════════════════════════════════════════════════════

class MessageItem(BaseModel):
    sender_name: str = Field(default="", description="发送者")
    direction: str = Field(default="", description="user_to_agent / agent_to_user")
    content_summary: str = Field(default="", description="内容摘要（80字截断）")
    created_at: str = Field(default="", description="时间")
