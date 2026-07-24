"""bootstrap —— Emily Core 初始化入口。

负责加载配置、初始化日志、初始化数据库（自动建表）、创建 EmilyCore 实例。
容器化后：配置主要来自环境变量（EMILY_DATABASE_URL / EMILY_LLM_* / EMILY_MAXKB_* 等），
由 api 层在启动时读取并传入。
"""

import json
import logging
import os
import socket
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
    # 布尔字段：环境变量为字符串，需显式转换
    bool_fields = {"llm_console_trace_enabled", "kb_enabled"}
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val and not data.get(cfg_key):
            if cfg_key in bool_fields:
                data[cfg_key] = val.strip().lower() in ("1", "true", "yes", "on")
            else:
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

    started_monotonic = time.monotonic()
    started_at = datetime.now(BEIJING_TZ)

    config = Config.from_dict(_config_from_env(config_data))
    _setup_logging(config)

    # 初始化 PostgreSQL（自动建表，幂等）
    from .infrastructure.database.session import init_db, get_db_path
    from .infrastructure.database.models import Base
    db_url = config.database_url if config.database_url else None
    migrations: list[dict] = []
    db_ready = False
    try:
        migrations = init_db(db_url)
        db_ready = True
    except Exception as e:
        _logger.error("Database init failed: %s", e)
    _logger.info("Database ready: %s", get_db_path())

    # 初始化 RAG Provider（如果 kb_enabled + maxkb 配置了）
    if rag_provider is None and config.kb_enabled:
        try:
            from .providers.rag.maxkb_provider import MaxKBRagProvider
            if config.maxkb_admin_password:
                rag_provider = MaxKBRagProvider(
                    base_url=config.maxkb_url,
                    admin_password=config.maxkb_admin_password,
                )
                _logger.info("MaxKB RAG provider created")
        except Exception as e:
            _logger.warning("MaxKB RAG provider init failed: %s", e)

    # ── 基座工具就绪检查 ──
    base_tools = _check_base_tools_readiness(rag_provider)

    _logger.info(
        "Emily Core initialized, mode=%s, bot_name=%s, llm=%s, kb=%s",
        config.takeover_mode,
        config.bot_name,
        "configured" if config.llm_api_key else "disabled",
        "enabled" if rag_provider else "disabled",
    )

    core = EmilyCore(config, rag_provider=rag_provider)

    # ── 组装启动报告 ──
    llm_configured = bool(config.llm_api_key)
    rag_enabled = rag_provider is not None
    smtp_configured = bool(os.environ.get("EMILY_EMAIL_IDKEY"))

    degradations: list[str] = []
    if not llm_configured:
        degradations.append("LLM 未配置 — 语义理解/自主规划不可用")
    if not rag_enabled:
        degradations.append("知识库 RAG 未配置 — 知识检索不可用")
    if not smtp_configured:
        degradations.append("SMTP 未配置 — 邮件通知不可用")
    if not db_ready:
        degradations.append("数据库未就绪 — 持久化功能不可用")

    startup_report = {
        "started_at": started_at,
        "duration_s": time.monotonic() - started_monotonic,
        "hostname": socket.gethostname(),
        "env": os.environ.get("EMILY_ENV", "unspecified"),
        "db_ready": db_ready,
        "db_tables": len(Base.metadata.tables) if db_ready else 0,
        "migrations": migrations,
        "llm_configured": llm_configured,
        "llm_model": config.llm_model or "unspecified",
        "rag_enabled": rag_enabled,
        "smtp_configured": smtp_configured,
        "base_tools": base_tools,
        "degradations": degradations,
        "config_summary": {
            "takeover_mode": config.takeover_mode,
            "bot_name": config.bot_name,
            "kb_enabled": config.kb_enabled,
        },
    }

    # 启动后异步发送冷启动邮件通知（fail-open，不阻塞启动）
    import asyncio
    try:
        _loop = asyncio.get_running_loop()
        _loop.create_task(_send_startup_email(core, config, startup_report))
    except RuntimeError:
        pass  # 无事件循环时跳过

    return core


def _check_base_tools_readiness(rag_provider) -> dict:
    """基座工具就绪检查：验证 query_data + knowledge_search 依赖是否可用（fail-open）。

    Returns:
        dict: 检查结果，如 {"query_data": "ok (projects=5)", ...}
    """
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
    return checks


