"""ExpertManualLoader —— 专家手册加载器。

多级 fallback 路径查找，参照 RuleBookLoader 模式。
从磁盘加载专家职能手册 + 任务手册 + 待审文件文本。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("emily.expert_manual_loader")


class ExpertManualLoader:
    """专家手册+文件加载工具（无状态静态方法类）。"""

    _MANUAL_DIR_CACHE: Path | None = None

    @staticmethod
    def resolve_manual_dir() -> Path:
        """解析手册根目录（多级 fallback）。"""
        if ExpertManualLoader._MANUAL_DIR_CACHE is not None:
            return ExpertManualLoader._MANUAL_DIR_CACHE

        candidates = []

        # 1. 容器内路径
        candidates.append("/app/files/Expert Work Manual")

        # 2. 环境变量
        env_dir = os.environ.get("EMILY_EXPERT_MANUAL_DIR", "")
        if env_dir:
            candidates.append(env_dir)

        # 3. 宿主机开发路径
        # __file__ = emily-core/emily_core/services/expert_manual_loader.py
        # parents[3] = 项目根（emily-core/ 的父目录）
        dev_path = (
            Path(__file__).resolve().parents[3]
            / "emily-data" / "files" / "Expert Work Manual"
        )
        candidates.append(str(dev_path))

        for path in candidates:
            p = Path(path)
            if p.exists() and p.is_dir():
                ExpertManualLoader._MANUAL_DIR_CACHE = p
                logger.info("ExpertManualLoader: resolved manual dir to %s", p)
                return p

        # 降级：用 dev_path 作为 fallback，即使不存在
        fallback = Path(candidates[-1])
        logger.warning(
            "ExpertManualLoader: no manual dir found, fallback to %s", fallback
        )
        ExpertManualLoader._MANUAL_DIR_CACHE = fallback
        return fallback

    @staticmethod
    def load_manual(filename: str) -> str:
        """加载手册全文；找不到返回空串+警告。
        
        Args:
            filename: 手册文件名，如 "EXP-001-景观苗木审核.md"
        """
        if not filename:
            return ""
        manual_dir = ExpertManualLoader.resolve_manual_dir()
        filepath = manual_dir / filename
        if filepath.exists() and filepath.is_file():
            try:
                content = filepath.read_text(encoding="utf-8")
                logger.info(
                    "ExpertManualLoader: loaded %s (%d chars)", filename, len(content)
                )
                return content
            except Exception as e:
                logger.warning(
                    "ExpertManualLoader: failed to read %s: %s", filepath, e
                )
                return ""
        logger.warning(
            "ExpertManualLoader: manual not found: %s (searched %s)",
            filename, filepath,
        )
        return ""

    @staticmethod
    def load_review_files(attachments: list[dict] | None,
                          user_input: str = "") -> str:
        """加载待审文件文本。
        
        优先解析附件，无附件时从 user_input 提取文件路径；
        失败降级返回提示串。
        
        Args:
            attachments: 附件列表 [{"file_path": "...", ...}, ...]
            user_input: 用户原文（无附件时尝试提取路径）
        """
        if not attachments:
            return ExpertManualLoader._extract_file_paths_from_text(user_input)

        texts = []
        for att in attachments:
            if not isinstance(att, dict):
                continue
            file_path = att.get("file_path", "") or att.get("local_path", "")
            if not file_path:
                continue
            try:
                p = Path(file_path)
                if p.exists() and p.is_file():
                    ext = p.suffix.lower()
                    if ext in (".txt", ".md", ".csv", ".json"):
                        content = p.read_text(encoding="utf-8")
                    else:
                        # PDF/Word/PPT 等二进制格式 → 复用 parse_document
                        content = ExpertManualLoader._parse_document(file_path)
                    if content:
                        texts.append(f"[文件: {p.name}]\n{content}")
                else:
                    logger.warning(
                        "ExpertManualLoader: file not found: %s", file_path
                    )
            except Exception as e:
                logger.warning(
                    "ExpertManualLoader: failed to load %s: %s", file_path, e
                )

        if texts:
            return "\n\n---\n\n".join(texts)
        return "（无待审文件）"

    @staticmethod
    def _extract_file_paths_from_text(user_input: str) -> str:
        """从用户输入文本中提取文件路径并加载。"""
        if not user_input:
            return "（无待审文件）"
        # 尝试查找类似路径的内容
        import re
        path_pattern = re.compile(r'(?:文件|路径)[：:]\s*([^\s,，]+)')
        matches = path_pattern.findall(user_input)
        if matches:
            return ExpertManualLoader.load_review_files(
                [{"file_path": m} for m in matches]
            )
        return "（无待审文件）"

    @staticmethod
    def _parse_document(file_path: str) -> str:
        """复用 handle_parse_document 解析二进制文档。"""
        try:
            from emily_core.tools.parse_document_tool import handle_parse_document
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在已有事件循环中：创建新任务
                result = asyncio.ensure_future(
                    handle_parse_document({"file_path": file_path})
                )
                # 注意：这在同步代码中无法 await，降级
                return f"[二进制文件: {file_path}，需异步解析]"
            else:
                result = loop.run_until_complete(
                    handle_parse_document({"file_path": file_path})
                )
                if isinstance(result, dict):
                    return result.get("text", "") or str(result)
                return str(result)
        except Exception as e:
            logger.warning(
                "ExpertManualLoader: parse_document failed for %s: %s", file_path, e
            )
            return f"[文件: {Path(file_path).name}]（解析失败）"
