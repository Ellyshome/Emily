# emily-core/emily_core/session/capability_contract.py
"""M2 能力契约模块 —— 查询 / 写入 / SOP 三类能力的统一契约与注册表。

定位（计划 M2 / PRD US-03、US-08）：
  - 契约是目录装配、计划校验、门禁判定的**唯一数据源**（PRD 约束 6、宪法 C10）
  - 缺参数 schema 的能力不进入可见集（AC-US-03.1）
  - 只做契约描述与装配，**不做权限判定**：权限仍走既有通道（PRD 约束 4）
  - 为 M4 的计划入参校验与 M6 的门禁判定提供类型化输入

两类入口：
  - `specs_from_entries()` / `full_specs_from_core()`：纯装配，可独立单测
  - `build_specs(core, actor_snapshot, session_context, ...)`：按操作者裁剪的可见契约

模块级只依赖标准库；对既有模块的 import 一律放在函数内延迟执行，便于独立导入与自测。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger("emily.session.capability_contract")

# ── 契约类别 ──
KIND_QUERY = "query"        # 只读能力：查询 / 检索 / 列举
KIND_WRITE = "write"        # 写入能力：追加 / 迁移 / 覆盖 / 删除
KIND_SOP = "sop"            # 业务流能力：一个 SOP 一个能力
KIND_CONTROL = "control"    # 控制工具：确认 / 取消类，不属三类业务能力

#: 三类业务能力（PRD US-02 的计划覆盖范围）
BUSINESS_KINDS = (KIND_QUERY, KIND_WRITE, KIND_SOP)
KINDS = (KIND_QUERY, KIND_WRITE, KIND_SOP, KIND_CONTROL)

#: 视为只读的写模式取值（含空值，与既有 write_mode 默认一致）
_READ_MODES = frozenset({"", "read"})

#: 既有能力目录条目中不作为模型可见能力的类别
_SKIPPED_ENTRY_KINDS = frozenset({"resolver"})


def derive_perm_scope(kind: str, write_mode: str) -> str:
    """由类别与写模式派生权限归属标签（供门禁与裁剪核对，不参与判定）。"""
    if kind == KIND_SOP:
        return "sop"
    if kind == KIND_CONTROL:
        return "control"
    return "read" if str(write_mode or "read") in _READ_MODES else "write"


@dataclass
class CapabilitySpec:
    """单条能力契约。

    Attributes:
        name: 能力名（= 模型可见工具名）
        kind: query | write | sop | control
        params_schema: JSON Schema 参数约束（缺省即不具备模型可见资格）
        write_mode: read / append / transition / overwrite / delete
        perm_scope: 权限归属标签（read / write / sop / control）
        source: 契约来源（工具注册表 / SOP 索引）
        sop_id: SOP 能力对应的短形编号（如 SOP-002-REC）
        description / display_name: 描述性字段，供目录渲染
    """

    name: str
    kind: str
    params_schema: dict = field(default_factory=dict)
    write_mode: str = "read"
    perm_scope: str = ""
    source: str = ""
    description: str = ""
    sop_id: str = ""
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"CapabilitySpec.kind 非法：{self.kind!r}（允许 {KINDS}）")
        if not self.perm_scope:
            self.perm_scope = derive_perm_scope(self.kind, self.write_mode)

    @property
    def is_business(self) -> bool:
        """是否为三类业务能力之一（控制工具不算）。"""
        return self.kind in BUSINESS_KINDS

    def has_schema(self) -> bool:
        """模型可见资格判定（宪法 C10 / AC-US-03.1）。

        SOP 能力的入参为「请求原文 + SOP 声明参数」，其参数约束在 SOP 声明中，
        不要求 JSON Schema；其余能力必须带非空参数 schema。
        """
        if self.kind == KIND_SOP:
            return True
        return bool(self.params_schema)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "params_schema": self.params_schema,
            "write_mode": self.write_mode,
            "perm_scope": self.perm_scope,
            "source": self.source,
            "sop_id": self.sop_id,
            "description": self.description,
            "display_name": self.display_name,
        }


class CapabilityContractRegistry:
    """契约注册表 —— 线程安全，幂等注册，重名以首次为准。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict = {}

    def register(self, spec: CapabilitySpec) -> bool:
        """注册单条契约；重名跳过并告警（返回 False）。"""
        with self._lock:
            if spec.name in self._specs:
                logger.warning("CapabilityContract: 重名契约 %s 已存在，跳过后者", spec.name)
                return False
            self._specs[spec.name] = spec
            return True

    def register_many(self, specs) -> int:
        """批量注册，返回新增条数。"""
        added = 0
        for spec in specs or []:
            if self.register(spec):
                added += 1
        return added

    def get(self, name: str):
        with self._lock:
            return self._specs.get(name)

    def list_all(self, *, kind: "str | None" = None, business_only: bool = False) -> list:
        """列举契约（按名称排序，输出稳定）。"""
        with self._lock:
            items = list(self._specs.values())
        if kind is not None:
            items = [s for s in items if s.kind == kind]
        if business_only:
            items = [s for s in items if s.is_business]
        return sorted(items, key=lambda s: s.name)

    def names(self, *, business_only: bool = False) -> list:
        return [s.name for s in self.list_all(business_only=business_only)]

    def missing_schema(self) -> list:
        """列出不具备模型可见资格（缺 schema）的能力名。"""
        return sorted(s.name for s in self.list_all() if not s.has_schema())

    def clear(self) -> None:
        with self._lock:
            self._specs.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._specs)


