"""SOP Markdown 块级 AST 解析器。

用 mistune 的 AST 渲染器（renderer='ast'）将 SOP Markdown 文件解析为
块级 Token 列表，然后按 heading level + 块类型（table/list/block_quote）
进行结构化提取，替代 intent_registry.py 中 7 个正则辅助函数。

依赖：mistune >= 3.0（使用 renderer='ast' + plugins=['table']）
"""

import re
from typing import Any


# ── mistune 惰性加载（未安装时仍可 import 本模块，由调用方降级） ──
_md: Any = None


def _get_md() -> Any:
    """获取或创建 mistune markdown 实例（惰性初始化）。"""
    global _md
    if _md is None:
        import mistune  # type: ignore[import-untyped]

        _md = mistune.create_markdown(renderer="ast", plugins=["table"])
    return _md


def _parse_to_tokens(content: str) -> list[dict[str, Any]]:
    """将 Markdown 文本解析为块级 AST token 列表。"""
    return _get_md()(content)  # type: ignore[no-any-return]


# ── 内联 Token 遍历工具 ──

# mistune 3.x AST token 结构速查：
#   heading:   {'type': 'heading', 'attrs': {'level': N}, 'children': [...]}
#   paragraph: {'type': 'paragraph', 'children': [...]}
#   list:      {'type': 'list', 'tight': bool, 'children': [list_item, ...]}
#   list_item: {'type': 'list_item', 'children': [block_text, ...]}
#   block_text:{'type': 'block_text', 'children': [text/codespan/strong/...]}
#   block_quote: {'type': 'block_quote', 'children': [paragraph, ...]}
#   table:     {'type': 'table', 'children': [table_head, table_body]}
#   table_head/body: {'type': 'table_head'|'table_body', 'children': [table_row]}
#   table_row: {'type': 'table_row', 'children': [table_cell, ...]}
#   table_cell:{'type': 'table_cell', 'attrs': {head: bool, align: ...}, 'children': [...]}
#   text:      {'type': 'text', 'raw': '...'}
#   codespan:  {'type': 'codespan', 'raw': '...', 'attrs': {'text': '...'}}
#   strong:    {'type': 'strong', 'children': [...]}


def _extract_text(children: list[dict[str, Any]]) -> str:
    """递归提取 inline token children 中的所有纯文本。"""
    parts: list[str] = []
    for child in children:
        ttype = child.get("type", "")
        if ttype == "text":
            parts.append(child.get("raw", ""))
        elif ttype == "codespan":
            attrs = child.get("attrs", {})
            parts.append(attrs.get("text", child.get("raw", "")))
        elif ttype in (
            "strong", "emphasis", "link", "image",
            "block_text", "block_code", "paragraph",
        ):
            parts.append(_extract_text(child.get("children", [])))
        elif ttype in ("softbreak", "linebreak"):
            parts.append(" ")
        elif child.get("children"):
            # 未知容器类型：递归穿透
            parts.append(_extract_text(child["children"]))
    return "".join(parts)


def _extract_codespan_values(children: list[dict[str, Any]]) -> list[str]:
    """递归提取 inline token children 中所有 codespan 值（如 `role1`）。"""
    codes: list[str] = []
    for child in children:
        ttype = child.get("type", "")
        if ttype == "codespan":
            attrs = child.get("attrs", {})
            codes.append(attrs.get("text", child.get("raw", "")))
        elif child.get("children"):
            codes.extend(_extract_codespan_values(child["children"]))
    return codes


def _has_strong_prefix(children: list[dict[str, Any]], text_prefix: str) -> bool:
    """检查 children 中是否存在文本以 text_prefix 开头的 **bold** token。"""
    for child in children:
        if child.get("type") == "strong":
            txt = _extract_text(child.get("children", []))
            if txt.startswith(text_prefix):
                return True
    return False


def _collect_paragraph_texts(children: list[dict[str, Any]]) -> list[str]:
    """收集 block_quote 中每个 paragraph 子节点的独立文本行。

    block_quote 可能包含多个 paragraph（如 > line1 + > line2），
    每个 paragraph 代表一条独立的引用行，需要分开提取。
    """
    lines: list[str] = []
    for child in children:
        if child.get("type") == "paragraph":
            lines.append(_extract_text(child.get("children", [])))
        elif child.get("children"):
            lines.extend(_collect_paragraph_texts(child["children"]))
    return lines


# ── 块级 Token Walker ──


