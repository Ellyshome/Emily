"""能力目录与工具集装配模块（M2）—— 会话可见能力的装配与护栏。

定位（计划 M2 / PRD US-02、US-03、US-04）：
  - 把"会话可见的能力"装配为 LLM 工具集：查询能力 + 执行手脚 + SOP 能力 + 控制工具；
  - **复用**现有权限过滤通道（`_session_api_ids` fail-closed）与分级兜底门禁
    （`FallbackPolicy`），不新造权限判定（PRD §4.4-2）；
  - 施加写护栏：高危（overwrite/delete）能力不进自由工具集；普通档只读、高级档可追加写。

只装配，不执行。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("emily.session.capability_catalog")

# 两段式加载阈值（PRD 风险 2 / 附录 B-4）：当前**全量暴露**，超过阈值仅告警，不实现两段式。
CAPABILITY_TWO_STAGE_THRESHOLD = 20

# 高危写语义：不出现在自由工具集，只能经对应 SOP 能力完成（AC-US-04.1）
_HIGH_RISK_WRITE_MODES = frozenset({"overwrite", "delete"})

# WI 生命周期控制工具名（新循环语义不同，需排除后追加自有控制工具）
_WI_CONTROL_NAMES = frozenset({"complete_work", "ask_user"})


@dataclass
class CapabilityEntry:
    """能力目录条目。"""

    name: str                              # 能力名（= LLM 工具名）
    kind: str                              # query | write | sop | resolver | control
    sop_id: str = ""                       # SOP 能力：执行期使用的文件 stem
    display_name: str = ""
    description: str = ""
    write_mode: str = "read"               # read/append/transition/overwrite/delete
    parameters: dict = field(default_factory=dict)


class _CtxShim:
    """把 SessionContext 适配为 `_session_api_ids(ctx)` 期望的 ctx 形状（复用现有函数）。"""

    def __init__(self, session_context) -> None:
        self._sc = session_context

    def get_session_context(self):
        return self._sc


class CapabilityCatalog:
    """能力目录与工具集装配器。"""

    def __init__(self, core=None, skill_registry=None, business_tools=None,
                 capability_registry=None, resolvers=None, config=None) -> None:
        if core is not None:
            skill_registry = skill_registry or getattr(core, "_skill_registry", None)
            business_tools = business_tools or getattr(core, "_business_flow_tools", None)
            capability_registry = capability_registry or getattr(core, "_capability_registry", None)
            resolvers = resolvers or getattr(core, "_resolvers", None)
            config = config if config is not None else getattr(core, "config", None)
        self._core = core
        self._skill_registry = skill_registry
        self._business_tools = business_tools
        self._capability_registry = capability_registry
        self._resolvers = resolvers
        self._config = config

    # ── 目录装配 ──

    def list_capabilities(self, actor_snapshot: dict, session_context, *,
                          allowed_sops: set | None = None) -> list:
        """产出按当前操作者权限裁剪后的能力目录。

        Args:
            actor_snapshot: 当前操作者快照（含 user_id / level / is_management_unit）。
            session_context: SessionContext（取 available_tools / sop_allow）。
            allowed_sops: 灰度准入清单（None = 全部放开）；由 M8 SessionPathRouter 提供。
        """
        from ..workitem.langgraph_engine.agent.tool_adapter import (
            _session_api_ids, build_tool_specs,
        )
        from ..workitem.langgraph_engine.agent.fallback_policy import FallbackPolicy

        entries: list = []
        api_ids = _session_api_ids(_CtxShim(session_context))
        tier = FallbackPolicy.gate(actor_snapshot).value
        allowed_write = set(FallbackPolicy.resolve(tier, with_write=True) or [])

        if not api_ids:
            logger.warning("CapabilityCatalog: session_api_ids 为空 → fail-closed，"
                           "不暴露业务工具（仅 resolver + 控制工具）")

        # ── 1) 业务工具（查询 / 执行手脚）：复用现有权限过滤通道 ──
        base_specs = []
        if self._business_tools is not None and self._resolvers is not None:
            base_specs = build_tool_specs(
                self._business_tools, self._resolvers, api_ids,
                fallback_mode=False, fallback_tier=tier,
            )
        resolver_names = set()
        if self._resolvers is not None:
            try:
                resolver_names = {r.spec["function"]["name"] for r in self._resolvers.list_all()}
            except Exception as e:  # noqa: BLE001
                logger.debug("resolver name scan skipped: %s", e)

        for spec in base_specs:
            fn = spec.get("function") or {}
            name = fn.get("name") or ""
            if not name or name in _WI_CONTROL_NAMES:
                continue
            if self._business_tools is not None and name in self._business_tools:
                tool = self._business_tools.get(name)
                wm = (getattr(tool, "write_mode", "read") or "read")
                if wm in _HIGH_RISK_WRITE_MODES:
                    logger.debug("CapabilityCatalog: 高危能力 %s(%s) 不进自由工具集", name, wm)
                    continue
                if wm != "read" and name not in allowed_write:
                    logger.debug("CapabilityCatalog: 写能力 %s 不在 tier=%s 白名单", name, tier)
                    continue
                entries.append(CapabilityEntry(
                    name=name, kind="query" if wm == "read" else "write",
                    display_name=name,
                    description=getattr(tool, "description", "") or fn.get("description", ""),
                    write_mode=wm, parameters=getattr(tool, "parameters", None) or {},
                ))
            elif name in resolver_names:
                entries.append(CapabilityEntry(
                    name=name, kind="resolver", display_name=name,
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters") or {},
                ))

        # ── 2) SOP 能力（一 SOP 一能力）：按 sop_allow ∩ 灰度准入清单 ──
        sop_allow = set(getattr(session_context, "sop_allow", None) or [])
        if not sop_allow:
            logger.warning("CapabilityCatalog: sop_allow 为空 → fail-closed，不暴露 SOP 能力")
        if self._capability_registry is not None:
            for cap in self._capability_registry.list_all():
                if allowed_sops is not None and cap.name not in allowed_sops:
                    continue
                if cap.name not in sop_allow:
                    continue
                entries.append(CapabilityEntry(
                    name=cap.name, kind="sop", sop_id=cap.sop_id,
                    display_name=cap.display_name or cap.name,
                    description=cap.description or "",
                    write_mode="transition",
                    parameters=(getattr(cap.tool, "parameters", None) or {}),
                ))

        # ── 3) 控制工具（确认 / 取消）：非业务能力，始终可见 ──
        try:
            from .confirm_dialog import CONTROL_TOOL_SPECS
            for spec in CONTROL_TOOL_SPECS:
                fn = spec.get("function") or {}
                entries.append(CapabilityEntry(
                    name=fn.get("name", ""), kind="control",
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters") or {},
                ))
        except Exception as e:  # noqa: BLE001
            logger.warning("CapabilityCatalog: control tools 装配失败: %s", e)

        if len(entries) > CAPABILITY_TWO_STAGE_THRESHOLD:
            logger.warning(
                "CapabilityCatalog: 能力数 %d 超过阈值 %d —— 需评估两段式加载（当前全量暴露）",
                len(entries), CAPABILITY_TWO_STAGE_THRESHOLD,
            )
        logger.info("CapabilityCatalog: %d capabilities assembled (tier=%s, sop_allow=%d, allowed_sops=%s)",
                    len(entries), tier, len(sop_allow),
                    "all" if allowed_sops is None else len(allowed_sops))
        return entries

    # ── 派生视图 ──

    def build_tool_specs(self, actor_snapshot: dict, session_context, *,
                         allowed_sops: set | None = None) -> list:
        """产出 LLM 可见工具集（OpenAI function 格式）。"""
        specs = []
        for e in self.list_capabilities(actor_snapshot, session_context, allowed_sops=allowed_sops):
            if not e.name:
                continue
            specs.append({
                "type": "function",
                "function": {
                    "name": e.name,
                    "description": e.description,
                    "parameters": e.parameters or {"type": "object", "properties": {}},
                },
            })
        return specs

    def list_capability_names(self, actor_snapshot: dict, session_context, *,
                              allowed_sops: set | None = None) -> set:
        """能力名集合（供执行期白名单校验与粗排候选集）。"""
        return {
            e.name for e in
            self.list_capabilities(actor_snapshot, session_context, allowed_sops=allowed_sops)
            if e.name
        }


def build_catalog(core=None) -> CapabilityCatalog:
    """构造目录装配器（供 M1 注入）。"""
    return CapabilityCatalog(core=core)
