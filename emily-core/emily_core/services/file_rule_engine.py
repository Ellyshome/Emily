"""FileRuleEngine —— 入站附件候选 purpose 推断。

基于文件名/扩展名/MIME/来源渠道的规则匹配。不读文件内容。
未来增强方向见 需求/强化文件分类识别.md。
"""

import logging
import re

logger = logging.getLogger("emily.service.file_rule_engine")


class FileRuleEngine:
    """入站附件候选 purpose 推断引擎。

    按优先级匹配规则，首个命中返回。默认 RECORD。
    """

    # 规则表（按优先级，首个命中返回）
    RULES = [
        # CHAT: 梗图/表情包（不入库）
        (lambda f: f["ext"] in (".gif",) or _match(f["name"], ["表情", "meme", "梗", "包情"]), "CHAT"),
        # EVIDENCE: 证照/合同/批复
        (lambda f: _match(f["name"], ["证", "许可", "执照", "合同", "批复", "合格单", "审批"]), "EVIDENCE"),
        # DESIGN: 图纸
        (lambda f: f["ext"] in (".dwg", ".dxf") or _match(f["name"], ["施工图", "设计图", "图纸", "竣工图"]), "DESIGN"),
        # REFERENCE: 参考样例
        (lambda f: _match(f["name"], ["参考", "样例", "规范", "工艺", "做法", "样板"]), "REFERENCE"),
    ]

    @staticmethod
    def guess_purpose(filename: str, mime: str = "", context: dict | None = None) -> str:
        """推断候选 purpose。返回 EVIDENCE/RECORD/DESIGN/REFERENCE/CHAT。

        Args:
            filename: 文件名（含扩展名）
            mime: MIME 类型
            context: 可选上下文（预留）

        Returns:
            候选 purpose 枚举值
        """
        f = {
            "name": filename or "",
            "ext": _ext(filename),
            "mime": mime or "",
        }
        for rule, purpose in FileRuleEngine.RULES:
            try:
                if rule(f):
                    logger.debug("Rule engine match: %s → %s", filename, purpose)
                    return purpose
            except Exception:
                continue
        return "RECORD"  # 默认


def _match(name: str, keywords: list[str]) -> bool:
    """检查 name 中是否包含任一关键词。"""
    return any(k in name for k in keywords)


def _ext(filename: str) -> str:
    """提取文件扩展名（小写）。"""
    import os
    return os.path.splitext(filename or "")[1].lower()
