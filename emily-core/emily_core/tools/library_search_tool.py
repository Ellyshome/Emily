"""library_search_tool —— 资料库关键词检索（只读，L3+）。

定位：`read_knowledge_library` 是“逐级读索引再下钻”，适合已知目录/文件；
本工具补一个**按关键词扫备注/说明**的入口，解决“查做法、查考察记录、查经验”
这类不知道落在哪个目录、也不该走向量 RAG 的问题。

边界：
  · 只扫资料库内的 .md/.txt 文本（跳过 .emily.md 索引与二进制/Office/PDF）；
  · 结果给出路径、标题、命中片段和同目录参考图，供后续 read_library_file /
    send_library_file 使用；
  · 不做向量检索、不读文件正文之外的格式、不越出资料库根目录。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger("emily.tool.library_search")

_MIN_LEVEL = 3
_MAX_LIMIT = 10
_TEXT_EXTS = {".md", ".txt", ".markdown", ".text"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
_INDEX_NAME = ".emily.md"

_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "关键词或自然语言问题，如“狭窄巷道雨水排水做法”“装饰井盖 点位 高程”",
        },
        "path": {
            "type": "string",
            "description": "可选，限定检索目录（资料库内相对路径，如 '节点做法' 或 'H_Project/萧山宁牧鸿瑞府/02_材料与做法'）；留空 = 全库",
        },
        "limit": {
            "type": "integer",
            "description": "最多返回条数（默认 5，最大 10）",
        },
    },
    "required": ["query"],
}

_DESCRIPTION = (
    "在资料库内按关键词检索备注/说明文件（只读，L3+）。用于“有没有类似做法/经验/考察记录/材料案例”"
    "这类不确定落在哪个目录的问题：返回命中文件路径、标题、片段和同目录参考图，"
    "随后可用 read_library_file 读正文、send_library_file 发图。"
    "只扫 .md/.txt 文本，不做向量检索。"
)


def _terms(query: str) -> list[str]:
    """把查询拆成可匹配的词/中文 n-gram。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    segments = [s for s in re.split(r"[\s，。；：、,.;:!?？!\"'“”‘’（）()\[\]【】]+", q) if s]
    terms: list[str] = []
    for seg in segments:
        if len(seg) <= 10:
            terms.append(seg)
        # 中文长句：补 2–4 字 n-gram，保证“狭窄巷道/雨水排水/做法”能命中
        if re.search(r"[\u4e00-\u9fff]", seg):
            for n in (2, 3, 4):
                for i in range(0, max(0, len(seg) - n + 1)):
                    part = seg[i:i + n]
                    if len(part) == n:
                        terms.append(part)
        # 英文/数字词
        terms.extend(re.findall(r"[a-z0-9_]{2,}", seg))
    # 去重、去掉纯停用词/单字
    stop = {"什么", "怎么", "如何", "有没有", "之前", "这个", "那个", "我们", "项目", "景观"}
    out: list[str] = []
    seen: set[str] = set()
    for t in terms:
        t = t.strip()
        if len(t) < 2 or t in stop or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _score(text: str, title: str, filename: str, terms: list[str]) -> tuple[float, str]:
    lower = (text or "").lower()
    title_l = (title or "").lower()
    name_l = (filename or "").lower()
    score = 0.0
    first_pos = -1
    for t in terms:
        c = lower.count(t)
        if c:
            score += min(c, 3) * 1.0
            pos = lower.find(t)
            if first_pos < 0 or 0 <= pos < first_pos:
                first_pos = pos
        if t in title_l:
            score += 5.0
        if t in name_l:
            score += 4.0
    # 多词同时命中加分，避免单个高频字词主导
    hit_terms = sum(1 for t in terms if t in lower or t in title_l or t in name_l)
    if hit_terms >= 3:
        score += hit_terms * 0.5
    snippet = ""
    if first_pos >= 0:
        start = max(0, first_pos - 60)
        end = min(len(text), first_pos + 140)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
    return score, snippet


def _title(text: str, fallback: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("# ").strip() or fallback
    return fallback


def _related_images(directory: Path) -> list[str]:
    try:
        names = sorted(
            p.name for p in directory.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTS
        )
    except OSError:
        return []
    return names[:3]


def _search(query: str, scope: str, limit: int) -> dict:
    from ..services.knowledge_library import KnowledgeLibraryFs

    fs = KnowledgeLibraryFs()
    root = fs.root
    base = fs.resolve(scope) if scope else root
    if not base.exists():
        return {"success": False, "error_code": "not_found", "reply": f"资料库中不存在「{scope}」。"}
    if base.is_file():
        base = base.parent

    terms = _terms(query)
    if not terms:
        return {"success": False, "error_code": "empty_query", "reply": "请提供要检索的关键词。"}

    hits: list[dict] = []
    for p in base.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in _TEXT_EXTS:
            continue
        if p.name == _INDEX_NAME:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        title = _title(text, p.stem)
        score, snippet = _score(text, title, p.name, terms)
        if score <= 0:
            continue
        hits.append({
            "path": rel,
            "title": title,
            "score": round(score, 2),
            "snippet": snippet,
            "directory": rel.rsplit("/", 1)[0] if "/" in rel else "",
            "related_images": _related_images(p.parent),
        })

    hits.sort(key=lambda h: (-h["score"], h["path"]))
    hits = hits[:limit]
    if not hits:
        return {
            "success": True, "count": 0, "query": query, "scope": scope,
            "reply": f"资料库中没有找到与「{query}」匹配的备注/说明文件。可换个关键词，或先用 read_knowledge_library 列目录。",
        }
    lines = [f"资料库检索「{query}」命中 {len(hits)} 条："]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}（{h['path']}，score={h['score']}）")
        if h["snippet"]:
            lines.append(f"   {h['snippet']}")
        if h["related_images"]:
            lines.append(f"   同目录图片：{', '.join(h['related_images'])}")
    return {
        "success": True,
        "count": len(hits),
        "query": query,
        "scope": scope,
        "hits": hits,
        "reply": "\n".join(lines),
    }


async def handle_library_search(params: dict, user_id: str = "") -> dict:
    from .file_tool import check_min_level

    denied = check_min_level(user_id, _MIN_LEVEL, "资料库检索")
    if denied is not None:
        return denied

    params = params or {}
    query = str(params.get("query") or "").strip()
    scope = str(params.get("path") or "").strip()
    try:
        limit = int(params.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, _MAX_LIMIT))

    try:
        return await asyncio.to_thread(_search, query, scope, limit)
    except Exception as e:  # noqa: BLE001
        logger.error("library_search failed query=%s scope=%s: %s", query, scope, e, exc_info=True)
        return {"success": False, "error_code": "search_failed", "reply": f"资料库检索失败：{e}"}
