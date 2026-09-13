# emily-core/emily_core/retrieval/channel_registry.py
"""M1 通道元数据注册 + M5 出处标注。

七项元数据：所属域 / 回答对象 / 参数 / 权限维度 / 是否含正文 / 空结果语义 / 升级目标。
来源：`emily-data/config/retrieval_channels.json`（配置文件承载静态字段与默认策略）；
可运营字段（启用状态、优先级）后续进数据表，属 P2。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("emily.retrieval.channels")

_CANDIDATES = (
    os.environ.get("EMILY_RETRIEVAL_CHANNELS", ""),
    os.path.join("emily-data", "config", "retrieval_channels.json"),
    "/app/config/retrieval_channels.json",
    os.path.join("config", "retrieval_channels.json"),
)

REQUIRED = ("name", "tools", "domain", "answers", "has_text",
            "permission", "empty_semantics", "upgrade_to")


@dataclass
class Channel:
    name: str
    tools: list = field(default_factory=list)
    domain: str = ""
    answers: str = ""
    params: str = ""
    permission: str = ""
    has_text: bool = False
    empty_semantics: str = ""
    upgrade_to: list = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in REQUIRED + ("params", "enabled")}


def _read(path: str) -> "dict | None":
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("channel config parse failed: %s — %s", path, e)
        return None


def load_channels(path: "str | None" = None) -> list:
    """加载并校验通道元数据；缺字段的通道被跳过并记日志（fail-closed）。"""
    raw = None
    for cand in ((path,) if path else ()) + _CANDIDATES:
        if cand and os.path.exists(cand):
            raw = _read(cand)
            if raw:
                logger.debug("channels loaded from %s", cand)
                break
    if not raw:
        logger.warning("通道元数据未加载：配置缺失（检索仍可用，但缺少通道治理信息）")
        return []
    out = []
    for item in (raw.get("channels") or []):
        missing = [k for k in REQUIRED if k not in item]
        if missing:
            logger.warning("通道 %s 元数据缺字段 %s，跳过", item.get("name"), missing)
            continue
        out.append(Channel(
            name=str(item.get("name")),
            tools=list(item.get("tools") or []),
            domain=str(item.get("domain") or ""),
            answers=str(item.get("answers") or ""),
            params=str(item.get("params") or ""),
            permission=str(item.get("permission") or ""),
            has_text=bool(item.get("has_text")),
            empty_semantics=str(item.get("empty_semantics") or ""),
            upgrade_to=list(item.get("upgrade_to") or []),
            enabled=bool(item.get("enabled", True)),
        ))
    logger.info("retrieval channels: %d 条已加载（%d 条启用）",
                len(out), sum(1 for c in out if c.enabled))
    return out


def describe_for_prompt(channels: "list | None" = None) -> str:
    """生成给模型看的通道说明块（M1 的消费方：提示词构建）。"""
    chs = channels if channels is not None else load_channels()
    if not chs:
        return ""
    lines = ["## 信息检索通道说明（去哪找、每条能答什么）"]
    for c in chs:
        if not c.enabled:
            continue
        tool_txt = "、".join(c.tools) if c.tools else "（无对应工具）"
        text_txt = "含正文" if c.has_text else "仅记录，不含正文"
        lines.append(
            "- **%s**（%s）：回答「%s」；%s；权限维度：%s；查不到时的语义：%s%s"
            % (c.name, tool_txt, c.answers, text_txt, c.permission or "沿用统一口径",
               c.empty_semantics or "无内容",
               ("；→ 改查：" + "、".join(c.upgrade_to)) if c.upgrade_to else "")
        )
    lines.append("说明：**正文只在知识库**，文件与节点通道只返回记录与状态；"
                 "需要文档内容时用知识库检索。")
    return "\n".join(lines)


def format_provenance(items: "list | None") -> str:
    """M5 出处标注：把命中项格式化为可区分的来源文本。"""
    if not items:
        return ""
    parts = []
    for it in items:
        if not isinstance(it, dict):
            continue
        ch = str(it.get("channel") or it.get("provider_name") or "")
        obj = str(it.get("object") or it.get("doc_name") or it.get("title") or "")
        if ch or obj:
            parts.append("%s%s" % (ch, ("「%s」" % obj) if obj else ""))
    return "、".join(dict.fromkeys(parts))