def _collect_system_snapshot() -> dict:
    """采集系统快照（用户/项目/业务量/世界书/SOP）。fail-open，失败返回 {}。"""
    try:
        from .infrastructure.database.session import get_session
        from .infrastructure.database.models import User, Project, Event, Task, ProjectNode, ProjectWorldBook
        with get_session() as session:
            snapshot = {
                "users_active": session.query(User).filter(User.is_deleted == False, User.status == "active").count(),
                "users_total": session.query(User).filter(User.is_deleted == False).count(),
                "admins": session.query(User).filter(User.is_deleted == False, User.is_admin == True).count(),
                "projects_active": session.query(Project).filter(Project.is_deleted == False, Project.status == "active").count(),
                "projects_total": session.query(Project).filter(Project.is_deleted == False).count(),
                "events": session.query(Event).count(),
                "tasks": session.query(Task).count(),
                "nodes": session.query(ProjectNode).filter(ProjectNode.is_discarded == False).count(),
                "world_books": session.query(ProjectWorldBook).count(),
                "world_books_activated": session.query(ProjectWorldBook).filter(ProjectWorldBook.is_activated == True).count(),
            }

        # SOP 计数：与 self_check.py 一致的 dev 回退路径
        sop_count = 0
        try:
            from .skill.registry import SkillRegistry
            skill_dir = "/app/skills"
            if not Path(skill_dir).exists():
                skill_dir = ""
            if not skill_dir:
                dev_dir = str(Path(__file__).resolve().parents[3] / "emily-data" / "skills")
                if Path(dev_dir).exists():
                    skill_dir = dev_dir
            if skill_dir:
                reg = SkillRegistry(skill_directory=skill_dir)
                reg.load()
                sop_count = len(reg.list_sop_ids())
        except Exception:
            pass
        snapshot["sops"] = sop_count

        return snapshot
    except Exception as e:
        _logger.warning("System snapshot collection failed: %s", e)
        return {}


def _read_last_startup() -> tuple[str | None, str | None]:
    """读取上次启动时间 (ISO) 和本次停机时长描述。fail-open。

    Returns:
        (last_startup_iso, downtime_str)：文件不存在时返回 (None, None)
    """
    try:
        from .infrastructure.paths import resolve_data_path
        state_dir = resolve_data_path("", "/app/data/state", "emily-data/state")
        state_file = Path(state_dir) / "last_startup.json"
        if not state_file.exists():
            return None, None
        data = json.loads(state_file.read_text(encoding="utf-8"))
        last_str = data.get("last_startup", "")
        downtime_str = data.get("downtime", "")
        return last_str or None, downtime_str or None
    except Exception as e:
        _logger.debug("Failed to read last startup: %s", e)
        return None, None


def _write_last_startup(started_at: datetime) -> None:
    """写入本次启动时间到 state 文件。fail-open。"""
    try:
        from .infrastructure.paths import resolve_data_path
        state_dir = resolve_data_path("", "/app/data/state", "emily-data/state")
        state_file = Path(state_dir) / "last_startup.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"last_startup": started_at.isoformat()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _logger.debug("Failed to write last startup: %s", e)


