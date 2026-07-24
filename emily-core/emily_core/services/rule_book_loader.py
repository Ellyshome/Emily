"""RuleBookLoader —— 规则书加载与热重载。

从 emily-data/rules/规则书.md 读取规则书全文，注入 Session prompt 的 {rule_book} 变量。
支持热重载：API 触发 reload_rule_book() 后更新所有活跃 Session。

参照模式：emily_core/skill/registry.py（多级 fallback 路径查找 + 热重载）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emily.rule_book_loader")


class RuleBookLoader:
    """规则书加载器。"""

    def __init__(self):
        self._content: str = ""
        self._loaded: bool = False

    def load(self) -> str:
        """加载规则书文件。多级 fallback 路径。"""
        # 路径优先级：容器内 > 环境变量 > 宿主机开发路径
        candidates = []

        # 1. 容器内路径
        candidates.append("/app/rules/规则书.md")

        # 2. 环境变量
        env_dir = os.environ.get("EMILY_RULE_BOOK_DIR", "")
        if env_dir:
            candidates.append(str(Path(env_dir) / "规则书.md"))

        # 3. 宿主机开发路径
        # __file__ = emily-core/emily_core/services/rule_book_loader.py
        # parents[3] = 项目根（emily-core/ 的父目录），指向 emily-data/rules/规则书.md
        dev_path = Path(__file__).resolve().parents[3] / "emily-data" / "rules" / "规则书.md"
        candidates.append(str(dev_path))

        for path in candidates:
            p = Path(path)
            if p.exists() and p.is_file():
                try:
                    self._content = p.read_text(encoding="utf-8")
                    self._loaded = True
                    logger.info("RuleBook loaded from %s (%d chars)", path, len(self._content))
                    return self._content
                except Exception as e:
                    logger.warning("Failed to read rule book from %s: %s", path, e)

        # 加载失败：降级为空字符串（不阻塞）
        self._content = ""
        self._loaded = False
        logger.warning("RuleBook file not found in any candidate path, using empty string")
        return ""

    def reload(self) -> dict:
        """热重载规则书。"""
        old_len = len(self._content)
        self.load()
        new_len = len(self._content)
        changed = old_len != new_len
        logger.info("RuleBook reload: %d->%d chars, changed=%s", old_len, new_len, changed)
        return {
            "ok": True,
            "content_length": new_len,
            "changed": changed,
        }

    @property
    def content(self) -> str:
        """当前规则书内容。如果未加载则自动加载。"""
        if not self._loaded:
            self.load()
        return self._content

    @property
    def is_loaded(self) -> bool:
        return self._loaded
