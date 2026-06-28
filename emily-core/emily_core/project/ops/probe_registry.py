"""ProbeRegistry — 探针注册器。

管理所有已注册的 Probe 实例，提供注册、查询功能。
同名 Probe 重复注册会抛出 ValueError。
"""

from __future__ import annotations

from .probe_base import Probe


class ProbeRegistry:
    """探针注册器。

    维护 name → Probe 的映射，支持按启用状态过滤查询。
    """

    def __init__(self):
        self._probes: dict[str, Probe] = {}

    def register(self, probe: Probe) -> None:
        """注册一个探针。同名抛 ValueError。"""
        name = probe.name()
        if name in self._probes:
            raise ValueError(f"Probe '{name}' is already registered")
        self._probes[name] = probe

    def get_enabled_probes(self) -> list[Probe]:
        """返回所有 enabled() 为 True 的探针。"""
        return [p for p in self._probes.values() if p.enabled()]

    def get_all(self) -> list[Probe]:
        """返回所有已注册的探针。"""
        return list(self._probes.values())