class _SOPBlockWalker:
    """遍历 mistune 块级 Token 列表，按章节上下文提取结构化字段。

    状态机依据：
    - heading level 1/2/3 → 跟踪当前章节编号
    - paragraph 中的 **bold** 标记 → 切换 §2.1 和 §2.2 的上下文子状态
    - table → 按章节上下文决定提取哪些字段
    - list → §2.1 上下文下提取触发/否定关键词
    - block_quote → §2.2 上下文下提取正/反示例
    """

    def __init__(self) -> None:
        # ── 章节跟踪 ──
        self._h2_num: int | None = None  # e.g. 1, 2, 3
        self._h3_id: str | None = None  # e.g. "2.1", "2.2", "3.2"

        # ── §2.1 子状态（由 **必须条件** / **否定条件** 切换） ──
        self._in_must: bool = False
        self._in_deny: bool = False

        # ── §2.2 子状态（由 **应触发…** / **不应触发…** 切换） ──
        self._in_pos_example: bool = False
        self._in_neg_example: bool = False

        # ── 提取结果缓冲区 ──
        self.display_name: str = ""
        self.sop_id: str = ""
        self.sop_type: str = ""
        self.version: str = ""
        self.allow_roles: list[str] = []
        self.trigger_keywords: list[str] = []
        self.deny_conditions: list[str] = []
        self.positive_examples: list[str] = []
        self.negative_examples: list[str] = []
        self.allowed_tools: list[str] = []

    # ── 主遍历入口 ──

    def walk(self, tokens: list[dict[str, Any]]) -> dict[str, Any]:
        """遍历所有 token，返回提取结果 dict。"""
        for token in tokens:
            ttype: str = token.get("type", "")

            if ttype == "heading":
                self._on_heading(token)
            elif ttype == "table":
                self._on_table(token)
            elif ttype == "list":
                self._on_list(token)
            elif ttype == "block_quote":
                self._on_block_quote(token)
            elif ttype == "paragraph":
                self._on_paragraph(token)
            # thematic_break, block_code, blank_line 等直接跳过

        return {
            "display_name": self.display_name,
            "sop_id": self.sop_id,
            "sop_type": self.sop_type,
            "version": self.version,
            "allow_roles": tuple(self.allow_roles) if self.allow_roles else (),
            "trigger_keywords": tuple(self.trigger_keywords),
            "deny_conditions": tuple(self.deny_conditions),
            "positive_examples": tuple(self.positive_examples),
            "negative_examples": tuple(self.negative_examples),
            "allowed_tools": list(self.allowed_tools),
        }

    # ── 块类型处理器 ──

    def _on_heading(self, token: dict[str, Any]) -> None:
        """heading: 更新章节跟踪状态。"""
        children: list[dict[str, Any]] = token.get("children", [])
        text = _extract_text(children).strip()
        level: int = token.get("attrs", {}).get("level", 0)

        if level == 1:
            # # 显示名称 — 业务流服务手册
            name = re.sub(r"\s*—\s*业务流服务手册\s*$", "", text)
            self.display_name = name.strip()

        elif level == 2:
            m = re.match(r"(\d+)\.", text)
            self._h2_num = int(m.group(1)) if m else None
            self._h3_id = None
            self._reset_section_state()

        elif level == 3:
            m = re.match(r"(\d+\.\d+)", text)
            self._h3_id = m.group(1) if m else None
            self._reset_section_state()

    def _on_table(self, token: dict[str, Any]) -> None:
        """table: 按章节上下文分发到具体提取方法。"""
        if self._h2_num == 1:
            self._extract_chapter1_table(token)
        elif self._h2_num == 3 and self._h3_id == "3.2":
            self._extract_section_32_table(token)

    def _on_list(self, token: dict[str, Any]) -> None:
        """list: §2.1 中提取触发条件 / 否定条件。"""
        if self._h2_num != 2:
            return
        if self._h3_id not in ("2.1", None):
            return

        for item_token in token.get("children", []):
            if item_token.get("type") != "list_item":
                continue
            item_text = _extract_text(item_token.get("children", [])).strip()
            if not item_text:
                continue

            if self._in_must:
                # 按中文顿号拆分为子关键词（与旧正则行为一致）
                sub_items = re.split(r"[、；;]", item_text)
                for sub in sub_items:
                    sub = sub.strip()
                    if sub and len(sub) > 2:
                        self.trigger_keywords.append(sub)
            elif self._in_deny:
                self.deny_conditions.append(item_text)

    def _on_block_quote(self, token: dict[str, Any]) -> None:
        """block_quote: §2.2 中提取正/反示例。"""
        if self._h2_num != 2 or self._h3_id != "2.2":
            return

        # 递归收集 block_quote 中的所有 paragraph 子节点，
        # 每个 paragraph 对应一条独立的引用行
        lines = _collect_paragraph_texts(token.get("children", []))
        if not lines:
            return

        for line_text in lines:
            line_text = line_text.strip()
            if not line_text:
                continue

            if self._in_pos_example:
                quotes = re.findall(r"「([^」]+)」", line_text)
                for q in quotes:
                    q = q.strip()
                    if q:
                        self.positive_examples.append(q)

            elif self._in_neg_example:
                quotes = re.findall(r"「([^」]+)」", line_text)
                for q in quotes:
                    q = q.strip()
                    if q:
                        self.negative_examples.append(q)
                # 提取 → 后的原因说明（→ 出现在」之后，下一个「或行尾之前）
                arrows = re.findall(r"」\s*→\s*(.+?)(?=「|$)", line_text)
                for a in arrows:
                    a = a.strip()
                    if a and a not in self.negative_examples:
                        self.negative_examples.append(a)

    def _on_paragraph(self, token: dict[str, Any]) -> None:
        """paragraph: 检测 **bold** 标记切换 §2.1/§2.2 子状态。"""
        children: list[dict[str, Any]] = token.get("children", [])

        if _has_strong_prefix(children, "必须条件"):
            self._in_must = True
            self._in_deny = False
        elif _has_strong_prefix(children, "否定条件"):
            self._in_must = False
            self._in_deny = True
        elif _has_strong_prefix(children, "应触发此业务流"):
            self._in_pos_example = True
            self._in_neg_example = False
        elif _has_strong_prefix(children, "不应触发此业务流"):
            self._in_pos_example = False
            self._in_neg_example = True

    # ── 表格字段提取 ──

    def _extract_chapter1_table(self, token: dict[str, Any]) -> None:
        """从第1章版本信息表中提取 sop_id / version / allow_roles / sop_type。

        注意：mistune 3.x table token 结构为
          table → table_head / table_body → table_row → table_cell
        """
        for section in token.get("children", []):  # table_head, table_body
            for row_token in section.get("children", []):
                if row_token.get("type") != "table_row":
                    continue
                cells: list[dict[str, Any]] = row_token.get("children", [])
                if len(cells) < 2:
                    continue

                key = _extract_text(cells[0].get("children", [])).strip()
                val_children = cells[1].get("children", [])

                if key == "业务流编号" and not self.sop_id:
                    self.sop_id = _extract_text(val_children).strip()
                elif key == "版本" and not self.version:
                    self.version = _extract_text(val_children).strip()
                elif key == "权限控制":
                    roles = _extract_codespan_values(val_children)
                    seen: set[str] = set()
                    unique: list[str] = []
                    for r in roles:
                        if r not in seen:
                            seen.add(r)
                            unique.append(r)
                    if unique:
                        self.allow_roles = unique
                elif key == "业务类型" and not self.sop_type:
                    self.sop_type = _extract_text(val_children).strip()

    def _extract_section_32_table(self, token: dict[str, Any]) -> None:
        """从 §3.2 工具表中提取允许的工具名（用于 BusinessFlowAgent 工具过滤）。"""
        for section in token.get("children", []):  # table_head, table_body
            for row_token in section.get("children", []):
                if row_token.get("type") != "table_row":
                    continue
                cells: list[dict[str, Any]] = row_token.get("children", [])
                if not cells:
                    continue
                # 第1列是工具名（反引号包裹的 codespan）
                tool_names = _extract_codespan_values(cells[0].get("children", []))
                for name in tool_names:
                    # 与旧正则一致的过滤规则
                    if (
                        name.startswith("record_")
                        or name.startswith("query_")
                        or name.startswith("invoke_")
                        or name.startswith("manage_")
                        or name.startswith("write_")
                    ):
                        if name not in self.allowed_tools:
                            self.allowed_tools.append(name)

    # ── 辅助 ──

    def _reset_section_state(self) -> None:
        """进入新章节时重置 §2.1/§2.2 子状态。"""
        self._in_must = False
        self._in_deny = False
        self._in_pos_example = False
        self._in_neg_example = False


# ── 公开 API ──


def parse_sop_markdown(content: str, file_path: str, file_name: str) -> dict[str, Any]:
    """解析 SOP Markdown 文件为结构化字段 dict。

    由 intent_registry.py 调用，返回 dict 后由调用方应用 fallback 并构建 SOPIntentSpec。

    Returns dict with keys:
        display_name, sop_id, sop_type, version, allow_roles,
        trigger_keywords, deny_conditions, positive_examples,
        negative_examples, allowed_tools
    """
    tokens = _parse_to_tokens(content)
    walker = _SOPBlockWalker()
    return walker.walk(tokens)


def extract_allowed_tools_from_sop(sop_text: str) -> list[str]:
    """从 SOP 全文的 §3.2 表中提取允许的工具名列表。

    供 business_flow_tool.py 使用，复用同一套 mistune 解析器。
    """
    tokens = _parse_to_tokens(sop_text)
    walker = _SOPBlockWalker()
    result = walker.walk(tokens)
    return result.get("allowed_tools", [])
