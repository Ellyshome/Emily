"""统一的数据路径解析工具。

三级探测：config 显式值 → 容器内路径(/app/...) → 宿主机路径。

宿主机路径分两类，物理位置不同：
  · 内容层（sops / prompts / config / rules / node_templates …）在仓库内
    `emily-data/` 下，随代码版本走，部署时必须与代码同版本；
  · 运行态（attachments / logs / runtime / journal / user_memory …
    ）在仓库**同级**的 `Emily-var/` 下，不随代码走、不入 git、不进部署包。

容器内路径不受影响：compose 把这些目录分别挂到 /app/<name>。
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger("emily.paths")

# 项目根目录：infrastructure/ → emily_core/ → emily-core/ → Emily/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 运行态数据根：与代码仓库同级（Emily/ 与 Emily-var/ 并列）
_VAR_ROOT = _PROJECT_ROOT.parent / "Emily-var"

# 属运行态的 emily-data 一级子目录名；命中即改到 Emily-var/ 下解析。
# 新增运行态目录时在此登记，否则会被当作内容层留在仓库内。
_RUNTIME_SUBDIRS = frozenset({
    "attachments", "session_archives", "logs", "journal", "notebooks",
    "user_memory", "runtime", "evolution", "state",
    "napcat_qq", "napcat_config", "postgres_data", "db_seeds", "hf_cache",
})


def var_root() -> Path:
    """运行态数据根目录（与代码仓库同级的 Emily-var/）。"""
    return _VAR_ROOT


def var_dir(name: str) -> Path:
    """运行态子目录路径（不创建目录）。"""
    return _VAR_ROOT / name


def resolve_data_path(
    config_value: str,
    container_path: str,
    dev_relative: str,
) -> str:
    """解析数据存储路径。

    Args:
        config_value: Config 中显式配置的路径（优先级最高，非空即用）。
        container_path: 容器内默认绝对路径（如 /app/journal/项目日志.md）。
        dev_relative: 开发环境相对项目根的路径（如 emily-data/journal/项目日志.md）；
            若首段为 emily-data 且第二段属运行态目录，宿主机改解析到 Emily-var/。

    Returns:
        最终生效的绝对路径字符串。
    """
    if config_value:
        return config_value
    cp = Path(container_path)
    # Windows 上 "/app/logs" 会被解析成当前盘符下的 "\\app\\logs"，若宿主恰好有
    # D:\\app 就会误判为"容器内"并写错位置；容器路径只可能在 Linux 容器里成立。
    if os.name != "nt" and cp.parent.exists():  # 容器内目录存在 → 容器路径
        return str(cp)

    rel = Path(dev_relative)
    if len(rel.parts) > 1 and rel.parts[0] == "emily-data" and rel.parts[1] in _RUNTIME_SUBDIRS:
        target = _VAR_ROOT.joinpath(*rel.parts[1:])
        legacy = _PROJECT_ROOT / dev_relative
        if not target.exists() and legacy.exists():
            logger.warning(
                "运行态数据仍在旧位置 %s，应迁移到 %s（本次按旧路径继续）", legacy, target,
            )
            return str(legacy)
        return str(target)

    return str(_PROJECT_ROOT / dev_relative)
