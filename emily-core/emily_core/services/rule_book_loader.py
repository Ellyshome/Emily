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

import re

# 章节未标注 applicable_levels 时的默认可见级别（fail-open：避免改版后静默失效）
_SECTION_DEFAULT_LEVELS: list[int] = [1, 2, 3, 4, 5, 6]

_RE_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _safe_yaml(text: str) -> dict:
    """解析章节 frontmatter；失败返回空 dict（fail-open）。"""
    try:
        import yaml
        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("rule book frontmatter parse failed: %s", e)
        return {}


def parse_sections(raw: str) -> list[dict]:
    """按 '## 章节' 切分规则书并解析章节级 frontmatter。

    Returns:
        [{title: str, levels: list[int], section_type: str,
          llm_inject: bool, body: str}]
    """
    if not raw:
        return []
    matches = list(_RE_SECTION.finditer(raw))
    sections: list[dict] = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        chunk = raw[start:end]
        stripped = chunk.lstrip("\n")
        fm = _RE_FRONTMATTER.match(stripped)
        if fm:
            meta = _safe_yaml(fm.group(1))
            body = stripped[fm.end():].strip()
        else:
            meta = {}
            body = chunk.strip()
        levels = meta.get("applicable_levels")
        if isinstance(levels, list) and levels:
            parsed: list[int] = []
            for x in levels:
                try:
                    parsed.append(int(x))
                except (TypeError, ValueError):
                    continue
            levels = parsed or list(_SECTION_DEFAULT_LEVELS)
        else:
            levels = list(_SECTION_DEFAULT_LEVELS)
        sections.append({
            "title": m.group(1).strip(),
            "levels": levels,
            "section_type": str(meta.get("section_type", "") or ""),
            "llm_inject": bool(meta.get("llm_inject", True)),
            "body": body,
        })
    return sections


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    nl = cut.rfind("\n")
    return cut[:nl] if nl > 0 else cut


def _section_levels(s: dict) -> list[int]:
    """章节可见等级；缺失/为空时按全员可见（fail-open，避免条文静默失效）。"""
    levels = s.get("levels")
    if levels:
        return levels
    logger.warning(
        "rule section %r has no applicable_levels, fallback to all levels",
        s.get("title", ""),
    )
    return list(_SECTION_DEFAULT_LEVELS)


def _is_visible(s: dict, level: int) -> bool:
    """章节是否对当前等级可见（llm_inject 与等级裁剪统一判定）。"""
    return bool(s.get("llm_inject", True)) and level in _section_levels(s)


def render_full(sections: list[dict], level: int) -> str:
    """按 level 过滤后输出规则全文（供 meta_cognition_read）。"""
    parts: list[str] = []
    for s in sections:
        if not _is_visible(s, level):
            continue
        parts.append(f"## {s['title']}\n{s.get('body', '')}")
    return "\n\n".join(parts)


def render_brief(sections: list[dict], level: int, max_chars: int = 200) -> str:
    """按 level 过滤后生成常驻摘要（≤max_chars）。"""
    picked = [s for s in sections if _is_visible(s, level)]
    if not picked:
        return ""
    text = "适用规则（{}）：{}".format(len(picked), " / ".join(s["title"] for s in picked))
    bullets: list[str] = []
    for s in picked[:4]:
        body_lines = (s.get("body") or "").splitlines()
        first = body_lines[0].strip() if body_lines else ""
        if first:
            bullets.append(f"{s['title']}：{first[:40]}")
    if bullets:
        text += "\n" + "；".join(bullets)
    return _truncate(text, max_chars)


def render_directives(sections: list[dict], level: int, max_chars: int = 2000) -> str:
    """按 level 过滤后输出可见章节正文，供 prompt 常驻注入（`{rule_directives}`）。

    与 render_brief（只给章节标题列表）的区别：这里给**正文**，使按等级载入的规则
    （如 L1 的访客接待规范、L5 的称呼口径）真正进入提示词并生效。超限时按章节边界
    截断，不切碎条文。
    """
    text = render_full(sections, level)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text.rfind("\n## ", 0, max_chars)
    return (text[:cut] if cut > 0 else _truncate(text, max_chars)).strip()


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

    def trim_for_level(self, level: int, max_chars: int = 200) -> str:
        """按权限等级裁剪生成摘要（便捷入口）。"""
        return render_brief(parse_sections(self.content), level, max_chars)

    def render_for_level(self, level: int) -> str:
        """按权限等级裁剪生成全文。"""
        return render_full(parse_sections(self.content), level)

    @property
    def content(self) -> str:
        """当前规则书内容。如果未加载则自动加载。"""
        if not self._loaded:
            self.load()
        return self._content

    @property
    def is_loaded(self) -> bool:
        return self._loaded
