"""确认/取消对话化模块（M6）—— 待确认项在对话中呈现与处理。

定位（计划 M6 / PRD F9、D4、US-07）：
  - 把"待确认项"从工单特例改为会话循环内的对话行为；
  - 确认/取消动作**复用现有确认存储链路**（EventApplication.handle_confirmation），
    不新增第二套确认存储（AC-US-07.2）。

对外提供：
  · `ConfirmDialog.fetch_pending()`      取当前会话待确认项
  · `ConfirmDialog.prompt_injection()`   产出注入主循环的提示块
  · `ConfirmDialog.handle()`             执行确认/取消
  · `CONFIRM_SPEC` / `CANCEL_SPEC`       暴露给循环的控制工具（带 JSON Schema）
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.session.confirm_dialog")


CONFIRM_SPEC = {
    "type": "function",
    "function": {
        "name": "confirm_pending",
        "description": (
            "确认当前会话中待确认的录入项，使其正式生效。"
            "仅当存在待确认项、且用户表达了确认意图时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_no": {
                    "type": "string",
                    "description": "待确认项编号（如 EVT-xxxx）；留空表示处理当前会话唯一待确认项",
                },
            },
            "required": [],
        },
    },
}

CANCEL_SPEC = {
    "type": "function",
    "function": {
        "name": "cancel_pending",
        "description": (
            "取消当前会话中待确认的录入项，使其作废。"
            "仅当存在待确认项、且用户表达了取消/放弃意图时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_no": {
                    "type": "string",
                    "description": "待确认项编号（如 EVT-xxxx）；留空表示处理当前会话唯一待确认项",
                },
            },
            "required": [],
        },
    },
}

CONTROL_TOOL_SPECS = [CONFIRM_SPEC, CANCEL_SPEC]
CONTROL_TOOL_NAMES = {"confirm_pending", "cancel_pending"}


class ConfirmDialog:
    """会话内确认交互组件。"""

    def __init__(self, journal: Any = None, event_app: Any = None) -> None:
        self._journal = journal
        self._event_app = event_app

    # ── 读取待确认项 ──

    def fetch_pending(self, conversation_id: str):
        """取当前会话待确认项（复用现有事件仓储）。"""
        if not conversation_id:
            return None
        try:
            from ..repositories.event_repo import EventRepository
            return EventRepository().find_pending_by_conversation_id(conversation_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("ConfirmDialog.fetch_pending failed: %s", e)
            return None

    def prompt_injection(self, pending) -> dict | None:
        """产出注入主循环的提示块（无待确认项返回 None）。"""
        if pending is None:
            return None
        return {
            "role": "system",
            "content": (
                "⚠️ 当前存在待确认的录入项：\n"
                f"  编号：{getattr(pending, 'event_no', '')}\n"
                f"  内容：{getattr(pending, 'title', '')}\n"
                "  状态：等待用户确认\n"
                "  若用户表达确认意图 → 调用 confirm_pending；"
                "若表达取消/放弃意图 → 调用 cancel_pending；"
                "不要另起其他业务能力处理该内容。"
            ),
        }

    # ── 执行确认 / 取消 ──

    async def handle(self, action: str, event_id: str, confirmed_by: str) -> str:
        """执行确认/取消，返回可读回复（复用现有确认链路）。"""
        if not event_id:
            return "没有待确认的事项。请先完成录入。"

        try:
            from ..repositories.event_repo import EventRepository
            event_repo = EventRepository()
            event = event_repo.get_by_id(event_id)
            if event is None:
                return "找不到该记录，可能已被处理。"
            if getattr(event, "status", "") != "pending":
                return f"该事项（{event.event_no}）已经处理过了，当前状态为「{event.status}」。"

            event_app = self._event_app or self._build_event_app()
            result = event_app.handle_confirmation(
                event_id=event_id, action=action, confirmed_by=confirmed_by,
            )
            logger.info("ConfirmDialog: event=%s action=%s success=%s",
                        getattr(event, "event_no", ""), action, getattr(result, "success", None))
            return getattr(result, "reply", "") or "处理完成。"
        except Exception as e:  # noqa: BLE001
            logger.error("ConfirmDialog.handle failed: %s", e, exc_info=True)
            return f"确认处理失败：{e}"

    def _build_event_app(self):
        """构造 EventApplication（复用 Core 注入的 journal，缺失时临时构造）。"""
        from ..application.event_app import EventApplication
        from ..services.event_service import EventService
        from ..services.event_journal import EventJournal

        app = EventApplication(EventService())
        journal = self._journal
        if journal is None:
            try:
                journal = EventJournal(path="", enabled=True)
            except Exception:  # noqa: BLE001
                journal = None
        if journal is not None:
            try:
                app.set_journal(journal)
            except Exception as e:  # noqa: BLE001
                logger.debug("set_journal skipped: %s", e)
        return app

    def resolve_event(self, conversation_id: str, event_no: str = ""):
        """按编号（或唯一待确认项）解析事件实体，返回 (event_id, event_no)。"""
        pending = self.fetch_pending(conversation_id)
        if pending is None:
            return "", ""
        if event_no and getattr(pending, "event_no", "") != event_no:
            logger.info("ConfirmDialog: event_no=%s 与当前待确认项 %s 不一致",
                        event_no, getattr(pending, "event_no", ""))
        return getattr(pending, "id", ""), getattr(pending, "event_no", "")
