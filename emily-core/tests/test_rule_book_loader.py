"""规则书章节解析与按等级裁剪的单元测试。

重点覆盖 P2-2 的兜底契约：缺 `applicable_levels`（含缺失/为空/非法）的章节
必须按全员可见处理，解析层与渲染层都不得静默丢弃条文。
"""

import logging

from emily_core.services.rule_book_loader import (
    parse_sections,
    render_brief,
    render_full,
)

_ALL_LEVELS = [1, 2, 3, 4, 5, 6]


def test_parse_sections_defaults_missing_levels_to_all():
    """无 frontmatter 的章节按全员可见处理。"""
    sections = parse_sections("## 一章\n无标注正文\n")

    assert len(sections) == 1
    assert sections[0]["levels"] == _ALL_LEVELS


def test_parse_sections_invalid_levels_falls_back_to_all():
    """等级标注非法（非 int）时按全员可见处理，不得丢章节。"""
    raw = '## 一章\n---\napplicable_levels: ["x"]\n---\n正文\n'
    sections = parse_sections(raw)

    assert len(sections) == 1
    assert sections[0]["levels"] == _ALL_LEVELS


def test_parse_sections_empty_levels_falls_back_to_all():
    """等级标注为空列表时按全员可见处理。"""
    raw = "## 一章\n---\napplicable_levels: []\n---\n正文\n"
    sections = parse_sections(raw)

    assert sections[0]["levels"] == _ALL_LEVELS


def test_render_full_filters_by_level():
    """正常裁剪：高等级专属章节对低等级不可见。"""
    raw = (
        "## 高等级专属\n---\napplicable_levels: [6]\n---\nsecret-rule\n\n"
        "## 全员可见\n---\napplicable_levels: [1, 2, 3, 4, 5, 6]\n---\npublic-rule\n"
    )
    sections = parse_sections(raw)

    low = render_full(sections, 1)
    assert "secret-rule" not in low
    assert "public-rule" in low
    assert "secret-rule" in render_full(sections, 6)


def test_render_full_fail_open_on_missing_levels():
    """渲染层收到未经解析、缺 levels 的章节时不得静默丢弃。"""
    hand = [{"title": "手拼章节", "body": "兜底正文"}]

    assert "兜底正文" in render_full(hand, 1)


def test_render_brief_fail_open_on_missing_levels():
    hand = [{"title": "手拼章节", "body": "兜底正文"}]

    assert "手拼章节" in render_brief(hand, 1)


def test_render_fail_open_on_empty_levels():
    """levels 为空列表同样按全员可见，摘要与全文行为一致。"""
    hand = [{"title": "空等级章节", "body": "正文", "levels": []}]

    assert "空等级章节" in render_brief(hand, 6)
    assert "正文" in render_full(hand, 6)


def test_llm_inject_false_is_hidden():
    """llm_inject=false 的章节不进摘要、不进按需查阅。"""
    hand = [{"title": "仅人读", "body": "人读正文", "llm_inject": False}]

    assert "人读正文" not in render_full(hand, 6)
    assert "仅人读" not in render_brief(hand, 6)


def test_missing_levels_emits_warning(caplog):
    """兜底放行时必须留下日志信号，避免标注遗漏无声无息。"""
    hand = [{"title": "未标注", "body": "正文"}]

    with caplog.at_level(logging.WARNING, logger="emily.rule_book_loader"):
        render_full(hand, 1)

    assert any("applicable_levels" in record.getMessage() for record in caplog.records)
