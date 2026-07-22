"""统一的数据路径解析工具。

三级探测：config 显式值 → 容器内路径(/app/...) → 开发环境回退(emily-data/...)。
"""
from pathlib import Path

# 项目根目录：infrastructure/ → emily_core/ → emily-core/ → Emily/
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_data_path(
    config_value: str,
    container_path: str,
    dev_relative: str,
) -> str:
    """解析数据存储路径。

    Args:
        config_value: Config 中显式配置的路径（优先级最高，非空即用）。
        container_path: 容器内默认绝对路径（如 /app/journal/项目日志.md）。
        dev_relative: 开发环境相对项目根的路径（如 emily-data/journal/项目日志.md）。

    Returns:
        最终生效的绝对路径字符串。
    """
    if config_value:
        return config_value
    cp = Path(container_path)
    if cp.parent.exists():  # 容器内目录存在 → 容器路径
        return str(cp)
    return str(_PROJECT_ROOT / dev_relative)
