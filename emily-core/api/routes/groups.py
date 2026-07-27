"""POST /api/v1/groups/sync —— 同步 bot 加入的群列表。"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from ..server import get_core

logger = logging.getLogger("emily.api.groups")

router = APIRouter()


class GroupItem(BaseModel):
    group_id: str
    group_name: str = ""
    member_count: int = 0
    platform: str = ""


class GroupsSyncIn(BaseModel):
    groups: list[GroupItem]


@router.post("/groups/sync")
async def sync_groups(payload: GroupsSyncIn):
    """同步 bot 加入的群列表到 core。"""
    core = get_core()
    count = core._group_registry_service.upsert_groups(
        [{"group_id": g.group_id, "group_name": g.group_name,
          "member_count": g.member_count, "platform": g.platform}
         for g in payload.groups]
    )
    return {"synced": count}
