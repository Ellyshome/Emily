"""platform_ops —— 平台动作执行（薄插件的 OneBot 出口）。

本模块是**平台边界的执行者**：内核（emily-core）不持有 OneBot 连接，只能经 SSE
出站事件请求平台动作，由本模块在 AstrBot 侧执行 `bot.call_action` 并回传结果。

安全：动作名走**白名单**（`_ALLOWED_ACTIONS`）。内核只可能发出这两个动作，白名单
是纵深防御——任一内核侧缺陷都不会扩散为"任意 OneBot 动作"。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("emily.plugin.platform")

#: 允许内核请求的 OneBot 动作（fail-closed：不在表内一律拒绝）
_ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "get_group_list",         # 群清单（群台账同步）
    "get_group_member_list",  # 群成员名册（名册刷新）
})


def _iter_platforms(context):
    """遍历 AstrBot 的平台适配器实例（与 main._sync_group_list 同款取法）。"""
    platform_manager = getattr(context, "platform_manager", None)
    if platform_manager is None:
        return []
    return list(getattr(platform_manager, "platform_insts", None) or [])


def _resolve_platform(context, platform_name: str):
    """按平台标识挑选适配器实例；留空取第一个持有 bot 的实例。"""
    for platform in _iter_platforms(context):
        bot = getattr(platform, "bot", None)
        if bot is None:
            continue
        if platform_name and str(getattr(platform, "platform_name", "")) != platform_name:
            continue
        return platform
    return None


async def call_platform_action(context, action: str, params: dict | None = None,
                              platform: str = "") -> dict:
    """执行一次 OneBot 动作。

    Args:
        context: AstrBot 插件 context（持有 platform_manager）。
        action: OneBot 动作名，须在 `_ALLOWED_ACTIONS` 内。
        params: 动作参数。
        platform: 目标平台标识；留空则由本函数挑选实例。

    Returns:
        {"ok": bool, "data": Any, "error": str}
        —— 失败只回报原因，不抛异常（内核按 ok=False 走降级路径）。
    """
    if action not in _ALLOWED_ACTIONS:
        logger.warning("platform action rejected by whitelist: %s", action)
        return {"ok": False, "data": None, "error": f"该动作不被允许（{action}）"}

    if context is None:
        return {"ok": False, "data": None, "error": "插件上下文不可用"}

    platform_inst = _resolve_platform(context, platform)
    if platform_inst is None:
        return {"ok": False, "data": None,
                "error": f"未找到可用平台实例（platform={platform or '-'}）"}

    bot = getattr(platform_inst, "bot", None)
    try:
        data = await bot.call_action(action, **dict(params or {}))
    except Exception as e:  # noqa: BLE001 — 平台侧异常一律转成结构化失败
        logger.warning("platform action '%s' failed: %s", action, e)
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}"}

    return {"ok": True, "data": data, "error": ""}
