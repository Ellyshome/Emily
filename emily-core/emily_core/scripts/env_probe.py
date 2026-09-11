"""env_probe — 脚本控制台的环境探针。

控制台需要让使用者一眼看清"当前连的是不是真实环境、Docker 部署成什么样"，
避免误以为在测试环境里、对生产库执行了写库脚本。
"""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlparse

logger = logging.getLogger("emily.scripts.env")

_DOCKER_SOCK = "/var/run/docker.sock"


def collect_env(core=None) -> dict:
    """汇总运行环境信息。

    Args:
        core: EmilyCore 实例，用于取 health()；None 时 Core 段留空。
    """
    database = _database_info()
    return {
        "runtime": _runtime_info(),
        "database": database,
        "counts": _entity_counts() if database.get("connected") else {},
        "core": _core_info(core),
        "containers": _docker_containers(),
    }


def _runtime_info() -> dict:
    """进程自身所在的运行环境。"""
    return {
        "in_container": os.path.exists("/.dockerenv"),
        "hostname": os.environ.get("HOSTNAME", ""),
        "python": sys.version.split()[0],
    }


def _database_info() -> dict:
    """数据库连接事实：来源（脱敏，不含账号口令）、连通性、表数量。"""
    info: dict = {"connected": False, "dialect": "", "host": "", "port": None,
                  "database": "", "tables": 0}

    raw_url = os.environ.get("EMILY_DATABASE_URL", "")
    if raw_url:
        try:
            parsed = urlparse(raw_url)
            info["dialect"] = parsed.scheme
            info["host"] = parsed.hostname or ""
            info["port"] = parsed.port
            info["database"] = (parsed.path or "").lstrip("/")
        except Exception as ex:
            logger.warning("database url parse failed: %s", ex)

    from sqlalchemy import text

    from emily_core.infrastructure.database.session import get_session

    try:
        with get_session() as session:
            tables = session.execute(text(
                "select count(*) from information_schema.tables where table_schema = 'public'"
            )).scalar()
        info["connected"] = True
        info["tables"] = int(tables or 0)
    except Exception as ex:
        logger.warning("database probe failed: %s", ex)
        info["error"] = str(ex)[:200]

    return info


def _entity_counts() -> dict:
    """关键实体计数 —— 用于判断这是真实生产数据还是空库。"""
    from sqlalchemy import func

    from emily_core.infrastructure.database.models import Project, ProjectNode, User
    from emily_core.infrastructure.database.session import get_session

    with get_session() as session:
        return {
            "users": session.query(func.count(User.id))
                             .filter(User.is_deleted.isnot(True)).scalar() or 0,
            "projects": session.query(func.count(Project.id))
                               .filter(Project.is_deleted.isnot(True)).scalar() or 0,
            "nodes": session.query(func.count(ProjectNode.id))
                            .filter(ProjectNode.is_discarded.isnot(True)).scalar() or 0,
        }


def _core_info(core) -> dict:
    """EmilyCore 健康状态。"""
    if core is None:
        return {}
    try:
        health = core.health()
    except Exception as ex:
        logger.warning("core health probe failed: %s", ex)
        return {"error": str(ex)[:200]}
    return {
        "status": health.get("status", ""),
        "initialized": health.get("initialized", False),
        "sessions": health.get("sessions", 0),
        "uptime_seconds": health.get("uptime", 0),
        "langgraph_engine": health.get("langgraph_engine", False),
    }


def _docker_containers(timeout: float = 3.0) -> list[dict]:
    """经 docker.sock 查容器清单（只读）。

    Docker Engine API 走 unix socket；容器内没装 docker CLI，故用 httpx 的 uds 传输。
    sock 未挂载或查询失败时返回空列表，不影响其余环境信息展示。
    """
    if not os.path.exists(_DOCKER_SOCK):
        return []

    try:
        import httpx

        transport = httpx.HTTPTransport(uds=_DOCKER_SOCK)
        with httpx.Client(transport=transport, base_url="http://docker",
                          timeout=timeout) as client:
            resp = client.get("/containers/json", params={"all": "true"})
            resp.raise_for_status()
            raw = resp.json()
    except Exception as ex:
        logger.warning("docker socket probe failed: %s", ex)
        return []

    containers = []
    for item in raw:
        names = [n.lstrip("/") for n in (item.get("Names") or [])]
        ports = [
            f"{p.get('PublicPort')}→{p.get('PrivatePort')}"
            for p in (item.get("Ports") or []) if p.get("PublicPort")
        ]
        containers.append({
            "id": (item.get("Id") or "")[:12],
            "name": names[0] if names else (item.get("Id") or "")[:12],
            "image": item.get("Image", ""),
            "state": item.get("State", ""),
            "status": item.get("Status", ""),
            "ports": ", ".join(ports),
        })
    return sorted(containers, key=lambda c: c["name"])