#: 进程级单例（与既有 capability_registry 的装配点分离，供 M3 注入）
_default_registry = CapabilityContractRegistry()


def get_contract_registry() -> CapabilityContractRegistry:
    return _default_registry


# ══════════════════════════════════════════════════════════════════════════════
# 装配：既有能力目录条目 → 契约
# ══════════════════════════════════════════════════════════════════════════════


def _normalize_write_mode(raw) -> str:
    """兼容 WriteMode 枚举与字符串两种取值。"""
    value = getattr(raw, "value", raw)
    return str(value or "read")


def spec_from_entry(entry) -> "CapabilitySpec | None":
    """把既有能力目录条目映射为契约（duck-typed，缺字段按保守值）。

    返回 None 表示该条目不作为模型可见能力（如参数解析器）。
    """
    kind = str(getattr(entry, "kind", "") or "")
    if kind in _SKIPPED_ENTRY_KINDS:
        return None
    mapped_kind = kind if kind in KINDS else KIND_WRITE
    name = str(getattr(entry, "name", "") or "")
    if not name:
        return None
    schema = getattr(entry, "parameters", None) or getattr(entry, "params_schema", None) or {}
    return CapabilitySpec(
        name=name,
        kind=mapped_kind,
        params_schema=dict(schema) if isinstance(schema, dict) else {},
        write_mode=_normalize_write_mode(getattr(entry, "write_mode", "read")),
        source=str(getattr(entry, "source", "") or "catalog"),
        description=str(getattr(entry, "description", "") or ""),
        sop_id=str(getattr(entry, "sop_id", "") or ""),
        display_name=str(getattr(entry, "display_name", "") or ""),
    )


def specs_from_entries(entries) -> list:
    """批量映射（纯函数，无外部依赖，可独立单测）。"""
    specs = []
    for entry in entries or []:
        spec = spec_from_entry(entry)
        if spec is not None:
            specs.append(spec)
    return specs


def _safe_get(registry, name: str):
    getter = getattr(registry, "get", None)
    if not callable(getter):
        return None
    try:
        return getter(name)
    except Exception as e:  # noqa: BLE001
        logger.debug("tool registry get(%s) failed: %s", name, e)
        return None


def _objects_from_items(registry, items) -> list:
    """把访问器返回值规整为工具对象列表（兼容 dict、对象、名称字符串三种形态）。"""
    if isinstance(items, dict):
        return [v for v in items.values() if v is not None and not isinstance(v, str)]
    objects = []
    for item in items or []:
        if item is None:
            continue
        if isinstance(item, str):
            obj = _safe_get(registry, item)
            if obj is not None:
                objects.append(obj)
        else:
            objects.append(item)
    return objects


def _iter_tool_objects(registry) -> list:
    """从既有工具注册表取工具对象。

    真实注册表（BusinessFlowToolRegistry）只提供 `list_names()` 与 `get(name)`，
    因此名称访问器是主路径；对象访问器作为兼容分支保留。
    """
    if registry is None:
        return []

    # 1) 直接返回对象的访问器
    for accessor in ("list_all", "all_tools", "values", "specs"):
        fn = getattr(registry, accessor, None)
        if not callable(fn):
            continue
        try:
            items = fn()
        except Exception as e:  # noqa: BLE001
            logger.debug("tool registry accessor %s failed: %s", accessor, e)
            continue
        objects = _objects_from_items(registry, items)
        if objects:
            return objects

    # 2) 名称访问器 + get()（真实注册表路径）
    for accessor in ("list_names", "names", "keys"):
        fn = getattr(registry, accessor, None)
        if not callable(fn):
            continue
        try:
            names = list(fn())
        except Exception as e:  # noqa: BLE001
            logger.debug("tool registry accessor %s failed: %s", accessor, e)
            continue
        objects = [obj for obj in (_safe_get(registry, n) for n in names) if obj is not None]
        if objects:
            return objects

    # 3) 可迭代注册表
    try:
        names = list(registry)
    except Exception:  # noqa: BLE001
        return []
    return [obj for obj in (_safe_get(registry, n) for n in names) if obj is not None]


