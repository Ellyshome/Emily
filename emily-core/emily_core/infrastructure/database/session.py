"""数据库会话管理 —— PostgreSQL 连接、会话工厂、自动建表。

对接 MaxKB 容器的 PostgreSQL 服务，pool_pre_ping + pool_recycle。
"""

import logging
from contextlib import contextmanager
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from .models import Base

logger = logging.getLogger("emily.db")

# 默认 PG 连接参数（Docker compose 服务名）
_DEFAULT_PG_HOST = "emily-postgres"
_DEFAULT_PG_PORT = 5432
_DEFAULT_PG_DB = "emily"
_DEFAULT_PG_USER = "emily"
_DEFAULT_PG_PASSWORD = "emily_secret_2026"

_engine = None
_SessionLocal = None


def _create_pg_engine(
    host: str,
    port: int,
    db: str,
    user: str,
    password: str,
):
    """创建 PostgreSQL 引擎。"""
    url = f"postgresql://{user}:{quote_plus(password)}@{host}:{port}/{db}"
    return create_engine(
        url,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,    # 连接前验证（容器重启后自动重连）
        pool_recycle=3600,     # 每小时回收连接
    )


def _ensure_columns(engine) -> None:
    """检查已有表是否缺少 ORM 定义的列，自动 ALTER TABLE 补齐。

    create_all() 只创建不存在的表，不会为已有表添加新列。
    此函数遍历已知需要补齐的表，检查 information_schema.columns，
    缺失的列自动 ALTER TABLE ADD COLUMN。

    每次启动执行一次，幂等（已有列跳过）。
    """
    # 已知需要补齐的表→列映射（表名: [(列名, SQL类型, 默认值), ...]）
    _PENDING_COLUMNS = {
        "hook_execution_logs": [
            ("user_id", "VARCHAR", "''"),
            ("sop_id", "VARCHAR", "''"),
            ("block_reason", "VARCHAR(500)", "''"),
            ("session_level", "INTEGER", "NULL"),
        ],
    }

    from sqlalchemy import text as sa_text

    with engine.connect() as conn:
        for table_name, columns in _PENDING_COLUMNS.items():
            # 检查表是否存在
            table_exists = conn.execute(
                sa_text(
                    "SELECT EXISTS ("
                    "  SELECT 1 FROM information_schema.tables"
                    "  WHERE table_name = :tbl"
                    ")"
                ),
                {"tbl": table_name},
            ).scalar()

            if not table_exists:
                continue

            # 获取已有列名
            existing_rows = conn.execute(
                sa_text(
                    "SELECT column_name FROM information_schema.columns"
                    "  WHERE table_name = :tbl"
                ),
                {"tbl": table_name},
            ).fetchall()
            existing = {r[0] for r in existing_rows}

            for col_name, col_type, col_default in columns:
                if col_name in existing:
                    continue
                default_clause = ""
                if col_default != "NULL":
                    default_clause = f" DEFAULT {col_default}"
                else:
                    default_clause = ""
                conn.execute(
                    sa_text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {col_name} {col_type}{default_clause}"
                    )
                )
                conn.commit()
                logger.info(
                    "Schema migration: added column %s to %s",
                    col_name, table_name,
                )


def init_db(
    db_url: str | None = None,
    *,
    pg_host: str = _DEFAULT_PG_HOST,
    pg_port: int = _DEFAULT_PG_PORT,
    pg_db: str = _DEFAULT_PG_DB,
    pg_user: str = _DEFAULT_PG_USER,
    pg_password: str = _DEFAULT_PG_PASSWORD,
) -> None:
    """初始化 PostgreSQL 数据库连接，自动建表（幂等）。

    Args:
        db_url: 完整的 PostgreSQL URL（如 postgresql://user:pass@host:port/db）。
                提供此参数时忽略 pg_* 参数。
        pg_host: PG 主机地址（Docker 内用服务名 maxkb）。
        pg_port: PG 端口。
        pg_db: PG 数据库名。
        pg_user: PG 用户名。
        pg_password: PG 密码。
    """
    global _engine, _SessionLocal

    if _engine is not None:
        return

    if db_url:
        _engine = create_engine(
            db_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        logger.info("Database engine: PostgreSQL (from URL)")
    else:
        _engine = _create_pg_engine(pg_host, pg_port, pg_db, pg_user, pg_password)
        logger.info("Database engine: PostgreSQL (%s:%d/%s)", pg_host, pg_port, pg_db)

    _SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=_engine,
        expire_on_commit=False,  # 避免 detached instance 后访问属性报错
    )

    # 建表（幂等，已存在的表不会重建）
    Base.metadata.create_all(bind=_engine)

    # 补齐已有表的新增列（create_all 不 ALTER 已有表）
    _ensure_columns(_engine)

    logger.info(
        "Database initialized (PostgreSQL): %d tables",
        len(Base.metadata.tables),
    )


@contextmanager
def get_session() -> Session:
    """获取数据库会话（上下文管理器，自动 commit/rollback）。

    Usage::

        with get_session() as session:
            user = session.query(User).first()
    """
    if _SessionLocal is None:
        init_db()

    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_raw() -> Session:
    """获取裸 Session（需调用方手动 close）。

    供需要跨多个操作持有同一数据库连接的场景使用，
    例如 PostgreSQL Advisory Lock：持锁期间必须保持同一 session/连接，
    否则锁会随 session 关闭而释放（见 PlanTaskScheduler._tick）。
    """
    if _SessionLocal is None:
        init_db()
    return _SessionLocal()


def get_db_path() -> str:
    """获取当前数据库连接信息（用于调试/日志）。"""
    if _engine is not None:
        return str(_engine.url)
    return "postgresql://emily@emily-postgres:5432/emily"
