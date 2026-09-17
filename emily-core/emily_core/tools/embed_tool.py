"""embed_and_index 工具 —— 文本批量 embedding + 入 pgvector。

薄壳：实现与留痕都在 `services/knowledge_service.KnowledgeService`（方案 A 同源同痕），
本模块只负责把 LLM 工具入参转成 service 调用，签名与返回契约保持不变。

输入 chunks[]，调当前 embedding 后端（本地 TEI / 远程 API，选型见
infrastructure/embedding/factory.py）生成向量，写入 knowledge_chunks 表。
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.embedding.base import EmbeddingClient
    from ..repositories.knowledge_chunk_repo import KnowledgeChunkRepo

logger = logging.getLogger("emily.tool.embed")

_EMBED_SCHEMA = {
    "type": "object",
    "properties": {
        "chunks": {
            "type": "array",
            "items": {"type": "object"},
            "description": "待入库的 chunk 列表，每个含 text（必填）和 index（可选）",
        },
        "doc_metadata": {
            "type": "object",
            "description": "文档级元数据 {doc_id?, doc_name?, stage?, role?, doc_type?, ...}",
        },
    },
    "required": ["chunks"],
}

_EMBED_DESCRIPTION = (
    "对文本 chunks 做 BGE-m3 embedding 并写入 pgvector 知识库。"
    "供 document → chunk_text → embed_and_index 流水线的最后一环使用。"
)


async def handle_embed_and_index(
    params: dict,
    tei: "EmbeddingClient",
    repo: "KnowledgeChunkRepo",
) -> dict:
    """M14 handler：embedding + 入 pgvector（委托 KnowledgeService）。

    Args:
        params: {chunks[{text, index?}], doc_metadata?}
        tei: EmbeddingClient 实例（本地 TEI / 远程 API / auto 组合）。
        repo: KnowledgeChunkRepo 实例。
    Returns:
        {success, indexed_ids[], count, doc_id, elapsed_ms}
    """
    from ..services.knowledge_service import KnowledgeService

    return await KnowledgeService(tei=tei, repo=repo).index_chunks(
        params.get("chunks", []) or [],
        params.get("doc_metadata", {}) or {},
    )


# ── rag_remove_document 工具 —— 文档级出库（删该文档全部向量分块）──

_RAG_REMOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "库内文件编号（如 FIL-20260709-0001）；与 doc_id 二选一",
        },
        "doc_id": {
            "type": "string",
            "description": "库内文档 ID（files.id，UUID）；与 file_no 二选一",
        },
    },
}

_RAG_REMOVE_DESCRIPTION = (
    "把一份文件从知识库（pgvector）出库：删除该文档的全部向量分块，检索随即不再命中。\n"
    "\n"
    "必填字段：\n"
    "  file_no 或 doc_id — 二选一，指向库内文件\n"
    "\n"
    "授权口径：仅 L5/L6 管理员可执行（fail-closed）；越权调用被拒且留痕。"
)


async def handle_rag_remove_document(
    params: dict,
    knowledge_service=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """RAG 出库（文档级删分块）—— 授权在此判定，动作与留痕委托 KnowledgeService。"""
    file_no = (params.get("file_no") or "").strip()
    doc_id = (params.get("doc_id") or "").strip()
    if not file_no and not doc_id:
        return {"success": False, "reply": "请提供文件编号 (file_no) 或文档 ID (doc_id)",
                "error_code": "missing_doc"}

    # 授权：仅 L5/L6；无操作人信息或取不到等级一律拒绝（fail-closed）
    operator_level = 0
    if user_id:
        try:
            from ..repositories.permission_repo import PermissionRepository

            operator = PermissionRepository.get_user(user_id)
            operator_level = getattr(operator, "level", 0) or 0
        except Exception:
            operator_level = 0
    if operator_level < 5:
        return {"success": False, "reply": "仅 L5/L6 管理员可执行知识库出库",
                "error_code": "permission_denied"}

    if knowledge_service is None:
        return {"success": False, "reply": "知识库服务未就绪", "error_code": "service_unavailable"}

    if not doc_id:
        try:
            from ..repositories.file_repo import FileRepository

            file_record = FileRepository.get_by_file_no(file_no)
        except Exception:
            file_record = None
        if file_record is None:
            return {"success": False, "reply": f"找不到文件编号 {file_no}",
                    "error_code": "file_not_found"}
        doc_id = str(file_record.id)

    res = await knowledge_service.remove_document(doc_id, operator_id=user_id or "")
    if not res.get("success"):
        return {"success": False, "reply": res.get("error", "出库失败"),
                "error_code": "rag_remove_failed"}

    deleted = int(res.get("deleted", 0) or 0)
    return {
        "success": True,
        "reply": f"已从知识库出库（删除 {deleted} 个向量分块）",
        "data": {"doc_id": res.get("doc_id", doc_id), "deleted": deleted},
    }
