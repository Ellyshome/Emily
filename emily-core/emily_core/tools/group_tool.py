"""query_groups —— 群信息查询（只读业务工具）。

让对话侧能够：① 列出系统正在保存信息的群；② 按关键词检索群消息原文。
取数一律经 `GroupQueryService`（三种读取与作用域收敛的**唯一实现**），本文件只做
参数校验、结果呈现与失败语义，不含任何检索逻辑。

只读：不写任何表（AC-US-10.3）；档位由身份推定，调用方不得自行指定。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("emily.tool.query_groups")

_QUERY_GROUPS_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "messages"],
            "description": (
                "list = 列出系统正在保存信息的群（有哪些群可查）；"
                "messages = 按关键词检索群消息原文"
            ),
        },
        "keyword": {
            "type": "string",
            "description": "检索关键词（action=messages 时必填）；按原文字串匹配，不做语义扩召回",
        },
        "group": {
            "type": "string",
            "description": (
                "群，可用备注名 / 平台群名 / 群号任一表达（不需提供内部 ID）；"
                "留空 = 跨全部在追踪群检索"
            ),
        },
        "time_range": {
            "type": "string",
            "enum": ["today", "week", "month", "all"],
            "description": "时间范围，默认 all（全部已留存区间）",
        },
        "limit": {
            "type": "integer",
            "description": "返回条数上限（默认 20，最大 50）",
        },
        "include_sources": {
            "type": "boolean",
            "description": "是否包含测试与运维来源的消息（默认 false = 排除，避免测试数据污染结论）",
        },
    },
    "required": ["action"],
}

_QUERY_GROUPS_DESCRIPTION = (
    "查询群信息（只读）。系统持续留存 QQ 群内的消息（含未 @ 机器人的消息），本工具用于：\n"
    "  · action=\"list\"：列出系统正在保存信息的群 —— 每条含备注名（无备注时平台群名）、"
    "平台、成员数、已留存消息数、最近活跃时间。用于回答「系统在保存哪些群」「有哪些群可以问」\n"
    "  · action=\"messages\"：按关键词检索群消息原文 —— 每条含所属群、发言人、时间、原文片段。"
    "可限定单个群（group 传备注名/群名/群号任一表达）或跨全部在追踪群；可限定时间范围\n"
    "典型用途：某材料的进场时间、验收时间、人员到场、变更通知这类信息常由某人在群里报备，"
    "可到群里找原文自行判断（本工具不做结论、不做摘要）。\n"
    "口径：子串匹配，不做向量检索或语义扩召回；默认排除测试与运维来源；"
    "空结果如实回「没有匹配记录」，不臆造。\n"
    "权限：只读能力，全员可调用；检索结果按发起者身份收敛 —— 建设主管（L4）及以上可见全部群消息，"
    "其余身份仅可见其当前参与的（在群成员名册内在册的）群；名册数据缺失时一律不返回。"
)


async def handle_query_groups(params: dict[str, Any], user_id: str = "", **kw) -> dict[str, Any]:
    """群信息查询（action=list / messages）。只读、幂等、失败不阻断。"""
    from api.server import get_core

    params = params or {}
    action = str(params.get("action") or "list").strip()
    if action not in ("list", "messages"):
        return {"success": False, "reply": f"不支持的操作类型：{action}（支持 list / messages）",
                "error_code": "bad_action"}

    try:
        core = get_core()
        svc = getattr(core, "_group_query_service", None)
    except Exception as e:  # noqa: BLE001 — 取数异常不影响对话主链路
        return {"success": False, "reply": f"群信息服务不可用：{e}", "error_code": "service_unavailable"}

    if svc is None:
        return {"success": False, "reply": "群信息服务未启用", "error_code": "service_unavailable"}

    # 档位：会话快照优先（_session_scope），否则查库；**不由调用方指定**
    scope_params = params.get("_session_scope") or {}
    session_level = scope_params.get("level")
    actor_user_id = str(user_id or params.get("_user_id") or "").strip()
    level = svc.resolve_actor_level(actor_user_id, session_level)

    try:
        if action == "list":
            return _handle_list(svc, params)
        return _handle_messages(svc, params, actor_user_id, level)
    except Exception as e:  # noqa: BLE001 — 如实回报，不阻断对话
        logger.error("query_groups failed: %s", e, exc_info=True)
        return {"success": False, "reply": f"群信息查询失败：{e}", "error_code": "query_failed"}


def _handle_list(svc, params: dict) -> dict[str, Any]:
    """action=list：列出在追踪群。"""
    page_size = _clamp(params.get("limit"), default=50, upper=50)
    result = svc.list_tracked_groups(keyword=str(params.get("keyword") or ""),
                                     page_size=page_size)
    rows = result["rows"]
    total = result["total"]
    if total == 0:
        return {"success": True, "data": result, "total": 0,
                "reply": "系统当前没有在追踪的群。"}

    lines = [
        f"{i + 1}. {r['display_name']}"
        f"（{r['im_platform']} · 群号 {r['group_id']}）"
        f" 成员 {r['member_count']} 人 · 已留存 {r['message_count']} 条 · 最近活跃 {_fmt_time(r['last_active_at'])}"
        + ("（名册待刷新）" if r.get("roster_stale") else "")
        for i, r in enumerate(rows)
    ]
    shown = len(rows)
    head = (f"系统正在保存信息的群共 {total} 个"
            + (f"，本次返回 {shown} 个：" if shown < total else "："))
    return {
        "success": True,
        "data": result,
        "total": total,
        "reply": head + "\n" + "\n".join(lines),
    }


def _handle_messages(svc, params: dict, actor_user_id: str, level: int) -> dict[str, Any]:
    """action=messages：按关键词检索群消息原文。"""
    keyword = str(params.get("keyword") or "").strip()
    if not keyword:
        return {"success": False, "reply": "请提供检索关键词（keyword）", "error_code": "missing_keyword"}

    limit = _clamp(params.get("limit"), default=20, upper=50)
    result = svc.search_group_messages(
        actor_user_id=actor_user_id,
        level=level,
        keyword=keyword,
        group_ref=str(params.get("group") or ""),
        time_range=str(params.get("time_range") or "all"),
        limit=limit,
        include_sources=bool(params.get("include_sources", False)),
    )

    # 群标识解析不出 / 歧义：如实回报，不以近似群代替（AC-US-07.4 / AC-US-10.1）
    status = result.get("status")
    if status == "not_found":
        return {"success": True, "data": result, "total": 0,
                "reply": f"未找到与「{params.get('group')}」匹配的群，请确认群名或群号。"}
    if status == "ambiguous":
        cands = "；".join(f"{c['display_name']}（群号 {c['group_id']}）"
                          for c in result.get("candidates") or [])
        return {"success": True, "data": result, "total": 0,
                "reply": f"匹配到多个群，请指明具体哪一个：{cands}"}

    if result.get("scope_denied"):
        # 作用域拒绝：回复中不泄露该群任何消息内容（AC-US-08.3）
        where = result.get("group", {}).get("display_name") or params.get("group") or "该群"
        return {"success": True, "data": {"rows": [], "total": 0}, "total": 0,
                "reply": f"「{where}」没有匹配记录。"}

    rows = result["rows"]
    total = result["total"]
    if not rows:
        where = result.get("group", {}).get("display_name") or "全部在追踪群"
        return {"success": True, "data": result, "total": 0,
                "reply": f"「{where}」在指定时间范围内没有匹配「{keyword}」的记录。"}

    lines = [
        f"{i + 1}. [{r['group_display']}] {r['sender_name'] or '（未知发言人）'}"
        f" {_fmt_time(r['created_at'])}：{r['content']}"
        for i, r in enumerate(rows)
    ]
    head = f"命中 {total} 条"
    if result.get("truncated"):
        head += f"，本次返回 {len(rows)} 条（已截断）"
    head += f"（关键词：{keyword}）："
    return {"success": True, "data": result, "total": total,
            "reply": head + "\n" + "\n".join(lines)}


def _clamp(value, *, default: int, upper: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, upper)


def _fmt_time(raw: str) -> str:
    text = str(raw or "")
    return text.replace("T", " ")[:16] if text else "—"
