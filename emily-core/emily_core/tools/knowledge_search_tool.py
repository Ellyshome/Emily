"""knowledge_search 工具 —— 搜索知识库获取领域知识。

工厂函数 create_knowledge_search_tool(rag_provider) 返回 ToolDefinition，
由 tools/__init__.py 的 create_all_tools() 注册到 ToolRegistry。

M10 扩展: 新增 stage 和 role 可选过滤参数，metadata 增强检索精度。
"""

import logging

from ..providers.rag.base import RagProvider
from ..agent.tool_registry import ToolDefinition

logger = logging.getLogger("emily.tool.knowledge_search")


def create_knowledge_search_tool(rag_provider: RagProvider) -> ToolDefinition:
    """创建 knowledge_search 工具的 ToolDefinition。

    Args:
        rag_provider: RAG 检索提供者（MaxKBRagProvider 或 LocalFileRagProvider）

    Returns:
        ToolDefinition 供 ToolRegistry.register() 注册
    """

    async def execute(args: dict) -> dict:
        query = args.get("query", "").strip()
        if not query:
            return {"success": False, "error": "请提供查询关键词 query"}

        top_k = min(int(args.get("top_k", 5)), 10)
        stage = args.get("stage", None)   # M10: 按阶段过滤
        role = args.get("role", None)     # M10: 按岗位过滤

        if not await rag_provider.is_available():
            return {
                "success": False,
                "error": "知识库暂不可用，请稍后重试",
            }

        try:
            response = await rag_provider.search(
                query, top_k=top_k,
                stage=stage, role=role,
            )
            return {
                "success": True,
                "query": response.query,
                "total": response.total,
                "context_text": response.context_text,
                "results": [
                    {
                        "content": r.content,
                        "score": r.score,
                        "source_document": r.source_document,
                        "stage": r.metadata.get("stage", ""),
                        "chunk_id": r.metadata.get("chunk_id", ""),
                        "keywords": r.metadata.get("keywords", []),
                    }
                    for r in response.results
                ],
            }
        except Exception as e:
            logger.error("knowledge_search 失败: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    return ToolDefinition(
        name="knowledge_search",
        description=(
            "搜索知识库获取项目相关的领域知识。"
            "适用场景：查询规范标准、施工工艺、政策法规、项目文档等。"
            "参数 query 为自然语言查询，top_k 控制返回条数（默认 5，最大 10）。"
            "可选参数 stage 按项目阶段过滤（如'投资决策'、'规划报建'、'施工建设'、'竣工验收'等），"
            "可选参数 role 按岗位过滤（如'工程部经理'、'设计部经理'、'规划报建专员'等）。"
        ),
        parameters={
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
        },
        execute=execute,
    )
