"""LocalFileRagProvider —— 本地 TF 关键词搜索（零外部依赖）+ metadata 过滤。

在配置目录内扫描 .md / .txt 文件，按 ## / # 标题分块，
查询时对每块做 TF 词频评分，返回 top_k 个最相关块。

M10 扩展：分块保留 metadata（stage/role/keywords），检索支持 stage/role 过滤。

纯 Python 标准库实现（pathlib + re），无需任何外部依赖。
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from .base import RagProvider, SearchResult, RagSearchResponse

logger = logging.getLogger("emily.rag.local")


# 默认搜索目录（相对于项目根目录）
_DEFAULT_SEARCH_DIR = "项目资料"


class LocalFileRagProvider(RagProvider):
    """本地关键词搜索提供者 + metadata 过滤。

    启动时扫描目录内所有 .md/.txt 文件，按 Markdown 标题分块。
    查询时用 TF（词频）评分，支持 stage/role 过滤。
    """

    def __init__(self, search_dir: str | None = None):
        # rag/ → providers/ → emily_core/ → emily-core/ → Emily/（项目根）
        if search_dir:
            self._search_dir = Path(search_dir)
        else:
            project_root = Path(__file__).resolve().parents[4]
            self._search_dir = project_root / _DEFAULT_SEARCH_DIR
        self._chunks: list[dict] = []
        self._loaded: bool = False

    def _load(self) -> None:
        """扫描目录，加载所有文件并按标题分块，附带 metadata。"""
        if self._loaded:
            return

        if not self._search_dir.exists():
            logger.info("LocalFileRag: 目录不存在 %s", self._search_dir)
            self._loaded = True
            return

        for file_path in self._search_dir.rglob("*"):
            if file_path.suffix.lower() not in (".md", ".txt", ".markdown"):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
            except Exception:
                continue

            source_name = str(file_path.relative_to(self._search_dir))
            # 按 ## 或 # 标题分块
            sections = _split_by_headings(content)
            for title, body in sections:
                if body.strip():
                    # M10: 提取 metadata
                    metadata = _extract_metadata(source_name, title, body)
                    self._chunks.append({
                        "title": title or file_path.name,
                        "body": body.strip(),
                        "source": source_name,
                        **metadata,
                    })

        self._loaded = True
        logger.info(
            "LocalFileRag: 已加载 %d 个文本块 (目录: %s)",
            len(self._chunks), self._search_dir,
        )

    async def is_available(self) -> bool:
        self._load()
        return len(self._chunks) > 0

    async def search(
        self, query: str, top_k: int = 5,
        stage: str | None = None,
        role: str | None = None,
    ) -> RagSearchResponse:
        """TF 词频检索（M10: 支持 stage/role metadata 过滤）。

        Args:
            query: 自然语言查询
            top_k: 最大返回结果数
            stage: 可选，按项目阶段过滤（如'投资决策'、'施工建设'等）
            role: 可选，按岗位过滤（如'工程部经理'、'设计部经理'等）
        """
        self._load()

        if not self._chunks:
            return RagSearchResponse(
                query=query, results=[], context_text="",
                total=0, provider_name="LocalFile (empty)",
            )

        # 分词（简单按中英文边界 + 空格切分）
        tokens = _tokenize(query)
        if not tokens:
            return RagSearchResponse(
                query=query, results=[], context_text="",
                total=0, provider_name="LocalFile (empty query)",
            )

        scored = []
        for chunk in self._chunks:
            # M10: metadata 过滤
            if stage and not _match_stage(chunk, stage):
                continue
            if role and not _match_role(chunk, role):
                continue

            body_lower = chunk["body"].lower()
            score = sum(body_lower.count(t.lower()) for t in tokens)
            if score > 0:
                scored.append((score / max(len(body_lower), 1), chunk))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, chunk in scored[:top_k]:
            results.append(SearchResult(
                content=chunk["body"][:1000],  # 截断长文本
                score=round(score * 100, 2),
                source_document=chunk["source"],
                metadata={
                    "title": chunk.get("title", ""),
                    "stage": chunk.get("stage", ""),
                    "chunk_id": chunk.get("chunk_id", ""),
                    "keywords": chunk.get("keywords", []),
                },
            ))

        parts = []
        for r in results:
            parts.append(f"### {r.source_document}\n{r.content}")
        context_text = "\n\n---\n\n".join(parts)

        return RagSearchResponse(
            query=query,
            results=results,
            context_text=context_text,
            total=len(results),
            provider_name="LocalFile",
        )


def _split_by_headings(content: str) -> list[tuple[str, str]]:
    """按 Markdown 标题（## 或 #）分块。

    Returns:
        [(section_title, section_body), ...]
    """
    sections: list[tuple[str, str]] = []
    # 找到所有标题位置
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(content))

    if not matches:
        # 无标题：整个文件作为一个块
        sections.append(("", content))
        return sections

    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append((m.group(2).strip(), content[start:end]))

    # 标题之前的内容（如果有的话）
    if matches and matches[0].start() > 0:
        sections.insert(0, ("", content[:matches[0].start()]))

    return sections


def _tokenize(text: str) -> list[str]:
    """简单分词：中文字符单独成词，英文按空格切分。"""
    tokens: list[str] = []
    # 中文字符
    chinese = re.findall(r"[一-鿿]", text)
    tokens.extend(chinese)
    # 英文/数字单词
    words = re.findall(r"[a-zA-Z0-9]{2,}", text)
    tokens.extend(words)
    return tokens


# ══════════════════════════════════════════════════════════════════════════════
# M10: Metadata 提取与过滤
# ══════════════════════════════════════════════════════════════════════════════

# 阶段关键词映射（用于从文件名/标题推断阶段）
_STAGE_KEYWORDS: dict[str, str] = {
    "投资决策": "投资决策",
    "投资": "投资决策",
    "专项审查": "专项审查",
    "专项": "专项审查",
    "规划报建": "规划报建",
    "报建": "规划报建",
    "规划": "规划报建",
    "招标采购": "招标采购",
    "招标": "招标采购",
    "采购": "招标采购",
    "施工建设": "施工建设",
    "施工": "施工建设",
    "建设": "施工建设",
    "竣工验收": "竣工验收",
    "验收": "竣工验收",
    "竣工": "竣工验收",
    "交付售后": "交付售后",
    "交付": "交付售后",
    "售后": "交付售后",
}

# 岗位名关键词
_ROLE_KEYWORDS = [
    "项目总经理", "设计部经理", "开发部经理", "工程部经理", "成本部经理",
    "营销部经理", "财务经理", "客服部经理", "行政人事经理",
    "投资拓展主管", "法务主管", "审计主管",
    "建筑设计主管", "精装设计主管", "景观设计主管", "给排水设计主管",
    "强弱电设计主管", "暖通设计主管",
    "规划报建专员", "工程报建专员", "配套报建专员",
    "土建工程主管", "精装工程主管", "景观工程主管", "水电工程主管",
    "安全总监", "资料员",
    "土建造价主管", "安装造价主管", "招标采购专员", "合同管理员",
    "策划师", "销售主管", "置业顾问", "渠道专员",
    "会计", "出纳",
    "维保主管", "产证专员",
    "行政专员", "人事专员",
]


def _extract_metadata(source_name: str, title: str, body: str) -> dict:
    """从文件名、标题和正文中提取 metadata。

    Returns:
        dict with keys: stage, chunk_id, keywords, responsible_role
    """
    # 推断阶段
    stage = ""
    for kw, stage_name in _STAGE_KEYWORDS.items():
        if kw in source_name or kw in title or kw in body[:200]:
            stage = stage_name
            break

    # 提取 chunk_id（如 LS-3-07, DOC-1-04）
    chunk_id = ""
    id_match = re.search(r"\b(LS-\d+-\d+)\b", title + body[:200])
    if id_match:
        chunk_id = id_match.group(1)
    else:
        id_match = re.search(r"\b(DOC-\d+-\d+)\b", title + body[:200])
        if id_match:
            chunk_id = id_match.group(1)

    # 提取岗位名
    responsible_role = ""
    for role_name in _ROLE_KEYWORDS:
        if role_name in title or role_name in body[:500]:
            responsible_role = role_name
            break

    # 提取关键词
    keywords = _extract_keywords(title, body)

    return {
        "stage": stage,
        "chunk_id": chunk_id,
        "keywords": keywords,
        "responsible_role": responsible_role,
    }


def _extract_keywords(title: str, body: str, max_kw: int = 10) -> list[str]:
    """从标题和正文前 500 字符中提取关键词。"""
    text = f"{title} {body[:500]}"
    # 提取中文词组（2-4 字连续汉字）
    kw_set: set[str] = set()
    chinese_chars = re.findall(r"[一-鿿]{2,6}", text)
    for kw in chinese_chars:
        if len(kw) >= 2:
            kw_set.add(kw)
    # 提取英文字词（2+ 字符）
    eng_words = re.findall(r"[a-zA-Z0-9±]{2,}", text)
    for w in eng_words:
        kw_set.add(w)
    return list(kw_set)[:max_kw]


def _match_stage(chunk: dict, stage: str) -> bool:
    """检查 chunk 是否匹配给定的阶段过滤条件。"""
    chunk_stage = chunk.get("stage", "")
    return stage in chunk_stage or stage in chunk.get("source", "")


def _match_role(chunk: dict, role: str) -> bool:
    """检查 chunk 是否匹配给定的岗位过滤条件。"""
    chunk_role = chunk.get("responsible_role", "")
    if role in chunk_role:
        return True
    # 也检查标题和正文
    return role in chunk.get("title", "") or role in chunk.get("body", "")[:500]
