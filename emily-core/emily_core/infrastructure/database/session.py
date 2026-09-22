"""数据库会话管理 —— PostgreSQL 连接、会话工厂、自动建表。

对接 emily-postgres 容器的 PostgreSQL 服务，pool_pre_ping + pool_recycle。
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


def _ensure_columns(engine) -> list[dict]:
    """检查已有表是否缺少 ORM 定义的列，自动 ALTER TABLE 补齐。

    create_all() 只创建不存在的表，不会为已有表添加新列。
    此函数遍历已知需要补齐的表，检查 information_schema.columns，
    缺失的列自动 ALTER TABLE ADD COLUMN；随后补 unique 约束与普通索引
    （见函数内 `_PENDING_CONSTRAINTS` / `_PENDING_INDEXES`）。

    每次启动执行一次，幂等（已有列/约束/索引跳过）。

    Returns:
        本次实际新增的清单（已存在的跳过不记），格式：
        [{"table": "hook_execution_logs", "column": "user_id"}, ...]
        索引项为 {"table": ..., "index": ...}，约束项为 {"table": ..., "constraint": ...}
    """
    # 已知需要补齐的表→列映射（表名: [(列名, SQL类型, 默认值), ...]）
    _PENDING_COLUMNS = {
        "hook_execution_logs": [
            ("user_id", "VARCHAR", "''"),
            ("sop_id", "VARCHAR", "''"),
            ("block_reason", "VARCHAR(500)", "''"),
            ("session_level", "INTEGER", "NULL"),
        ],
        "session_archives": [
            ("md_file_path", "VARCHAR(500)", "''"),
            ("platform", "VARCHAR(50)", "''"),
            ("im_user_id", "VARCHAR(100)", "''"),
            ("is_guest", "BOOLEAN", "FALSE"),
            ("last_active_at", "VARCHAR(50)", "''"),
            # 历史行都是「归档时才建档」，故补列默认按已截断处理
            ("status", "VARCHAR(20)", "'truncated'"),
        ],
        "evolution_llm_interaction_logs": [
            ("response_full", "TEXT", "''"),
            ("reasoning_content", "TEXT", "''"),
        ],
        "files": [
            ("purpose", "VARCHAR(50)", "'RECORD'"),
            ("purpose_confirmed", "BOOLEAN", "FALSE"),
            ("attachment_of", "VARCHAR", "NULL"),
            ("rag_indexed", "BOOLEAN", "FALSE"),
            ("rag_collection", "VARCHAR(100)", "''"),
            ("content_summary", "TEXT", "NULL"),
            ("summary_generated_at", "VARCHAR", "NULL"),
        ],
        "tool_registry": [
            ("exposure_mode", "VARCHAR(20)", "'meta'"),
        ],
        "project_nodes": [
            ("progress", "VARCHAR", "'0.00'"),
            ("parent_node_id", "VARCHAR(100)", "''"),
            ("child_weight", "VARCHAR", "'1.0000'"),
            ("acknowledged_by", "VARCHAR(100)", "''"),
            ("acknowledged_at", "VARCHAR(50)", "''"),
            ("acknowledged_level", "INTEGER", "0"),
            ("template_ref_id", "VARCHAR(100)", "''"),
        ],
        "events": [
            ("confirmed_by", "VARCHAR", "NULL"),
        ],
        "knowledge_chunks": [
            ("content_hash", "VARCHAR(64)", "''"),
            ("ingest_status", "VARCHAR(20)", "'pending'"),
        ],
        # 操作留痕治理：归因四元组与流量性质（见 issues/操作留痕治理/）
        "business_event_logs": [
            ("source", "VARCHAR(20)", "''"),
            ("channel", "VARCHAR(30)", "''"),
            ("channel_account", "VARCHAR(200)", "''"),
            ("result", "VARCHAR(20)", "''"),
            ("error_reason", "VARCHAR(500)", "''"),
        ],
        "messages": [
            ("source", "VARCHAR(20)", "''"),
        ],
    }

    from sqlalchemy import text as sa_text

    migrations: list[dict] = []

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
                migrations.append({"table": table_name, "column": col_name})

    # ── 补齐 unique 约束（create_all 不 ALTER 已有表）──
    # 检查 pg_indexes 中是否已存在同名约束，缺失则 ALTER TABLE ADD CONSTRAINT
    _PENDING_CONSTRAINTS = {
        "project_nodes": [
            ("uq_pn_node_id", "UNIQUE (node_id)"),
        ],
    }
    with engine.connect() as conn:
        for table_name, constraints in _PENDING_CONSTRAINTS.items():
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
            for cname, definition in constraints:
                already = conn.execute(
                    sa_text(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM pg_indexes"
                        "  WHERE indexname = :idx"
                        ")"
                    ),
                    {"idx": cname},
                ).scalar()
                if already:
                    continue
                try:
                    conn.execute(
                        sa_text(f"ALTER TABLE {table_name} ADD CONSTRAINT {cname} {definition}")
                    )
                    conn.commit()
                    logger.info("Schema migration: added constraint %s to %s", cname, table_name)
                    migrations.append({"table": table_name, "constraint": cname})
                except Exception as e:
                    # 约束添加失败（如已有重复数据）只警告不阻塞启动
                    logger.warning(
                        "Schema migration: failed to add constraint %s to %s: %s",
                        cname, table_name, e,
                    )

    # ── 补齐普通索引（create_all 不 ALTER 已有表；上面的约束块只处理 UNIQUE）──
    # 操作留痕治理：source 过滤依赖索引（NFR-3），后续新增索引在此登记
    _PENDING_INDEXES = {
        "business_event_logs": [
            ("idx_bel_source_created", "(source, created_at)"),
        ],
        "messages": [
            ("idx_msg_source_created", "(source, created_at)"),
        ],
    }
    with engine.connect() as conn:
        for table_name, indexes in _PENDING_INDEXES.items():
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
            for iname, definition in indexes:
                already = conn.execute(
                    sa_text(
                        "SELECT EXISTS ("
                        "  SELECT 1 FROM pg_indexes"
                        "  WHERE indexname = :idx"
                        ")"
                    ),
                    {"idx": iname},
                ).scalar()
                if already:
                    continue
                try:
                    conn.execute(
                        sa_text(
                            f"CREATE INDEX IF NOT EXISTS {iname} "
                            f"ON {table_name} {definition}"
                        )
                    )
                    conn.commit()
                    logger.info("Schema migration: created index %s on %s", iname, table_name)
                    migrations.append({"table": table_name, "index": iname})
                except Exception as e:
                    # 索引创建失败只警告不阻塞启动
                    logger.warning(
                        "Schema migration: failed to create index %s on %s: %s",
                        iname, table_name, e,
                    )

    return migrations


def init_db(
    db_url: str | None = None,
    *,
    pg_host: str = _DEFAULT_PG_HOST,
    pg_port: int = _DEFAULT_PG_PORT,
    pg_db: str = _DEFAULT_PG_DB,
    pg_user: str = _DEFAULT_PG_USER,
    pg_password: str = _DEFAULT_PG_PASSWORD,
) -> list[dict]:
    """初始化 PostgreSQL 数据库连接，自动建表（幂等）。

    Args:
        db_url: 完整的 PostgreSQL URL（如 postgresql://user:pass@host:port/db）。
                提供此参数时忽略 pg_* 参数。
        pg_host: PG 主机地址（Docker 内用服务名 emily-postgres）。
        pg_port: PG 端口。
        pg_db: PG 数据库名。
        pg_user: PG 用户名。
        pg_password: PG 密码。

    Returns:
        本次初始化过程中新增的数据库列迁移清单（空列表表示无迁移或幂等短路）。
    """
    global _engine, _SessionLocal

    if _engine is not None:
        return []

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

    # pgvector 扩展（幂等）
    # 必须先于 create_all：knowledge_chunks 等表含 Vector 列，
    # 扩展未安装时 create_all 会报 type "vector" does not exist，导致全库建表失败。
    _ensure_pgvector_extension(_engine)

    # 建表（幂等，已存在的表不会重建）
    Base.metadata.create_all(bind=_engine)

    # 补齐已有表的新增列（create_all 不 ALTER 已有表）
    migrations = _ensure_columns(_engine)

    logger.info(
        "Database initialized (PostgreSQL): %d tables",
        len(Base.metadata.tables),
    )

    return migrations


def _ensure_pgvector_extension(engine) -> None:
    """确保 pgvector 扩展已安装（幂等）。"""
    from sqlalchemy import text as sa_text
    try:
        with engine.connect() as conn:
            conn.execute(sa_text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        logger.info("pgvector extension ensured")
    except Exception as e:
        logger.warning("pgvector extension not available: %s", e)


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
