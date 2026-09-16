# emily-core/emily_core/kernel/policies.py
"""节点级策略（M3 / US-02）—— 重试与超时的声明式配置。

纪律（对应 PRD §4.4 与约束「根治而非迁就」）：
  - **重试交给框架的节点级策略**（`retry_policy=`），节点内不再自持重试循环；
    节点内的计数只保留用于**终态判定**（如连续文本纠错超限转兜底）。
  - **带副作用的节点不得挂重试策略**（执行工具/写库的节点重跑会产生重复副作用）；
    调用模型的纯节点可挂。
  - **挂起（interrupt）节点不得挂超时**，否则等待用户补充信息的挂起会被误杀。
  - 取值经配置注入，未配置时给保守默认值。
"""
from __future__ import annotations

import logging
from typing import Any

from . import react_kernel

logger = logging.getLogger("emily.kernel.policies")

#: 重试次数上限（含首次尝试）
DEFAULT_MAX_ATTEMPTS = 2
#: 首次退避（秒）
DEFAULT_INITIAL_INTERVAL = 0.5
#: 退避倍数
DEFAULT_BACKOFF_FACTOR = 2.0
#: 普通节点超时（秒）——模型调用类节点
DEFAULT_NODE_TIMEOUT_SECONDS = 300
#: 工具/执行类节点超时（秒）
DEFAULT_TOOL_TIMEOUT_SECONDS = 180


def _transient_only(exc: BaseException) -> bool:
    """仅瞬时故障参与框架重试；终态故障由内核转结构化结果，不反复打给模型。"""
    return react_kernel.default_classify_error(exc) == react_kernel.ERROR_TRANSIENT


def build_retry_policy(config: Any):
    """构造节点级重试策略（瞬时故障、指数退避）。

    适用节点：只调用模型、不产生业务副作用的节点（understand / agent_node / plan_build 等）。
    """
    from langgraph.types import RetryPolicy

    attempts = int(getattr(config, "node_retry_max_attempts", DEFAULT_MAX_ATTEMPTS) or 1)
    initial = float(getattr(config, "node_retry_initial_interval", DEFAULT_INITIAL_INTERVAL)
                    or DEFAULT_INITIAL_INTERVAL)
    backoff = float(getattr(config, "node_retry_backoff_factor", DEFAULT_BACKOFF_FACTOR)
                    or DEFAULT_BACKOFF_FACTOR)
    policy = RetryPolicy(
        max_attempts=max(1, attempts),
        initial_interval=initial,
        backoff_factor=backoff,
        jitter=True,
        retry_on=_transient_only,
    )
    return policy


def node_timeout(config: Any, node_name: str, *, default_seconds: int = DEFAULT_NODE_TIMEOUT_SECONDS):
    """单节点超时秒数；返回 None 表示该节点不启用超时。

    优先级：`node_timeout_overrides[node_name]` → `node_timeout_seconds` → 入参默认值。
    """
    overrides = getattr(config, "node_timeout_overrides", None) or {}
    raw = overrides.get(node_name, getattr(config, "node_timeout_seconds", default_seconds))
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        seconds = int(default_seconds)
    return seconds if seconds > 0 else None


def describe(config: Any) -> dict:
    """策略摘要（供启动日志与回归断言）。"""
    policy = build_retry_policy(config)
    return {
        "max_attempts": getattr(policy, "max_attempts", None),
        "initial_interval": getattr(policy, "initial_interval", None),
        "backoff_factor": getattr(policy, "backoff_factor", None),
        "node_timeout_seconds": getattr(config, "node_timeout_seconds", None),
        "overrides": dict(getattr(config, "node_timeout_overrides", None) or {}),
    }
