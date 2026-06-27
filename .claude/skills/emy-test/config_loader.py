"""EmysTester —— Emily Core 容器接口配置加载。

从 .env 文件和系统环境变量加载 EMILY_* 前缀的配置。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# 项目根目录（文件位于 .claude/skills/emy-test/config_loader.py，上溯 4 层）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# 技能目录
_SKILL_DIR = Path(__file__).resolve().parent

# 确保 stdout 使用 UTF-8（Windows 控制台兼容）
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保能导入 emily_agent 插件的 adapters 模块（复用 api_client / sse_listener / StandardMessage）
_PLUGIN_DIR = _PROJECT_ROOT / "data" / "plugins" / "emily_agent"
for _candidate in [
    _SKILL_DIR,       # 本 skill 目录（兄弟模块互 import）
    _PROJECT_ROOT,    # 项目根目录
    _PLUGIN_DIR,      # emily_agent 插件目录（复用 adapters/*）
]:
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

# ═══════════════════════════════════════════════════════════════════════════════
# .env 文件加载
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_dotenv_fallback(env_path: Path) -> None:
    """手动解析 .env，将键值写入 os.environ（不覆盖已有）。"""
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def _load_env_override(env_path: Path | None = None) -> dict:
    """可选：从项目根目录 .env 读取覆盖项（不覆盖已有 os.environ 值）。

    .env 优先级高于系统环境变量。
    """
    if env_path is None:
        env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return {}

    # 尝试用 python-dotenv 解析；失败则手动解析
    try:
        from dotenv import load_dotenv as _load
        _load(env_path, override=False)
    except ImportError:
        _parse_dotenv_fallback(env_path)

    # 从环境变量提取 EmyTester 关心的键
    overrides = {}
    for env_key in os.environ:
        if env_key.startswith("EMY_"):
            overrides[env_key] = os.environ[env_key]
    return overrides


# 模块加载时自动：.env 若存在则读入环境变量（不覆盖已有值）
_load_env_override()

# ═══════════════════════════════════════════════════════════════════════════════
# 配置读取函数
# ═══════════════════════════════════════════════════════════════════════════════

def get_core_url() -> str:
    """获取 emily-core 容器 HTTP 地址。

    来源：EMILY_CORE_URL 环境变量，默认 http://localhost:18080
    """
    return os.environ.get("EMILY_CORE_URL", "http://localhost:18080")


def get_api_token() -> str:
    """获取 emily-core API 认证 token。

    来源：EMILY_API_TOKEN 环境变量，默认空（无认证）。
    """
    return os.environ.get("EMILY_API_TOKEN", "")


def get_llm_config() -> dict:
    """获取 LLM 配置。

    来源：EMILY_LLM_* 环境变量。

    Returns:
        {"api_key": ..., "base_url": ..., "model": ...} or {} if not configured
    """
    api_key = os.environ.get("EMILY_LLM_API_KEY", "")
    if not api_key:
        return {}
    return {
        "api_key": api_key,
        "base_url": os.environ.get("EMILY_LLM_BASE_URL", "https://api.deepseek.com"),
        "model": os.environ.get("EMILY_LLM_MODEL", "deepseek-chat"),
    }


def get_pg_config() -> dict:
    """解析 PostgreSQL 连接配置。

    来源：EMILY_DATABASE_URL 环境变量，
    默认 Docker Compose 配置 (emily-postgres 容器，host 端口 25432)。

    Returns:
        {"host": ..., "port": ..., "db": ..., "user": ..., "password": ...}
    """
    url = os.environ.get("EMILY_DATABASE_URL", "")
    if url:
        try:
            parsed = urlparse(url)
            return {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 5432,
                "db": (parsed.path or "/emily").lstrip("/"),
                "user": parsed.username or "emily",
                "password": parsed.password or "",
            }
        except Exception:
            pass

    # 默认：Docker Compose 中 emily-postgres 的 host 端口映射
    return {
        "host": "localhost",
        "port": 25432,
        "db": "emily",
        "user": "emily",
        "password": "emily_secret_2026",
    }


def get_db_url() -> str:
    """获取完整的 PostgreSQL 连接 URL。

    用于 SQLAlchemy create_engine()。
    """
    pg = get_pg_config()
    return (
        f"postgresql://{pg['user']}:{pg['password']}"
        f"@{pg['host']}:{pg['port']}/{pg['db']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 数据库查询函数（用于 Web UI 下拉选择）
# ═══════════════════════════════════════════════════════════════════════════════

def get_active_users() -> list[dict]:
    """从数据库获取活跃用户列表。

    用于 Web UI 的发送者下拉选择框，按权限级别排序。

    Returns:
        list[dict]: 用户列表，每个用户包含 id, real_name, username, 
                   permission_level, company_name, phone 等字段
                   失败时返回空列表
    """
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        # 如果没有 SQLAlchemy，返回空列表
        return []

    try:
        db_url = get_db_url()
        engine = create_engine(db_url)
        
        query = text("""
            SELECT 
                u.id,
                u.real_name,
                u.username,
                u.permission_level,
                u.phone,
                u.email,
                c.company_name,
                CASE u.permission_level
                    WHEN 1 THEN '访客'
                    WHEN 2 THEN '参建执行'
                    WHEN 3 THEN '参建管理'
                    WHEN 4 THEN '建设主管'
                    WHEN 5 THEN '管理员'
                    WHEN 6 THEN '系统管理员'
                    ELSE '未知'
                END as permission_label
            FROM users u
            LEFT JOIN company_info c ON u.company = c.id
            WHERE u.is_deleted = false
              AND u.status = 'active'
            ORDER BY u.permission_level DESC, u.real_name
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query)
            users = []
            for row in result:
                users.append({
                    "id": row[0],
                    "real_name": row[1] or row[2] or "未知用户",
                    "username": row[2],
                    "permission_level": row[3],
                    "phone": row[4],
                    "email": row[5],
                    "company_name": row[6] or "未分配单位",
                    "permission_label": row[7],
                    "display_name": f"{row[1] or row[2]} ({row[7]} - {row[6] or '未分配单位'})"
                })
            return users
    except Exception as e:
        # 数据库连接失败时返回空列表，不影响 UI 使用
        logging.getLogger("emys.config_loader").warning(
            "Failed to load users from database: %s", e
        )
        return []


