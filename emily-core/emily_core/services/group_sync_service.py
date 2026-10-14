"""GroupSyncService —— 群台账维护（群清单同步 + 人工功能备注）。

写侧唯一入口：把平台群清单同步进 `conversations`（幂等、保留人工备注、落地成员数
快照），并维护人工功能备注（含留痕）。**读取侧不在此**——三种读取统一在
`group_query_service.py`（单一出口）。

群标识解析复用 `group_query_service.resolve_group_ref`（同一份实现，不另写一套）。
"""

from __future__ import annotations

import asyncio
import logging

from ..infrastructure.database.models import Conversation
from ..infrastructure.database.session import get_session
from ..infrastructure.logging.audit import audited

logger = logging.getLogger("emily.services.group_sync")


class GroupSyncService:
    """群台账写侧：群清单同步 + 人工备注维护。"""

    def __init__(self, platform_action=None, registry=None):
        """
        Args:
            platform_action: PlatformActionService（按需拉取平台群清单）。
            registry: GroupRegistryService（执行 conversations 的幂等 upsert）。
        """
        self._platform_action = platform_action
        self._registry = registry

    # ── 群清单同步 ──

    async def sync_from_platform(self, *, platform: str = "napcat", timeout: float = 8.0) -> dict:
        """经平台拉取 bot 所在全部群并 upsert 进群台账（幂等）。

        Args:
            platform: 平台标识（既用于挑选插件的平台实例，也作为入库的 `im_platform`）。
                默认 `napcat`——本期通道范围仅 QQ（R19），企微群消息不在范围内。
            timeout: 平台动作等待秒数。

        Returns:
            {"ok": bool, "pulled": int, "upserted": int, "error": str}
            —— 失败如实回报，不抛异常（群消息留存与对话不受影响）。
        """
        if self._platform_action is None:
            return {"ok": False, "pulled": 0, "upserted": 0,
                    "error": "平台动作服务未启用"}
        if self._registry is None:
            return {"ok": False, "pulled": 0, "upserted": 0,
                    "error": "群注册服务未启用"}

        result = await self._platform_action.request(
            "get_group_list", {}, platform=platform, timeout=timeout)
        if not result.get("ok"):
            return {"ok": False, "pulled": 0, "upserted": 0,
                    "error": result.get("error") or "拉取群清单失败"}

        groups = self._normalize_group_list(result.get("data"), platform)
        if not groups:
            # 平台返回空不是错误（机器人可能确实不在任何群），但须让调用方可分辨
            logger.info("platform returned an empty group list")
            return {"ok": True, "pulled": 0, "upserted": 0, "error": ""}

        upserted = await asyncio.to_thread(self._registry.upsert_groups, groups)
        return {"ok": True, "pulled": len(groups), "upserted": upserted, "error": ""}

    @staticmethod
    def _normalize_group_list(data, platform: str) -> list[dict]:
        """平台群清单 → upsert 入参（丢弃无法定位的条目）。"""
        rows = data if isinstance(data, list) else []
        out: list[dict] = []
        for g in rows:
            if not isinstance(g, dict):
                continue
            gid = str(g.get("group_id", "") or "").strip()
            if not gid:
                continue
            out.append({
                "group_id": gid,
                "group_name": g.get("group_name", "") or "",
                "member_count": int(g.get("member_count") or 0),
                "platform": platform or "napcat",
            })
        return out

    # ── 人工功能备注 ──

    def preview_note(self, *, group_ref: str, note: str) -> dict:
        """只读预览（供脚本 --dry-run 与控制台预览）：不写库、不留痕。"""
        resolved = self._resolve(group_ref)
        if resolved.get("status") != "ok":
            return {"ok": False, "group": {}, "old_note": "", "new_note": note,
                    "error": resolved.get("error") or "群未找到", "status": resolved.get("status")}
        conv = resolved["group"]
        return {"ok": True, "group": conv, "old_note": conv.get("note", ""),
                "new_note": note, "error": "", "status": "ok"}

    @audited(
        category="group",
        action="note_updated",
        target_type="conversation",
        actor_arg="operator_id",
        summary_arg="summary",
        summary="{summary}",
    )
    def set_note(self, *, group_ref: str, note: str, operator_id: str) -> dict:
        """设置某群的人工功能备注（覆盖写 + 留痕）。

        留痕摘要形如 `备注更新：<群显示名> 旧值「…」→ 新值「…」`，可回答
        "谁 / 何时 / 旧值 → 新值"（AC-US-01.5）。

        Returns:
            {"ok", "group", "old_note", "new_note", "error", "status", "summary"}
        """
        resolved = self._resolve(group_ref)
        if resolved.get("status") != "ok":
            return {"ok": False, "group": {}, "old_note": "", "new_note": note,
                    "error": resolved.get("error") or "群未找到",
                    "status": resolved.get("status"), "summary": ""}

        conv = resolved["group"]
        old_note = conv.get("note", "")
        new_text = (note or "").strip()
        display = conv.get("display_name") or conv.get("group_id", "")

        with get_session() as session:
            row = session.query(Conversation).filter(
                Conversation.im_platform == conv["im_platform"],
                Conversation.conversation_id == conv["group_id"],
            ).first()
            if row is None:
                return {"ok": False, "group": conv, "old_note": old_note, "new_note": new_text,
                        "error": "群在台账中已不存在", "status": "not_found", "summary": ""}
            row.note = new_text
            session.commit()

        summary = (f"备注更新：{display}（{conv.get('group_id', '')}） "
                   f"旧值「{old_note}」→ 新值「{new_text}」")
        logger.info("group note updated group=%s by=%s", conv.get("group_id"), operator_id or "-")
        return {"ok": True, "group": conv, "old_note": old_note, "new_note": new_text,
                "error": "", "status": "ok", "summary": summary}

    # ── 内部 ──

    @staticmethod
    def _resolve(group_ref: str) -> dict:
        """群标识解析（复用取数出口的唯一实现，不另立口径）。"""
        from .group_query_service import resolve_group_ref

        return resolve_group_ref(group_ref)
