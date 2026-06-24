"""RagProvider 抽象基类与数据模型。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class SearchResult:
    """单条检索结果。"""
    content: str
    score: float = 0.0
    source_document: str = ""
    source_kb: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class RagSearchResponse:
    """RAG 检索响应。"""
    query: str
    results: list[SearchResult]
    context_text: str
    total: int
    provider_name: str


class RagProvider(ABC):
    """知识库检索提供者抽象基类。

    所有 RAG 实现（MaxKB / 本地关键词）必须实现此接口。
    """

    @abstractmethod
    async def search(
        self, query: str, top_k: int = 5,
        stage: str | None = None,
        role: str | None = None,
    ) -> RagSearchResponse:
        """检索知识库。

        Args:
            query: 自然语言查询
            top_k: 最大返回结果数
            stage: 可选，按项目阶段过滤（如'投资决策'、'施工建设'等）
            role: 可选，按岗位过滤（如'工程部经理'、'设计部经理'等）

        Returns:
            RagSearchResponse 包含检索结果和 LLM 可用的格式化文本
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """检查知识库是否可用。

        Returns:
            True 如果知识库可以响应查询
        """
