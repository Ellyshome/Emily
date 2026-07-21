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
        "EMILY_MAXKB_URL": "maxkb_url",
        "EMILY_MAXKB_ADMIN_PASSWORD": "maxkb_admin_password",
        "EMILY_MAXKB_KNOWLEDGE_ID": "maxkb_knowledge_id",
        "EMILY_KB_ENABLED": "kb_enabled",
        "EMILY_PROMPTS_DIR": "prompts_dir",
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

    # 初始化 RAG Provider（如果 kb_enabled + maxkb 配置了）
    if rag_provider is None and config.kb_enabled:
        try:
            from .providers.rag.maxkb_provider import MaxKBRagProvider
            if config.maxkb_admin_password and config.maxkb_knowledge_id:
                rag_provider = MaxKBRagProvider(
                    base_url=config.maxkb_url,
                    admin_password=config.maxkb_admin_password,
                    knowledge_id=config.maxkb_knowledge_id,
                )
                _logger.info("MaxKB RAG provider created: kb=%s", config.maxkb_knowledge_id[:8])
        except Exception as e:
            _logger.warning("MaxKB RAG provider init failed: %s", e)

    # ── 基座工具就绪检查 ──
    _check_base_tools_readiness(rag_provider)

    _logger.info(
        "Emily Core initialized, mode=%s, bot_name=%s, llm=%s, kb=%s",
        config.takeover_mode,
        config.bot_name,
        "configured" if config.llm_api_key else "disabled",
        "enabled" if rag_provider else "disabled",
    )

    core = EmilyCore(config, rag_provider=rag_provider)

    # 启动后异步发送冷启动邮件通知（fail-open，不阻塞启动）
    import asyncio
    try:
        _loop = asyncio.get_running_loop()
        _loop.create_task(_send_startup_email(core, config))
    except RuntimeError:
        pass  # 无事件循环时跳过

    return core


def _check_base_tools_readiness(rag_provider) -> None:
    """基座工具就绪检查：验证 query_data + knowledge_search 依赖是否可用（fail-open）。"""
    checks = {}

    # 1. query_data — 依赖数据库连接
    try:
        from .infrastructure.database.session import get_session
        from .infrastructure.database.models import Project
        with get_session() as session:
            count = session.query(Project).count()
        checks["query_data"] = f"ok (projects={count})"
    except Exception as e:
        checks["query_data"] = f"degraded ({e})"

    # 2. knowledge_search — 依赖 RAG Provider
    if rag_provider is not None:
        checks["knowledge_search"] = "ok (rag connected)"
    else:
        checks["knowledge_search"] = "stub (kb disabled)"

    # 3. 工具目录存在性
    import importlib
    for mod_name in ("query_tool", "knowledge_search_tool"):
        try:
            importlib.import_module(f".tools.{mod_name}", package="emily_core")
            checks[f"tool:{mod_name}"] = "found"
        except ImportError:
            checks[f"tool:{mod_name}"] = "missing"

    status_parts = [f"{k}={v}" for k, v in checks.items()]
    _logger.info("Base tools readiness: %s", "; ".join(status_parts))


async def _send_startup_email(core: "EmilyCore", config: Config) -> None:
    """冷启动邮件通知：向 EMILY_EMAIL_IDKEY 邮箱发送启动完成邮件（fail-open）。"""
    email_idkey = os.environ.get("EMILY_EMAIL_IDKEY", "")
    email_password = os.environ.get("EMILY_EMAIL_PASSWORD", "")
    if not email_idkey or not email_password:
        _logger.info("Startup email skipped: EMILY_EMAIL_IDKEY / EMILY_EMAIL_PASSWORD not set")
        return

    try:
        from .providers.email.base import EmailCredentials
        from .providers.email.smtp_provider import SMTPEmailProvider
        from .services.email_service import EmailService

        creds = EmailCredentials(
            smtp_host=getattr(config, "email_smtp_host", "smtp.qq.com"),
            smtp_port=getattr(config, "email_smtp_port", 465),
            imap_host=getattr(config, "email_imap_host", "imap.qq.com"),
            imap_port=getattr(config, "email_imap_port", 993),
            username=email_idkey,
            password=email_password,
            use_ssl=True,
        )

        smtp = SMTPEmailProvider()
        email_service = EmailService(smtp=smtp, imap=None)

        now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        subject = "[Emily] 系统启动完成"
        body = f"Emily 系统已启动。\n启动时间：{now_str}\n"

        result = await email_service.send(
            creds=creds,
            to=email_idkey,
            subject=subject,
            body=body,
        )
        if result.success:
            _logger.info("Startup email sent to %s", email_idkey)
        else:
            _logger.warning("Startup email failed: %s", result.error)

    except Exception as e:
        _logger.warning("Startup email init failed: %s", e)
