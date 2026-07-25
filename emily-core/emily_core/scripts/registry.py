"""ScriptRegistry — 从声明式 YAML 清单加载脚本注册信息。

调用方式：
    from emily_core.scripts.registry import load_registry
    reg = load_registry()
    for entry in reg.entries:
        print(entry.name)
"""

from __future__ import annotations

import logging
from pathlib import Path

from .script_entry import ScriptEntry

logger = logging.getLogger("emily.scripts.registry")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # scripts/ → emily_core/ → emily-core/ → Emily/


class ScriptRegistry:
    """脚本注册表 —— 从 YAML 清单加载，提供按需查询。"""

    def __init__(self, entries: list[ScriptEntry], aggregations: dict | None = None,
                 prologue: str = "", epilogue: str = ""):
        self._entries: dict[str, ScriptEntry] = {e.name: e for e in entries}
        self.aggregations = aggregations or {}
        self.prologue = prologue
        self.epilogue = epilogue

    @property
    def entries(self) -> list[ScriptEntry]:
        """返回所有脚本条目列表。"""
        return list(self._entries.values())

    @property
    def count(self) -> int:
        return len(self._entries)

    def get(self, name: str) -> ScriptEntry | None:
        """按名称获取脚本条目。"""
        return self._entries.get(name)

    def has(self, name: str) -> bool:
        """检查脚本是否在注册表中。"""
        return name in self._entries

    def entries_with_auto_run(self, trigger: str) -> list[ScriptEntry]:
        """获取指定 auto_run 触发类型的脚本。"""
        return [e for e in self._entries.values() if e.auto_run == trigger]

    def __len__(self) -> int:
        return self.count


def load_registry(yaml_path: str | None = None) -> ScriptRegistry:
    """从 YAML 清单加载脚本注册表。

    三级路径探测：显式路径 → 容器 /app/data/config/ → 开发 emily-data/config/

    Args:
        yaml_path: 显式指定 YAML 路径（优先级最高）。None 时自动探测。

    Returns:
        ScriptRegistry 实例。
    """
    if yaml_path is None:
        yaml_path = _resolve_registry_path()

    import yaml
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"scripts_registry.yaml must be a dict, got {type(raw)}")

    scripts_section = raw.get("scripts", {})
    aggregations = raw.get("aggregations", {})
    prologue = raw.get("prologue", "")
    epilogue = raw.get("epilogue", "")

    entries = []
    for name, data in scripts_section.items():
        if not isinstance(data, dict):
            logger.warning("scripts_registry: skipping non-dict entry '%s'", name)
            continue
        try:
            entry = ScriptEntry(
                name=name,
                description=data.get("description", ""),
                category=data.get("category", "system_maintenance"),
                source_path=data.get("source_path", f"scripts/{name}.py"),
                invocation=data.get("invocation", f"uv run python scripts/{name}.py {{args}}"),
                check_arg=data.get("check_arg"),
                run_args=data.get("run_args", []),
                auto_run=data.get("auto_run"),
                auto_run_args=data.get("auto_run_args", []),
                writes_db=data.get("writes_db", False),
                aggregation_parent=data.get("aggregation_parent"),
                status=data.get("status", "active"),
                entrypoint=data.get("entrypoint"),
                timeout_seconds=data.get("timeout_seconds", 60),
                flow_note=data.get("flow_note"),
                scheduling_note=data.get("scheduling_note"),
            )
            entries.append(entry)
        except Exception as e:
            logger.warning("scripts_registry: failed to parse entry '%s': %s", name, e)

    logger.info("script_registry: loaded %d scripts from %s", len(entries), yaml_path)
    return ScriptRegistry(entries, aggregations, prologue, epilogue)


def _resolve_registry_path() -> str:
    """三级路径探测：容器 → emily-data/config → 失败回退。"""
    # Try container path first
    container_path = Path("/app/data/config/scripts_registry.yaml")
    if container_path.exists():
        return str(container_path)

    # Try dev path
    dev_path = _PROJECT_ROOT / "emily-data" / "config" / "scripts_registry.yaml"
    if dev_path.exists():
        return str(dev_path)

    raise FileNotFoundError(
        "scripts_registry.yaml not found at container path /app/data/config/ "
        "or dev path emily-data/config/"
    )
