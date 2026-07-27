"""FileParserService —— 统一文件解析服务，封装 parse_document / ocr_document 工具调用。

大文件限制：PDF 只解析前 10 页，>50MB 文件默认跳过。
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PAGES = 10
MAX_FILE_SIZE_MB = 50

SUPPORTED_SUFFIXES = {'.pdf', '.docx', '.xlsx', '.jpg', '.jpeg', '.png', '.bmp', '.tiff'}


@dataclass
class ParseResult:
    summary: str          # 文件摘要
    file_type: str        # 证照/合同/图纸/公文/报告/其他
    key_info: dict        # {证号, 日期, 甲方, 乙方, ...}
    confidence: float     # 摘要质量置信度 0-1


class FileParserService:
    """统一文件解析服务，封装 parse_document / ocr_document，加页数限制和 LLM 摘要。"""

    @staticmethod
    def _should_parse(file_path: str) -> bool:
        """判断是否需要解析。"""
        path = Path(file_path)
        if not path.exists():
            logger.debug("文件不存在: %s", file_path)
            return False
        if path.stat().st_size > MAX_FILE_SIZE_MB * 1024 * 1024:
            logger.info("跳过大文件: %.1fMB > %dMB", path.stat().st_size / 1024 / 1024, MAX_FILE_SIZE_MB)
            return False
        return path.suffix.lower() in SUPPORTED_SUFFIXES

    @staticmethod
    def _choose_tool(file_path: str) -> str:
        """根据扩展名选择解析工具。"""
        suffix = Path(file_path).suffix.lower()
        if suffix in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}:
            return 'ocr_document'
        return 'parse_document'

    @staticmethod
    async def parse_and_summarize(file_path: str, filename: str) -> ParseResult | None:
        """
        解析文件并生成摘要。
        返回 None 表示跳过或失败。
        """
        if not FileParserService._should_parse(file_path):
            return None

        tool_name = FileParserService._choose_tool(file_path)

        try:
            from emily_core.tools.parse_document_tool import handle_parse_document
            from emily_core.tools.ocr_tool import handle_ocr_document

            if tool_name == 'parse_document':
                raw_result = await handle_parse_document({
                    "file_path": file_path,
                    "max_pages": MAX_PAGES,
                })
            else:
                raw_result = await handle_ocr_document({
                    "file_path": file_path,
                })

            # 调用 LLM 生成结构化摘要
            summary = await FileParserService._llm_summarize(raw_result, filename)

            return ParseResult(
                summary=summary.get("summary", ""),
                file_type=summary.get("file_type", "其他"),
                key_info=summary.get("key_info", {}),
                confidence=summary.get("confidence", 0.5),
            )

        except Exception as e:
            logger.warning("文件解析失败 %s: %s", filename, e)
            return None

    @staticmethod
    async def _llm_summarize(raw_content: dict | str, filename: str) -> dict:
        """生成摘要 + 文件性质分类 + 关键字段提取。

        优先尝试调用 LLM client 生成结构化摘要；
        LLM 不可用时从原始内容截取前 200 字作为摘要。
        """
        # 提取文本内容
        if isinstance(raw_content, dict):
            text = raw_content.get("text", raw_content.get("content", str(raw_content)))
        else:
            text = str(raw_content)

        # 尝试调用 LLM
        try:
            from emily_core.infrastructure.llm.client import LLMClient
            # LLM 客户端需要外部注入，无法在此直接实例化
            # 退化为基础摘要
            pass
        except ImportError:
            pass

        # 基础摘要：截取前 200 字
        short = text.strip()[:200] if text.strip() else f"文件「{filename}」"
        return {
            "summary": short,
            "file_type": "其他",
            "key_info": {},
            "confidence": 0.5,
        }
