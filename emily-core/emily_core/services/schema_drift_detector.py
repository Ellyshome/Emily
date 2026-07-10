"""SchemaDriftDetector —— 系统描述偏差检测器。

比对代码结构 hash vs 存储的描述 hash，检测三域是否过时。
纯代码反射，无需 LLM，非常轻量。

参照模式：emily_core/services/cognition_drift_detector.py
"""

from __future__ import annotations

import logging
from typing import Optional

from ..repositories.system_description_repo import SystemDescriptionRepo

logger = logging.getLogger("emily.schema_drift_detector")


class SchemaDriftDetector:
    """系统描述偏差检测器——比对代码结构 hash vs 存储的描述快照。"""

    def detect(self) -> dict:
        """检测系统描述是否与当前代码结构一致。

        Returns:
            {
                "has_description": bool,
                "needs_build": bool,
                "has_drift": bool,
                "stale_domains": ["database", ...],
                "drift": {"database": {"stale": bool, "signals": [...]}, ...},
            }
        """
        stored = SystemDescriptionRepo.get_latest()
        if stored is None:
            return {
                "has_description": False,
                "needs_build": True,
                "has_drift": True,
                "stale_domains": ["database", "file", "permission"],
                "drift": {},
            }

        drift = {}

        # D1: 数据库 schema 偏差
        drift["database"] = self._check_schema_drift(stored.schema_hash)

        # D2: 文件模型偏差
        drift["file"] = self._check_file_model_drift(stored.file_model_hash)

        # D3: 权限体系偏差
        drift["permission"] = self._check_permission_drift(stored.permission_hash)

        stale_domains = [k for k, v in drift.items() if v.get("stale", False)]

        return {
            "has_description": True,
            "needs_build": False,
            "has_drift": len(stale_domains) > 0,
            "stale_domains": stale_domains,
            "drift": drift,
        }

    def _check_schema_drift(self, stored_hash: str) -> dict:
        """D1：数据库 schema 偏差检测。"""
        signals = []
        stale = False
        try:
            from ..services.system_description_builder import SystemDescriptionBuilder
            current_hash = SystemDescriptionBuilder._compute_schema_hash()
            if current_hash != stored_hash:
                signals.append(f"schema_hash \u53d8\u5316: {stored_hash[:12]}...\u2192{current_hash[:12]}...")
                stale = True
        except Exception as e:
            signals.append(f"\u68c0\u6d4b\u5f02\u5e38: {e}")
        return {"stale": stale, "signals": signals}

    def _check_file_model_drift(self, stored_hash: str) -> dict:
        """D2：文件模型偏差检测。"""
        signals = []
        stale = False
        try:
            from ..services.system_description_builder import SystemDescriptionBuilder
            current_hash = SystemDescriptionBuilder._compute_file_model_hash()
            if current_hash != stored_hash:
                signals.append(f"file_model_hash \u53d8\u5316: {stored_hash[:12]}...\u2192{current_hash[:12]}...")
                stale = True
        except Exception as e:
            signals.append(f"\u68c0\u6d4b\u5f02\u5e38: {e}")
        return {"stale": stale, "signals": signals}

    def _check_permission_drift(self, stored_hash: str) -> dict:
        """D3：权限体系偏差检测。"""
        signals = []
        stale = False
        try:
            from ..services.system_description_builder import SystemDescriptionBuilder
            current_hash = SystemDescriptionBuilder._compute_permission_hash()
            if current_hash != stored_hash:
                signals.append(f"permission_hash \u53d8\u5316: {stored_hash[:12]}...\u2192{current_hash[:12]}...")
                stale = True
        except Exception as e:
            signals.append(f"\u68c0\u6d4b\u5f02\u5e38: {e}")
        return {"stale": stale, "signals": signals}