def get_user_by_id(user_id: str) -> dict | None:
    """根据用户 ID 获取用户详情。

    Args:
        user_id: 用户 ID

    Returns:
        dict: 用户详情字典，不存在或失败时返回 None
    """
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return None

    try:
        db_url = get_db_url()
        engine = create_engine(db_url)
        
        query = text("""
            SELECT 
                u.id,
                u.real_name,
                u.username,
                u.permission_level,
                u.phone,
                u.email,
                u.wechat,
                u.qq,
                c.company_name,
                c.type as company_type
            FROM users u
            LEFT JOIN company_info c ON u.company = c.id
            WHERE u.id = :user_id
              AND u.is_deleted = false
        """)
        
        with engine.connect() as conn:
            result = conn.execute(query, {"user_id": user_id})
            row = result.fetchone()
            if row:
                return {
                    "id": row[0],
                    "real_name": row[1] or row[2],
                    "username": row[2],
                    "permission_level": row[3],
                    "phone": row[4],
                    "email": row[5],
                    "wechat": row[6],
                    "qq": row[7],
                    "company_name": row[8],
                    "company_type": row[9],
                }
            return None
    except Exception as e:
        logging.getLogger("emys.config_loader").warning(
            "Failed to get user by id: %s", e
        )
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 向后兼容别名（旧代码可能引用这些名称）
# ═══════════════════════════════════════════════════════════════════════════════

def _read_cmd_config() -> dict:
    """[已弃用] 从旧 AstrBot cmd_config.json 读取 LLM 配置。

    保留此函数仅用于向后兼容。新代码应使用 get_llm_config()。
    """
    cfg_path = _PROJECT_ROOT / "data" / "cmd_config.json"
    if not cfg_path.exists():
        return {}
    import json
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return {}

    overrides: dict = {}
    try:
        src = cfg["provider_sources"][0]
        overrides["llm_api_key"] = src["key"][0]
        overrides["llm_base_url"] = src.get("api_base", "https://api.deepseek.com/v1")
    except (KeyError, IndexError):
        pass
    try:
        overrides["llm_model"] = cfg["provider"][0]["model"]
    except (KeyError, IndexError):
        pass
    return overrides


def _load_pg_config() -> dict:
    """[已弃用] 从旧 team_brain_agent_config.json 加载 PG 配置。

    保留此函数仅用于向后兼容。新代码应使用 get_pg_config()。
    """
    cfg_path = _PROJECT_ROOT / "data" / "config" / "team_brain_agent_config.json"
    if not cfg_path.exists():
        return {}
    import json
    with open(cfg_path, encoding="utf-8-sig") as f:
        plugin_cfg = json.load(f)
    if plugin_cfg.get("db_type") == "postgresql":
        return {
            "db_type": "postgresql",
            "pg_host": plugin_cfg.get("pg_host", "maxkb"),
            "pg_port": plugin_cfg.get("pg_port", 5432),
            "pg_db": plugin_cfg.get("pg_db", "team_brain"),
            "pg_user": plugin_cfg.get("pg_user", "root"),
            "pg_password": plugin_cfg.get("pg_password", ""),
        }
    return {}