async def _send_startup_email(core: "EmilyCore", config: Config, startup_report: dict) -> None:
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

        started_at = startup_report.get("started_at")
        now_str = started_at.strftime("%Y-%m-%d %H:%M:%S") if started_at else "unknown"
        hostname = startup_report.get("hostname", "unknown")
        env = startup_report.get("env", "unspecified")
        duration = startup_report.get("duration_s", 0)

        # ── 构建邮件正文（每节独立 try/except） ──
        lines = []
        lines.append("Emily 系统启动完成 ✓")
        lines.append("──────────────────────────────")
        lines.append(f"启动时间：{now_str} (北京时间)")
        lines.append(f"主机：{hostname}")
        lines.append(f"环境：{env}")
        lines.append("启动耗时：{:.1f}s".format(duration))
        lines.append("")

        # 节 1：依赖连通性
        try:
            lines.append("═══ 依赖连通性 ═══")
            db_ready = startup_report.get("db_ready", False)
            db_tables = startup_report.get("db_tables", 0)
            if db_ready:
                lines.append(f"✓ PostgreSQL — emily-postgres:5432/emily ({db_tables} 表)")
            else:
                lines.append("✗ PostgreSQL — 连接失败")
            smtp_ok = startup_report.get("smtp_configured", False)
            lines.append(f"{'✓' if smtp_ok else '✗'} SMTP 邮箱 — {'已配置' if smtp_ok else '未配置'}")
            llm_ok = startup_report.get("llm_configured", False)
            llm_model = startup_report.get("llm_model", "unspecified")
            lines.append(f"{'✓' if llm_ok else '✗'} LLM — {llm_model} ({'已配置' if llm_ok else '未配置'})")
            rag_ok = startup_report.get("rag_enabled", False)
            lines.append(f"{'✓' if rag_ok else '✗'} RAG/MaxKB — {'已启用' if rag_ok else '未启用'}")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 2：降级警告
        try:
            lines.append("═══ 降级警告 ═══")
            degradations = startup_report.get("degradations", [])
            if degradations:
                for d in degradations:
                    lines.append(f"⚠ {d}")
            else:
                lines.append("无降级，全部依赖就绪")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 3：系统快照
        try:
            snapshot = _collect_system_snapshot()
            lines.append("═══ 系统快照 ═══")
            if snapshot:
                users_active = snapshot.get("users_active", 0)
                users_total = snapshot.get("users_total", 0)
                admins = snapshot.get("admins", 0)
                lines.append(f"用户：{users_active} 活跃 / {users_total} 总计 ({admins} 管理员)")

                projects_active = snapshot.get("projects_active", 0)
                projects_total = snapshot.get("projects_total", 0)
                lines.append(f"项目：{projects_active} 活跃 / {projects_total} 总计")

                events = snapshot.get("events", 0)
                tasks = snapshot.get("tasks", 0)
                nodes = snapshot.get("nodes", 0)
                lines.append(f"业务：{events} 事件 / {tasks} 任务 / {nodes} 节点")

                wb = snapshot.get("world_books", 0)
                wb_act = snapshot.get("world_books_activated", 0)
                lines.append(f"世界书：{wb} 份 / {wb_act} 已激活")

                sops = snapshot.get("sops", 0)
                lines.append(f"SOP：{sops} 个")
            else:
                lines.append("(快照不可用)")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 4：配置概要
        try:
            lines.append("═══ 配置概要 ═══")
            cs = startup_report.get("config_summary", {})
            lines.append(f"接管模式：{cs.get('takeover_mode', 'unknown')}")
            lines.append(f"机器人名称：{cs.get('bot_name', 'unknown')}")
            lines.append(f"LLM 模型：{startup_report.get('llm_model', 'unspecified')}")
            lines.append(f"知识库：{'on' if cs.get('kb_enabled') else 'off'}")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 5：基座工具就绪
        try:
            lines.append("═══ 基座工具就绪 ═══")
            base_tools = startup_report.get("base_tools", {})
            if base_tools:
                for k, v in base_tools.items():
                    lines.append(f"{k}: {v}")
            else:
                lines.append("(无)")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 6：启动副作用（数据库迁移）
        try:
            lines.append("═══ 启动副作用 ═══")
            migrations = startup_report.get("migrations", [])
            if migrations:
                migration_lines = []
                for m in migrations:
                    migration_lines.append(f"{m['table']}.{m['column']}")
                lines.append("数据库迁移：新增列 " + ", ".join(migration_lines))
            else:
                lines.append("数据库迁移：无")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        # 节 7：历史信息
        try:
            lines.append("═══ 历史信息 ═══")
            last_startup_str, downtime_str = _read_last_startup()
            if last_startup_str:
                lines.append(f"上次启动：{last_startup_str}")
                if downtime_str:
                    lines.append(f"停机时长：约 {downtime_str}")
                else:
                    # 用本次启动时间减去上次时间算停机时长
                    try:
                        last_dt = datetime.fromisoformat(last_startup_str)
                        if started_at:
                            delta = started_at - last_dt
                            hours = delta.total_seconds() // 3600
                            minutes = (delta.total_seconds() % 3600) // 60
                            if hours > 0:
                                lines.append(f"停机时长：约 {int(hours)}h {int(minutes)}m")
                            else:
                                lines.append(f"停机时长：约 {int(minutes)}m")
                    except Exception:
                        pass
            else:
                lines.append("首次启动")
            lines.append("")
        except Exception:
            lines.append("(采集失败)")
            lines.append("")

        lines.append("— Emily Core")

        body = "\n".join(lines)
        subject = "[Emily] 系统启动完成"

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

        # 写入本次启动时间（无论邮件成功与否）
        if started_at:
            _write_last_startup(started_at)

    except Exception as e:
        _logger.warning("Startup email init failed: %s", e)