def _spec_from_tool_object(tool) -> "CapabilitySpec | None":
    """工具对象 → 契约（兼容对象与 OpenAI 格式 dict 两种形态）。"""
    if isinstance(tool, dict):
        fn_spec = tool.get("function") or {}
        name = str(fn_spec.get("name") or tool.get("name") or "")
        if not name:
            return None
        schema = fn_spec.get("parameters") or tool.get("params_schema") or {}
        write_mode = _normalize_write_mode(tool.get("write_mode", "read"))
        kind = KIND_QUERY if write_mode in _READ_MODES else KIND_WRITE
        return CapabilitySpec(
            name=name,
            kind=kind,
            params_schema=dict(schema) if isinstance(schema, dict) else {},
            write_mode=write_mode,
            source="tool_registry",
            description=str(fn_spec.get("description") or tool.get("description") or ""),
        )

    name = str(getattr(tool, "name", "") or getattr(tool, "tool_name", "") or "")
    if not name:
        return None
    schema = (
        getattr(tool, "params", None)
        or getattr(tool, "parameters", None)
        or getattr(tool, "params_schema", None)
        or {}
    )
    write_mode = _normalize_write_mode(getattr(tool, "write_mode", "read"))
    kind = KIND_QUERY if write_mode in _READ_MODES else KIND_WRITE
    return CapabilitySpec(
        name=name,
        kind=kind,
        params_schema=dict(schema) if isinstance(schema, dict) else {},
        write_mode=write_mode,
        source="tool_registry",
        description=str(getattr(tool, "description", "") or ""),
    )


def _sop_specs_from_registry(skill_registry) -> list:
    """SOP 索引 → SOP 能力契约（排除系统内部 SOP）。"""
    if skill_registry is None:
        return []
    try:
        from .capability_runner import is_capability_sop, short_sop_id
    except Exception as e:  # noqa: BLE001 — 准入判定不可用时 fail-closed
        logger.warning("CapabilityContract: 准入判定不可用，跳过 SOP 契约：%s", e)
        return []
    try:
        docs = skill_registry.list_skills()
    except Exception as e:  # noqa: BLE001
        logger.warning("CapabilityContract: 列举 SOP 失败：%s", e)
        return []
    specs = []
    for doc in docs or []:
        stem = str(getattr(doc, "sop_id", "") or getattr(doc, "stem", "") or "")
        if not is_capability_sop(stem):
            continue
        specs.append(
            CapabilitySpec(
                name=stem,
                kind=KIND_SOP,
                params_schema={},
                write_mode="append",
                sop_id=short_sop_id(stem),
                source="sop_index",
                description=str(getattr(doc, "first_instruction", "") or ""),
                display_name=str(getattr(doc, "display_name", "") or ""),
            )
        )
    return specs


def full_specs_from_core(core) -> list:
    """全量契约（不做操作者裁剪），供启动注册使用。"""
    if core is None:
        return []
    specs = []
    for tool in _iter_tool_objects(getattr(core, "_business_flow_tools", None)):
        spec = _spec_from_tool_object(tool)
        if spec is not None:
            specs.append(spec)
    specs.extend(_sop_specs_from_registry(getattr(core, "_skill_registry", None)))
    specs.extend(_control_specs())
    return specs


def _control_specs() -> list:
    """控制工具契约（确认 / 取消），单列一类，不计入三类业务能力。"""
    return [
        CapabilitySpec(
            name="confirm_pending",
            kind=KIND_CONTROL,
            params_schema={
                "type": "object",
                "properties": {"event_no": {"type": "string"}},
                "required": [],
            },
            write_mode="transition",
            source="control",
            description="确认待确认的业务对象",
        ),
        CapabilitySpec(
            name="cancel_pending",
            kind=KIND_CONTROL,
            params_schema={
                "type": "object",
                "properties": {"event_no": {"type": "string"}},
                "required": [],
            },
            write_mode="transition",
            source="control",
            description="取消待确认的业务对象",
        ),
    ]


def register_contracts(core) -> int:
    """启动注册：全量契约写入注册表（幂等，返回新增条数）。"""
    registry = get_contract_registry()
    added = registry.register_many(full_specs_from_core(core))
    logger.info("CapabilityContract: 注册 %d 条契约（表内共 %d 条）", added, len(registry))
    return added


def build_specs(core, actor_snapshot: dict, session_context, *, allowed_sops=None) -> list:
    """按当前操作者裁剪后的可见契约（供目录装配与计划校验消费）。

    复用既有权限过滤通道（`CapabilityCatalog.list_capabilities`，fail-closed），
    本函数不新造权限判定（PRD 约束 4）。
    """
    try:
        from .capability_catalog import CapabilityCatalog
    except Exception as e:  # noqa: BLE001
        logger.warning("CapabilityContract.build_specs: 目录模块不可用：%s", e)
        return []
    catalog = CapabilityCatalog(core=core)
    entries = catalog.list_capabilities(
        actor_snapshot, session_context, allowed_sops=allowed_sops
    )
    return specs_from_entries(entries)
