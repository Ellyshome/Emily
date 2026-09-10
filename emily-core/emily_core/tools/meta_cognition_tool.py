"""meta_cognition_read —— 三书全文按需检索（按操作者权限裁剪）。

常驻摘要（{project_brief}/{rule_brief}/{system_brief}）不够用时，Agent 用本工具
拉取裁剪后的三书全文。归 base 类（所有 SOP 可用）+ FallbackPolicy basic 白名单
（兜底档可见，只读零写）。

参照模式：emily_core/tools/knowledge_search_tool.py（schema + handler + 签名注入 user_id）
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("emily.tool.meta_cognition")

_VALID_BOOKS = ("world", "rule", "system")

_EMPTY_REPLY = {
    "world": "当前没有可访问的项目态势信息（无项目世界书或你没有授权节点）。",
    "rule": "规则书暂不可用，请稍后重试或联系管理员。",
    "system": "系统能力描述暂不可用，请稍后重试或联系管理员。",
}

_META_COG_SCHEMA = {
    "type": "object",
    "properties": {
        "book": {
            "type": "string",
            "enum": ["world", "rule", "system"],
            "description": (
                "要检索的书：world=项目态势全文（项目/节点/事件/阻塞）；"
                "rule=组织规则全文（按你的权限等级裁剪）；"
                "system=系统能力全文（你可访问的数据表/文件分类/权限体系）"
            ),
        },
        "query": {
            "type": "string",
            "description": "可选关键词，用于在裁剪后的全文中做关键词过滤；不传则返回全文",
        },
    },
    "required": ["book"],
}

_META_COG_DESCRIPTION = (
    "按需检索「项目态势 / 组织规则 / 系统能力」三书的裁剪后全文。"
    "当常驻摘要信息不足、需要更详细的项目节点/规则条文/可访问数据表时调用。"
    "参数 book 必选（world/rule/system）；参数 query 可选，用于关键词过滤。"
    "返回内容已按当前调用者权限裁剪，不会越权。"
)


async def handle_meta_cognition_read(params: dict, user_id: str | None = None, core=None) -> dict:
    """处理三书全文检索。

    Args:
        params: {"book": "world|rule|system", "query": "可选关键词"}
        user_id: 当前操作者 UUID（框架按 handler 签名自动注入）
        core: EmilyCore 实例（registry 注册时 partial 注入）

    Returns:
        {"success": bool, "book": str, "matched_lines": int, "reply": str}
    """
    book = str((params or {}).get("book", "") or "").strip().lower()
    query = str((params or {}).get("query", "") or "").strip()

    if book not in _VALID_BOOKS:
        return {
            "success": False, "book": book, "matched_lines": 0,
            "reply": "参数 book 必须是 world / rule / system 之一。",
        }

    # 权限快照（fail-closed：取不到 → 空权限 → 最小可见集）
    perms: dict = {}
    if user_id:
        try:
            from ..session.session_data_fetcher import SessionDataFetcher
            perms = await asyncio.to_thread(
                SessionDataFetcher.fetch_actor_snapshot, user_id, core,
            ) or {}
        except Exception as e:
            logger.warning("meta_cognition_read perms fetch failed user=%s: %s", user_id, e)
            perms = {}

    # 读取 + 裁剪（sync IO 包 to_thread）
    try:
        text = await asyncio.to_thread(_render_book, book, perms)
    except Exception as e:
        logger.error("meta_cognition_read render failed book=%s: %s", book, e, exc_info=True)
        return {"success": False, "book": book, "matched_lines": 0, "reply": "读取失败，请稍后重试。"}

    if not text:
        return {"success": False, "book": book, "matched_lines": 0, "reply": _EMPTY_REPLY[book]}

    filtered, matched = _apply_query(text, query)
    if query and matched == 0:
        return {
            "success": True, "book": book, "matched_lines": 0,
            "reply": f"在{book}全文中未找到与「{query}」相关的内容。",
        }
    return {"success": True, "book": book, "matched_lines": matched, "reply": filtered}


def _render_book(book: str, perms: dict) -> str:
    """读取并裁剪指定书的全文（sync，纯 IO + 纯函数）。"""
    level = int(perms.get("level", 1) or 1)

    if book == "rule":
        from ..services.rule_book_loader import RuleBookLoader, parse_sections, render_full
        return render_full(parse_sections(RuleBookLoader().content), level)

    if book == "system":
        from ..repositories.system_description_repo import SystemDescriptionRepo
        from ..session.fetchers.fetch_system_description import render_full
        desc = SystemDescriptionRepo.get_latest()
        if desc is None:
            return ""
        return render_full(desc.content_json, perms)

    # book == "world"
    project_ids = list(perms.get("project_ids") or [])
    if not project_ids:
        return ""
    from ..repositories.world_book_repo import ProjectWorldBookRepo
    from ..session.fetchers.fetch_world_book import render_full
    node_ids = list(perms.get("authorized_node_ids") or [])
    include_events = bool(perms.get("is_management_unit"))
    parts: list[str] = []
    for wb in ProjectWorldBookRepo.get_by_projects(project_ids):
        text = render_full(wb.content_json, node_ids, include_events=include_events)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _apply_query(text: str, query: str) -> tuple[str, int]:
    """关键词过滤（大小写不敏感，按行保留命中行）。query 为空则返回全文。"""
    if not query:
        return text, len([ln for ln in text.splitlines() if ln.strip()])
    q = query.lower()
    hits = [ln for ln in text.splitlines() if q in ln.lower()]
    return "\n".join(hits), len(hits)
