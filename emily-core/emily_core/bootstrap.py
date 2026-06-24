"""bootstrap —— Emily Core 初始化入口。

负责加载配置、初始化日志、初始化数据库（自动建表）、创建 EmilyCore 实例。
容器化后：配置主要来自环境变量（EMILY_DATABASE_URL / EMILY_LLM_* / EMILY_MAXKB_* 等），
由 api 层在启动时读取并传入。
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from .config import Config

BEIJING_TZ = timezone(timedelta(hours=8))

_logger = logging.getLogger("emily.bootstrap")


def _setup_logging(config: Config) -> None:
    """配置日志输出（控制台 + 可选文件）。"""
    root_logger = logging.getLogger("emily")
    root_logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        console.setFormatter(fmt)
        root_logger.addHandler(console)

    if config.log_to_file:
        try:
            os.makedirs(config.log_dir, exist_ok=True)
            date_str = datetime.now(BEIJING_TZ).strftime("%Y%m%d")
            log_file = os.path.join(config.log_dir, f"emily_{date_str}.log")
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(fmt)
            root_logger.addHandler(file_handler)
        except Exception as e:  # 文件日志失败不应阻断启动
            _logger.warning("File logging disabled: %s", e)


def _config_from_env(config_data: dict | None) -> dict:
    """从环境变量补全配置（容器化部署主路径）。"""
    data = dict(config_data or {})
    env_map = {
        "EMILY_DATABASE_URL": "database_url",
        "EMILY_LLM_API_KEY": "llm_api_key",
        "EMILY_LLM_BASE_URL": "llm_base_url",
        "EMILY_LLM_MODEL": "llm_model",
        "EMILY_STORAGE_ROOT": "storage_root",
        "EMILY_HOOK_CONFIG_PATH": "hook_config_path",
        "EMILY_SOP_REPOSITORY_DIR": "sop_repository_dir",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val and not data.get(cfg_key):
            data[cfg_key] = val
    return data


def init(config_data: dict | None = None, rag_provider=None) -> "EmilyCore":
    """初始化 Emily Core。

    Args:
        config_data: 可选配置字典，覆盖默认值（api 层可传环境变量解析结果）。
        rag_provider: RAG 知识库检索提供者（可选，提前创建后注入）。

    Returns:
        EmilyCore: 初始化完毕的内核实例。
    """
    from . import EmilyCore

    config = Config.from_dict(_config_from_env(config_data))
    _setup_logging(config)

    # 初始化 PostgreSQL（自动建表，幂等）
    from .infrastructure.database.session import init_db, get_db_path
    db_url = config.database_url if config.database_url else None
    init_db(db_url)
    _logger.info("Database ready: %s", get_db_path())

    _logger.info(
        "Emily Core initialized, mode=%s, bot_name=%s, llm=%s",
        config.takeover_mode,
        config.bot_name,
        "configured" if config.llm_api_key else "disabled (Mock brain)",
    )
    return EmilyCore(config, rag_provider=rag_provider)
