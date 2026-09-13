# emily-core/emily_core/retrieval/strategy.py
"""M2 策略表：分工表、升级目标、并发白名单、跳数上限（P0 只做读取与提示词输出）。

来源：`emily-data/config/retrieval_strategy.json`（运维可改，不发版）。
配置缺失或非法时回落内置最小默认（单通道、不升级），并告警（fail-safe）。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("emily.retrieval.strategy")

_CANDIDATES = (
    os.environ.get("EMILY_RETRIEVAL_STRATEGY", ""),
    os.path.join("emily-data", "config", "retrieval_strategy.json"),
    "/app/config/retrieval_strategy.json",
    os.path.join("config", "retrieval_strategy.json"),
)

MINIMAL_DEFAULT = {
    "enabled": False,          # 策略表整体开关（NFR-3 可回退）
    "max_hops": 1,             # 回落默认：单通道，不升级
    "concurrency_whitelist": [],
    "dispatch": [],
}


@dataclass
class Strategy:
    enabled: bool = False
    max_hops: int = 1
    concurrency_whitelist: list = field(default_factory=list)
    dispatch: list = field(default_factory=list)
    source: str = "builtin_default"

    def prefer_for(self, intent: str) -> "tuple | None":
        for row in self.dispatch:
            if row.get("intent") and row["intent"] in str(intent or ""):
                return (row.get("prefer"), list(row.get("fallback") or []))
        return None


def load_strategy(path: "str | None" = None) -> Strategy:
    for cand in ((path,) if path else ()) + _CANDIDATES:
        if not cand or not os.path.exists(cand):
            continue
        try:
            with open(cand, encoding="utf-8") as f:
                raw = json.load(f) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("策略表解析失败 %s：%s → 回落最小默认", cand, e)
            continue
        max_hops = int(raw.get("max_hops", 1) or 1)
        if max_hops < 1:
            logger.warning("策略表 max_hops=%s 非法 → 约束为 1", max_hops)
            max_hops = 1
        st = Strategy(
            enabled=bool(raw.get("enabled", False)),
            max_hops=max_hops,
            concurrency_whitelist=list(raw.get("concurrency_whitelist") or []),
            dispatch=list(raw.get("dispatch") or []),
            source=cand,
        )
        logger.info("retrieval strategy: enabled=%s max_hops=%d dispatch=%d whitelist=%d",
                    st.enabled, st.max_hops, len(st.dispatch), len(st.concurrency_whitelist))
        return st
    logger.warning("策略表未找到 → 回落最小默认（单通道、不升级）")
    return Strategy(**MINIMAL_DEFAULT)


def strategy_enabled() -> bool:
    return load_strategy().enabled


def dispatch_table_text(strategy: "Strategy | None" = None) -> str:
    """把分工表转成提示词可读文本（M2 的消费方：提示词构建）。"""
    st = strategy if strategy is not None else load_strategy()
    if not st.enabled or not st.dispatch:
        return ""
    lines = ["## 检索通道分工（优先按此选择）"]
    for row in st.dispatch:
        intent = str(row.get("intent") or "").strip()
        prefer = str(row.get("prefer") or "").strip()
        fallback = "、".join(row.get("fallback") or [])
        if not intent or not prefer:
            continue
        lines.append("- 问题类型「%s」→ 首选：%s%s"
                     % (intent, prefer, ("；查不到再查：" + fallback) if fallback else ""))
    if st.max_hops:
        lines.append("（最多 %d 跳；仍无结果时如实告知已查范围）" % st.max_hops)
    return "\n".join(lines) if len(lines) > 1 else ""
