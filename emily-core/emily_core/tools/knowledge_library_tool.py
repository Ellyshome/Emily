"""knowledge_library_tool —— 资料库查阅 / 入库工具（进程内 LLM 工具）。

查阅（M5，只读，L3 门槛）：`read_knowledge_library`
  · action=list       列出目录下条目（缺省资料库首层，不含索引文件自身）
  · action=read_index 读取该目录的索引内容（逐级下钻的唯一入口）
  · action=read_file  读取索引里列出的资料文件（先校验同目录索引存在 —— 约束 12）

入库（M4b，写索引，L5 门槛）：`ingest_knowledge_library`
  · 薄壳委派入库引擎（M2），与脚本通道（M4a）共享同一服务层，互不调用（C9）。

分层（C2 / C6）：本层为 async 壳，文件访问与入库核心为 sync，统一用
`asyncio.to_thread()` 包裹 `KnowledgeLibraryFs` / 入库引擎的调用，不跨层。
取数一律经 services/knowledge_library（M1/M2），本文件不含目录遍历逻辑。

查阅通道**不依赖**既有 RAG 向量检索（US-07 / AC-US-04.1）：本文件不 import 任何
检索 provider；资料库目录也不在 RAG 扫描目录内。
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("emily.tool.knowledge_library")

# 查阅 / 入库的门槛（线性"不低于"语义 —— PRD 约束 6；不查询则用树形继承）
READ_MIN_LEVEL = 3
INGEST_MIN_LEVEL = 5

_KNOWLEDGE_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "read_index", "read_file"],
            "description": (
                "list = 列出某目录下的子目录与文件（缺省 = 资料库首层，用于回答「有哪些资料」）；"
                "read_index = 读取某目录的索引内容（**下钻的唯一入口**，先读它再决定进哪一层）；"
                "read_file = 读取索引里列出的某个资料文件正文（仅文本类文件可读）"
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "资料库内的相对路径，**不带**资料库根目录名（如 'H_Project' 或 "
                "'H_Project/黄湖/合同类'）。list / read_index 传目录，read_file 传文件；"
                "留空 = 资料库根目录"
            ),
        },
    },
    "required": ["action"],
}

_KNOWLEDGE_READ_DESCRIPTION = (
    "查阅资料库（项目档案资料的结构化查阅通道，只读）。按「先读索引 → 再逐级下钻 → 命中后读文件」"
    "查阅，**不使用**向量检索：\n"
    "  · action=\"list\"：列出目录条目（子目录 / 文件、大小、修改时间）。回答「资料库里有哪些项目/资料」"
    "先用它，缺省即首层\n"
    "  · action=\"read_index\"：读取该目录的索引内容 —— 索引含该目录的简介、文件目录（每个子目录与"
    "文件各带一句话描述）。**每一层的下钻都应先读索引**，按索引里的线索决定进哪个子目录或读哪个文件\n"
    "  · action=\"read_file\"：读取索引里列出的资料文件正文（仅文本类：md/txt/csv/json 等）。"
    "图片、图纸、压缩包、PDF/Word 等不可解析类型会如实说明，**不会**编造内容\n"
    "作业纪律：① 目录缺索引 = 未入库，读文件前会校验同目录索引存在，缺失则提示先入库；"
    "② 路径越出资料库范围一律拒绝；③ 索引可能过期时会附带提示，但仍照常返回内容；"
    "④ 查不到就如实说没有，不要臆造资料内容。\n"
    "权限：参建管理（L3）及以上可查阅。"
)

_KNOWLEDGE_INGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "dir": {
            "type": "string",
            "description": (
                "目标目录（资料库内的相对路径，如 'H_Project' 或 'H_Project/黄湖'；"
                "留空 = 整个资料库）。入库会为其**每一层子目录**各生成一份索引"
            ),
        },
        "dry_run": {
            "type": "boolean",
            "description": "true = 仅预览将生成/更新的索引清单，不写任何文件（默认 false）",
        },
    },
    "required": ["dir"],
}

_KNOWLEDGE_INGEST_DESCRIPTION = (
    "资料库入库（写操作，需管理员 L5 及以上）：把指定目录纳入资料库管理 —— 一次性遍历该目录树，"
    "为其每一层目录生成/更新索引文件（幂等覆盖，不改动任何原始资料）。\n"
    "入库是**人工/对话确认后的动作**：先用 dry_run=true 预览将生成/更新的索引清单并向用户确认，"
    "再以 dry_run=false 实际入库。\n"
    "预检提示：入库不会解析资料内容（图片/图纸/压缩包按名称推断），大目录（上千文件）耗时较长。"
)


# ══════════════════════════════════════════════════════════════════════════════
#  查阅（M5）
# ══════════════════════════════════════════════════════════════════════════════

def _relay_denied(denied: dict) -> dict:
    """越权拒绝：在门禁文案后附交代口径（不改动共享的 check_min_level 语义）。

    目的：被拒后如实转述所需等级，不要改用其他工具绕道取数（AC-US-03.2 / 06.2）。
    """
    reply = str(denied.get("reply") or "").rstrip("。")
    return {
        **denied,
        "reply": f"{reply}。请把所需权限等级如实告知用户，不要改用其他工具获取资料库内容。",
    }


async def handle_read_knowledge_library(params: dict, user_id: str = "") -> dict:
    """资料库查阅（list / read_index / read_file）。只读、幂等、失败不阻断。"""
    from ..services.knowledge_library import (
        KnowledgeLibraryError,
        KnowledgeLibraryNotFoundError,
        KnowledgeLibraryPathError,
        KnowledgeLibraryTypeError,
    )
    from .file_tool import check_min_level

    denied = check_min_level(user_id, READ_MIN_LEVEL, "资料库查阅")
    if denied is not None:
        return _relay_denied(denied)

    params = params or {}
    action = str(params.get("action") or "").strip()
    path = str(params.get("path") or "").strip()
    if action not in ("list", "read_index", "read_file"):
        return {"success": False, "error_code": "bad_action",
                "reply": f"不支持的操作类型：{action or '（空）'}（支持 list / read_index / read_file）"}

    try:
        if action == "list":
            return await asyncio.to_thread(_do_list, path)
        if action == "read_index":
            return await asyncio.to_thread(_do_read_index, path)
        return await asyncio.to_thread(_do_read_file, path)
    except KnowledgeLibraryPathError as e:
        # 越界一律拒绝（US-04.9 / 约束 13）：转为用户可读拒绝语，不冒泡
        return {"success": False, "error_code": "path_out_of_range", "path": path,
                "reply": f"已拒绝越界访问：{e}。只能查阅资料库范围内的内容。"}
    except KnowledgeLibraryNotFoundError as e:
        return {"success": False, "error_code": "not_found", "path": path,
                "reply": f"{e}。可先用 action=\"list\" 查看可用的目录。"}
    except KnowledgeLibraryTypeError as e:
        return {"success": False, "error_code": "unsupported_type", "path": path,
                "reply": (f"该文件不可按文本读取：{e}。"
                          f"请按索引中的文件名与描述定位，或由人工打开原件。")}
    except KnowledgeLibraryError as e:
        return {"success": False, "error_code": "unavailable", "path": path,
                "reply": f"资料库不可达或读取失败：{e}"}
    except Exception as e:  # noqa: BLE001 — 如实回报，不阻断对话
        logger.error("read_knowledge_library failed action=%s path=%s: %s", action, path, e, exc_info=True)
        return {"success": False, "error_code": "read_failed", "reply": f"资料库查阅失败：{e}"}


def _fs():
    from ..services.knowledge_library import KnowledgeLibraryFs

    return KnowledgeLibraryFs()


def _do_list(path: str) -> dict:
    """action=list：列出目录条目（不含索引文件自身）。"""
    fs = _fs()
    where = path or "资料库根目录"
    entries = fs.list_entries(path)

    dirs = [e for e in entries if e["kind"] == "dir"]
    files = [e for e in entries if e["kind"] == "file"]
    indexed = fs.index_exists(path)

    if not entries:
        head = f"「{where}」下没有子目录或文件。"
    else:
        lines = [
            f"- {e['name']}/  （目录）" if e["kind"] == "dir"
            else f"- {e['name']}  （文件 · {e['size']} 字节 · {e['mtime']}）"
            for e in entries
        ]
        head = (f"「{where}」下共 {len(entries)} 项（子目录 {len(dirs)} 个、文件 {len(files)} 个）：\n"
                + "\n".join(lines))
    hints: list[str] = []
    if path and not indexed:
        hints.append("该目录尚无索引（未入库），可查阅其父目录索引或请管理员入库后查阅。")
    elif entries:
        hints.append("下一步：对感兴趣的目录用 action=\"read_index\" 读取其索引，按索引线索继续下钻。")
    return {"success": True, "action": "list", "path": path, "entries": entries,
            "count": len(entries), "indexed": bool(indexed),
            "reply": head + (("\n\n" + "\n".join(hints)) if hints else "")}


def _do_read_index(path: str) -> dict:
    """action=read_index：读取该目录索引内容（含时效提示）。"""
    fs = _fs()
    where = path or "资料库根目录"
    if not fs.resolve(path).exists():
        return {"success": False, "error_code": "not_found", "path": path,
                "reply": f"资料库中不存在「{path}」。可先用 action=\"list\" 查看可用的目录。"}

    text = fs.read_index(path)
    if not text:
        return {"success": False, "error_code": "index_missing", "path": path,
                "reply": (f"「{where}」未入库（该目录没有索引文件），无法读取索引。"
                          f"可先用 action=\"list\" 查看其上级目录，或请管理员执行资料入库。")}

    stale = _is_stale(fs, path)
    suffix = ("\n\n（提示：该目录内容晚于索引生成时间，索引可能已过期，建议重新入库；"
              "以下内容仍可参考。）" if stale else "")
    return {"success": True, "action": "read_index", "path": path, "stale": stale,
            "content": text, "reply": f"「{where}」索引内容：\n{text}{suffix}"}


def _do_read_file(path: str) -> dict:
    """action=read_file：先校验同目录索引存在，再读文本文件（约束 12）。"""
    fs = _fs()
    if not path:
        return {"success": False, "error_code": "missing_path", "reply": "请提供要读取的文件路径（path）。"}

    # 强制作业顺序：同目录索引存在才可读文件（R1 / 约束 12）
    if not fs.index_exists(path):
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return {"success": False, "error_code": "index_missing", "path": path,
                "reply": (f"「{parent or '资料库根目录'}」未入库（缺索引），不能直接读取文件。"
                          f"请先由管理员对该目录执行入库，或查阅索引后改取其列出的其他文件。")}

    text = fs.read_text_file(path)
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    stale = _is_stale(fs, parent)
    suffix = "\n\n（提示：所在目录内容晚于索引生成时间，索引可能已过期。）" if stale else ""
    return {"success": True, "action": "read_file", "path": path, "stale": stale,
            "content": text, "reply": f"「{path}」内容：\n{text}{suffix}"}


def _is_stale(fs, rel: str) -> bool:
    """索引是否可能过期（US-09.2）：目录内容最新修改时间晚于索引生成时间。"""
    try:
        meta = fs.dir_meta(rel)
    except Exception:  # noqa: BLE001 — 时效提示失败不影响查阅
        return False
    index_mtime = meta.get("index_mtime")
    newest = meta.get("newest_content_mtime")
    if not index_mtime or not newest:
        return False
    return str(newest) > str(index_mtime)


# ══════════════════════════════════════════════════════════════════════════════
#  入库（M4b，薄壳委派 M2）
# ══════════════════════════════════════════════════════════════════════════════

async def handle_ingest_knowledge_library(params: dict, user_id: str = "") -> dict:
    """资料库入库（L5 门槛）：薄壳委派 KnowledgeLibraryIngestService（M2）。"""
    from .file_tool import check_min_level

    denied = check_min_level(user_id, INGEST_MIN_LEVEL, "资料入库")
    if denied is not None:
        return _relay_denied(denied)

    params = params or {}
    rel = str(params.get("dir") or "").strip()
    dry_run = bool(params.get("dry_run", False))

    def _run() -> dict:
        from ..services.knowledge_library import KnowledgeLibraryFs
        from ..services.knowledge_library.ingest_service import KnowledgeLibraryIngestService

        svc = KnowledgeLibraryIngestService(KnowledgeLibraryFs())
        return svc.ingest(rel, dry_run=dry_run, operator_id=user_id)

    result = await asyncio.to_thread(_run)
    if not result.get("ok"):
        return {"success": False, "error_code": result.get("error_code", "ingest_failed"),
                "reply": f"资料入库失败：{result.get('error') or '未知原因'}"}

    stats = result.get("stats") or {}
    if dry_run:
        lines = [f"- {p}" for p in (result.get("created") or []) + (result.get("updated") or [])]
        reply = (f"预览完成（未写任何文件）：目标「{result.get('target')}」，"
                 f"将生成 {stats.get('created', 0)} 份、更新 {stats.get('updated', 0)} 份索引，"
                 f"共 {stats.get('dirs', 0)} 层目录、{stats.get('files', 0)} 个文件。\n"
                 + ("\n".join(lines) if lines else ""))
        return {"success": True, "dry_run": True, "data": result, "reply": reply}

    reply = (f"入库完成：目标「{result.get('target')}」，"
             f"新建 {stats.get('created', 0)} 份、更新 {stats.get('updated', 0)} 份索引，"
             f"共 {stats.get('dirs', 0)} 层目录、{stats.get('files', 0)} 个文件"
             f"（{stats.get('elapsed_ms', 0)} ms）。原始资料未被改动。")
    if result.get("skipped"):
        reply += f"\n跳过的目录 {len(result['skipped'])} 个：{'、'.join(result['skipped'])}"
    return {"success": True, "dry_run": False, "data": result, "reply": reply}
