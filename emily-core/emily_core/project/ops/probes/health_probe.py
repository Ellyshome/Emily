"""HealthProbe — 健康度检查探针（占位，Phase 2+ 实现）。

预留接口：检查系统各组件的健康状态（DB/LLM/MaxKB/Email/Pipeline）。
当前返回空列表。
"""

from __future__ import annotations

from ..probe_base import Probe, ProbeFinding, TickContext


class HealthProbe(Probe):
    """健康度检查探针。

    Phase 2+ 将实现以下检查：
      - DB 连接检测（SELECT 1）
      - LLM API ping
      - MaxKB 服务检测
      - Email SMTP/IMAP 检测
      - Pipeline BUS 状态
    """

    def name(self) -> str:
        return "health_probe"

    def enabled(self) -> bool:
        return False  # Phase 2+ 实现后启用

    def run(self, ctx: TickContext) -> list[ProbeFinding]:
        """Phase 2+ 实现。当前返回空列表。"""
        return []
