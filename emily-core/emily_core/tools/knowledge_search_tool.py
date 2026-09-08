"""knowledge_search 工具 —— 搜索知识库获取领域知识。

M14: 核心逻辑提取为独立 handler (handle_knowledge_search)，
    注册到 BusinessFlowToolRegistry，由 SOP 引导 Agent 在需要时调用。
    ToolRegistry 已移除，不再需要 LLM ToolDefinition 包装器。

M10 扩展: 新增 stage 和 role 可选过滤参数，metadata 增强检索精度。
"""

import logging
import time as _time

from ..providers.rag.base import RagProvider

logger = logging.getLogger("emily.tool.knowledge_search")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler + schema + description —— knowledge_search（基座能力）
# ══════════════════════════════════════════════════════════════════════════════

_KNOWLEDGE_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "自然语言检索关键词或问题",
        },
        "top_k": {
            "type": "integer",
            "description": "返回结果条数，默认 5，最大 10",
        },
        "stage": {
            "type": "string",
            "description": (
                "按项目阶段过滤，可选：投资决策、专项审查、规划报建、"
                "招标采购、施工建设、竣工验收、交付售后"
            ),
        },
        "role": {
            "type": "string",
            "description": (
                "按岗位过滤，如'工程部经理'、'设计部经理'、"
                "'规划报建专员'、'成本部经理'等"
            ),
        },
    },
    "required": ["query"],
}

_KNOWLEDGE_SEARCH_DESCRIPTION = (
    "搜索知识库获取领域知识与公司制度。"
    "知识库包含两类来源：①项目资料（规范标准、施工工艺、政策法规、项目文档等）；"
    "②公司规章制度（管理办法、管理制度、操作规程等）。"
    "参数 query 为自然语言查询，top_k 控制返回条数（默认 5，最大 10）。"
    "可选参数 stage 按项目阶段过滤（如'投资决策'、'规划报建'、'施工建设'、'竣工验收'等），"
    "可选参数 role 按岗位过滤（如'工程部经理'、'设计部经理'、'规划报建专员'等）。"
)


async def handle_knowledge_search(
    params: dict,
    rag_provider: RagProvider,
    user_id: str | None = None,
    resolver=None,
) -> dict:
    """处理知识库检索（M14 业务流工具 handler）。

    作为基座能力注册到 BusinessFlowToolRegistry，供所有 SOP 流程调用。
    由 RealExecutor 在框架层直接调用，不走 LLM function calling。

    Args:
        params: LLM 提取的结构化参数 {query, top_k?, stage?, role?}
        rag_provider: RAG 检索提供者（PgVectorRagProvider 或 LocalFileRagProvider）
        user_id: 可选当前用户 id（M4，由框架签名注入）
        resolver: 可选 VisibleFileSetResolver（M4，由 registry._rag 注入）

    Returns:
        dict: {
            success, reply,
            rag_results_data: {query, provider, chunks[{content, score, doc_name, cite_id, title}], hit_count, elapsed_ms}
        }
    """
    query = params.get("query", "").strip()
    if not query:
        return {"success": False, "reply": "请提供查询关键词 query"}

    top_k = min(int(params.get("top_k", 5)), 10)
    stage = params.get("stage", None)
    role = params.get("role", None)

    # 检查知识库可用性
    if not await rag_provider.is_available():
        return {
            "success": False,
            "reply": "知识库暂不可用，请稍后重试",
        }

    # M4: 解析当前用户可见文件集（可见性唯一权威来源），宁少答不泄露
    scoped_doc_ids = None
    if user_id and resolver is not None:
        scoped_doc_ids = await _resolve_scoped_doc_ids(user_id, resolver)

    t_start = _time.monotonic()
    try:
        response = await rag_provider.search(
            query, top_k=top_k,
            stage=stage, role=role,
            scoped_doc_ids=scoped_doc_ids,
        )
        elapsed_ms = int((_time.monotonic() - t_start) * 1000)

        chunks = [
            {
                "content": r.content,
                "score": r.score,
                "doc_name": r.source_document,
                "cite_id": getattr(r, "source_file_id", "") or "",
                "title": getattr(r, "source_title", "") or r.source_document,
            }
            for r in response.results
        ]

        # RAG 检索日志（供 evolution 的 rag_retrieval_logs 统计消费，写入失败不影响主流程）
        try:
            from emily_core.infrastructure.logging.rag_logger import RAGRetrievalLogger
            scores = [r.score for r in response.results]
            await RAGRetrievalLogger.log(
                query_text=response.query or query,
                provider=response.provider_name,
                hit_count=response.total,
                top_score=max(scores) if scores else 0.0,
                avg_score=(sum(scores) / len(scores)) if scores else 0.0,
                latency_ms=elapsed_ms,
                was_used_by_llm=True,
            )
        except Exception as log_err:
            logger.warning("RAG retrieval log write failed: %s", log_err)

        reply = response.context_text or "未找到相关知识"

        # M8: 引用溯源 —— 在回复尾部附 [cite:doc_id] 标记
        citations = []
        for r in response.results:
            cite_id = getattr(r, "source_file_id", "")
            if cite_id:
                title = getattr(r, "source_title", "") or r.source_document
                citations.append(f"[cite:{cite_id}] {title}")
        if citations:
            reply = reply + "\n\n引用来源：\n" + "\n".join(citations)

        return {
            "success": True,
            "query": response.query,
            "total": response.total,
            "reply": reply,
            "rag_results_data": {
                "query": response.query,
                "provider": response.provider_name,
                "chunks": chunks,
                "hit_count": response.total,
                "elapsed_ms": elapsed_ms,
            },
        }
    except Exception as e:
        logger.error("knowledge_search handler failed: %s", e, exc_info=True)
        # 失败检索也记日志（error_summary），供 evolution 统计零命中/异常
        try:
            from emily_core.infrastructure.logging.rag_logger import RAGRetrievalLogger
            await RAGRetrievalLogger.log(
                query_text=query,
                provider=getattr(rag_provider, "provider_name", "unknown"),
                hit_count=0,
                error_summary=str(e)[:500],
                was_used_by_llm=True,
            )
        except Exception as log_err:
            logger.warning("RAG retrieval error log write failed: %s", log_err)
        return {"success": False, "reply": f"知识库检索失败: {e}"}


async def _resolve_scoped_doc_ids(user_id: str, resolver) -> "object":
    """解析当前用户可见文件 id 集合（返回 SQLAlchemy Select，惰性）。

    通过 PermissionService.build_permission_dict 取 company_id / info_level，
    再交给 VisibleFileSetResolver 实时计算 ①∪②∪③∪④。
    """
    import asyncio
    from ..services.permission_service import PermissionService

    def _build_perm() -> dict:
        return PermissionService().build_permission_dict(user_id)

    perm = await asyncio.to_thread(_build_perm)
    company_id = perm.get("company_id", "")
    info_level = perm.get("info_level", "public")
    return resolver.resolve_visible_file_ids(
        user_id, company_id=company_id, info_level=info_level,
    )
