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
    "搜索知识库获取项目相关的领域知识。"
    "适用场景：查询规范标准、施工工艺、政策法规、项目文档等。"
    "参数 query 为自然语言查询，top_k 控制返回条数（默认 5，最大 10）。"
    "可选参数 stage 按项目阶段过滤（如'投资决策'、'规划报建'、'施工建设'、'竣工验收'等），"
    "可选参数 role 按岗位过滤（如'工程部经理'、'设计部经理'、'规划报建专员'等）。"
)


async def handle_knowledge_search(
    params: dict,
    rag_provider: RagProvider,
) -> dict:
    """处理知识库检索（M14 业务流工具 handler）。

    作为基座能力注册到 BusinessFlowToolRegistry，供所有 SOP 流程调用。
    由 RealExecutor 在框架层直接调用，不走 LLM function calling。

    Args:
        params: LLM 提取的结构化参数 {query, top_k?, stage?, role?}
        rag_provider: RAG 检索提供者（MaxKBRagProvider 或 LocalFileRagProvider）

    Returns:
        dict: {
            success, reply,
            rag_results_data: {query, provider, chunks[{content, score, doc_name}], hit_count, elapsed_ms}
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

    t_start = _time.monotonic()
    try:
        response = await rag_provider.search(
            query, top_k=top_k,
            stage=stage, role=role,
        )
        elapsed_ms = int((_time.monotonic() - t_start) * 1000)

        chunks = [
            {
                "content": r.content,
                "score": r.score,
                "doc_name": r.source_document,
            }
            for r in response.results
        ]

        reply = response.context_text or "未找到相关知识"

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
        return {"success": False, "reply": f"知识库检索失败: {e}"}
