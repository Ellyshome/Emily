"""snapshot collector — 统一信息快照采集器（进化模块专用）。

一次采集，供进化闭环、认知闭环两个模块共同消费。
晨报模块独立采集，不在此处。

数据源：
  项目节点 — 状态分布、逾期、按项目拆分
  出入站聊天 — messages 表中目标日期的纯文本对话（用户消息 + Emily回复）
  系统日志报错 — emily_YYYYMMDD.log 中目标日期的 ERROR/WARNING + Traceback
  Session 异常 — session_archives 中目标日期的 pipeline 失败/工具调用失败

用法（import）:
    from emily_core.snapshot import SnapshotCollector
    collector = SnapshotCollector()
    snapshot = await collector.collect("2026-07-27", days=1)

用法（CLI）:
    uv run python scripts/snapshot.py
    uv run python scripts/snapshot.py --date 2026-07-27 --json
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_

logger = logging.getLogger("emily.snapshot")

# ── 通用文件读取工具 ────────────────────────────────────────────────

_MAX_PREVIEW = 300  # 每条报错内容的字符上限


def _read_text_file(path: Path) -> str | None:
    """安全读取文本文件全部内容。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ── emily-data 路径 ────────────────────────────────────────────────


def _resolve_data_dir() -> Path:
    """三级探测 emily-data 目录：环境变量 → 容器路径 → 开发回退。"""
    env_dir = os.environ.get("EMILY_DATA_DIR", "")
    if env_dir:
        p = Path(env_dir)
        if p.exists():
            return p

    container_dir = Path("/app/data")
    if container_dir.exists():
        return container_dir

    # 开发环境：从 collector.py 向上 3 级到项目根，再拼接 emily-data
    dev_dir = Path(__file__).resolve().parents[3] / "emily-data"
    return dev_dir


# ── Docker PG 端口检测 ────────────────────────────────────────────────


def _detect_docker_pg_port() -> int | None:
    """检测 emily-postgres 容器的映射端口。"""
    try:
        result = subprocess.run(
            ["docker", "port", "emily-postgres", "5432/tcp"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port_str = result.stdout.strip().rsplit(":", 1)[-1]
            return int(port_str)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _init_db(db_url: str = "") -> None:
    """初始化数据库连接（幂等）。"""
    from emily_core.infrastructure.database.session import init_db, _engine

    if _engine is not None:
        return

    if db_url:
        init_db(db_url=db_url)
    else:
        db_url_env = os.environ.get("EMILY_DATABASE_URL", "")
        if db_url_env:
            init_db(db_url=db_url_env)
        else:
            pg_host = os.environ.get("EMILY_PG_HOST", "127.0.0.1")
            pg_port_env = os.environ.get("EMILY_PG_PORT")
            pg_port = int(pg_port_env) if pg_port_env else (_detect_docker_pg_port() or 5432)
            pg_db = os.environ.get("EMILY_PG_DB", "emily")
            pg_user = os.environ.get("EMILY_PG_USER", "emily")
            pg_password = os.environ.get("EMILY_PG_PASSWORD", "emily_secret_2026")
            init_db(pg_host=pg_host, pg_port=pg_port, pg_db=pg_db, pg_user=pg_user, pg_password=pg_password)


# ══════════════════════════════════════════════════════════════════════════════
# SnapshotCollector
# ══════════════════════════════════════════════════════════════════════════════


class SnapshotCollector:
    """统一信息快照采集器（进化模块专用）。

    Parameters:
        db_url: PostgreSQL 连接 URL（为空则从环境变量/Docker 自动检测）
    """

    _ERROR_LINE_RE = re.compile(r"\[(ERROR|CRITICAL)\]")
    _TRACEBACK_START_RE = re.compile(r"^Traceback \(most recent call last\):")
    _PIPELINE_HEADER_RE = re.compile(r"^#{1,4}\s.*(?:Pipeline|pipeline|管道|执行)")
    _FAILURE_KEYWORDS = re.compile(r"(FAILED|BLOCKED|ABORTED|failed|blocked|aborted|失败|阻断|中断|异常)")

    def __init__(self, *, db_url: str = ""):
        self._db_url = db_url
        self._initialized = False

    def _ensure_db(self) -> None:
        if self._initialized:
            return
        _init_db(self._db_url)
        self._initialized = True

    # ════════════════════════════════════════════════════════════════════
    # 主入口
    # ════════════════════════════════════════════════════════════════════

    async def collect(self, end_date: str, *, days: int = 1) -> dict:
        """采集统一快照。

        Args:
            end_date: 统计截止日期 YYYY-MM-DD，同时也作为扫描日期的基准
            days: 复盘天数，默认 1

        Returns:
            完整快照 dict，包含 meta / projects / chat_samples / system_errors / session_anomalies 五个区块
        """
        self._ensure_db()

        start_dt = datetime.fromisoformat(end_date) - timedelta(days=days - 1)
        start_date = start_dt.strftime("%Y-%m-%d")
        collected_at = datetime.utcnow().isoformat()

        return {
            "meta": {
                "start_date": start_date,
                "end_date": end_date,
                "analysis_days": days,
                "collected_at": collected_at,
            },
            "projects":           await self._collect_projects(end_date),
            "chat_samples":       await self._collect_chat_samples(start_date, end_date),
            "system_errors":      await self._collect_system_errors(end_date),
            "session_anomalies":  await self._collect_session_anomalies(end_date),
        }

    # ════════════════════════════════════════════════════════════════════
    # 一、项目级数据
    # ════════════════════════════════════════════════════════════════════

    async def _collect_projects(self, end_date: str) -> dict:
        """采集所有活跃项目的节点、依赖、生命周期、参建单位。"""
        from emily_core.infrastructure.database.session import get_session
        from emily_core.infrastructure.database.models import (
            Project, ProjectNode, NodeDependency,
        )
        from emily_core.repositories.evolution_repo import EvolutionRepo

        with get_session() as sess:
            projects = sess.query(Project).filter(
                or_(Project.is_deleted == False, Project.is_deleted == None),
            ).all()

            # I: 项目节点聚合
            nodes_aggregate = EvolutionRepo.aggregate_project_nodes(end_date, session=sess)

            # 按项目拆分的节点详情
            per_project_nodes = {}
            for proj in projects:
                nodes = sess.query(ProjectNode).filter(
                    ProjectNode.project_id == proj.id,
                    ProjectNode.is_discarded == False,
                ).all()
                per_project_nodes[proj.id] = {
                    "project_name": proj.name or "",
                    "total_nodes": len(nodes),
                    "status_distribution": {},
                    "overdue_count": 0,
                    "nodes": [],
                }
                now_str = datetime.utcnow().strftime("%Y-%m-%d")
                for n in nodes:
                    per_project_nodes[proj.id]["status_distribution"][n.status] = \
                        per_project_nodes[proj.id]["status_distribution"].get(n.status, 0) + 1
                    if n.status != "COMPLETED" and n.deadline and n.deadline < now_str:
                        per_project_nodes[proj.id]["overdue_count"] += 1
                    per_project_nodes[proj.id]["nodes"].append({
                        "node_id": n.node_id,
                        "name": n.node_name,
                        "status": n.status,
                        "deadline": n.deadline,
                        "responsible_user_id": n.responsible_user_id,
                    })

            # K: 每项目依赖链数量
            per_project_deps = {}
            for proj in projects:
                node_ids = [n.node_id for n in sess.query(ProjectNode).filter(
                    ProjectNode.project_id == proj.id,
                    ProjectNode.is_discarded == False,
                ).all()]
                if node_ids:
                    dep_count = sess.query(func.count(NodeDependency.id)).filter(
                        NodeDependency.node_id.in_(node_ids),
                    ).scalar() or 0
                else:
                    dep_count = 0
                per_project_deps[proj.id] = dep_count

            # L: 生命周期阶段
            lifecycle_stages = {}
            for proj in projects:
                lifecycle_stages[proj.id] = {
                    "project_name": proj.name or "",
                    "lifecycle_stage": proj.lifecycle_stage or 0,
                }

            return {
                "aggregate": nodes_aggregate,
                "by_project": per_project_nodes,
                "dependencies_by_project": per_project_deps,
                "lifecycle_stages": lifecycle_stages,
            }

    # ════════════════════════════════════════════════════════════════════
    # 二、出入站聊天记录（目标时间段）
    # ════════════════════════════════════════════════════════════════════

    async def _collect_chat_samples(self, start_date: str, end_date: str) -> dict:
        """从 messages 表拉取目标时间段的纯文本对话，按会话分组。"""
        from emily_core.infrastructure.database.models import Message
        from emily_core.infrastructure.database.session import get_session

        end_next = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")

        with get_session() as sess:
            messages = sess.query(Message).filter(
                Message.created_at >= start_date,
                Message.created_at < end_next,
                Message.msg_type == 1,
                or_(
                    Message.message_type == "text",
                    Message.direction == "agent_to_user",
                ),
            ).order_by(Message.conversation_id, Message.created_at).all()

        # 按 conversation_id 分组
        conversations: dict[str, dict] = {}
        for m in messages:
            cid = m.conversation_id or "_unknown"
            if cid not in conversations:
                conversations[cid] = {
                    "conversation_id": cid,
                    "primary_user": "",
                    "turns": [],
                }
            name = m.sender_name or ""
            if not conversations[cid]["primary_user"] and m.direction == "user_to_agent" and name:
                conversations[cid]["primary_user"] = name

            conversations[cid]["turns"].append({
                "time": m.created_at[11:19] if m.created_at and len(m.created_at) >= 19 else "",
                "direction": m.direction,
                "user": name,
                "content": m.content or "",
            })

        # 按消息数量排序，取前 20 个会话，每个取前 30 轮
        sorted_convs = sorted(
            conversations.values(),
            key=lambda c: len(c["turns"]), reverse=True,
        )

        result = []
        for conv in sorted_convs[:20]:
            turns = conv["turns"][:30]
            result.append({
                "user": conv["primary_user"],
                "cid": conv["conversation_id"][:8],
                "turns": turns,
            })

        return {
            "start_date": start_date,
            "end_date": end_date,
            "total_messages": len(messages),
            "total_conversations": len(conversations),
            "conversations": result,
        }

    # ════════════════════════════════════════════════════════════════════
    # 三、系统日志报错（目标日期）
    # ════════════════════════════════════════════════════════════════════

    async def _collect_system_errors(self, end_date: str) -> dict:
        """读取目标日期 emily_YYYYMMDD.log 中的报错行及相邻 Traceback。"""
        data_dir = _resolve_data_dir()
        logs_dir = data_dir / "logs"

        target_date = end_date[:10]
        date_compact = target_date.replace("-", "")  # YYYYMMDD
        log_file = logs_dir / f"emily_{date_compact}.log"

        if not log_file.exists():
            return {"date": target_date, "log_file": str(log_file), "exists": False, "errors": []}

        content = _read_text_file(log_file)
        if content is None:
            return {"date": target_date, "log_file": str(log_file), "exists": True, "errors": []}

        lines = content.split("\n")
        errors = []
        seen: set[str] = set()
        i = 0

        while i < len(lines):
            line = lines[i]
            if self._ERROR_LINE_RE.search(line):
                block_lines = [line]
                j = i + 1
                while j < len(lines) and j <= i + 20:
                    next_line = lines[j]
                    if (self._TRACEBACK_START_RE.search(next_line) or
                            next_line.startswith("  File ")):
                        block_lines.append(next_line)
                        j += 1
                    elif next_line.strip() == "":
                        j += 1
                    else:
                        break
                text = "\n".join(block_lines).strip()
                fingerprint = text[:200]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    errors.append(text[:_MAX_PREVIEW])
                i = j
            else:
                i += 1

        return {
            "date": target_date,
            "log_file": str(log_file),
            "exists": True,
            "total_lines": len(lines),
            "error_count_raw": sum(1 for l in lines if "[ERROR]" in l),
            "error_count_dedup": len(errors),
            "errors": errors[:50],
        }

    # ════════════════════════════════════════════════════════════════════
    # 四、Session 归档异常（目标日期）
    # ════════════════════════════════════════════════════════════════════

    async def _collect_session_anomalies(self, end_date: str) -> dict:
        """扫描目标日期 session 归档，提取 pipeline 执行异常及工具调用失败段落。"""
        data_dir = _resolve_data_dir()
        archives_dir = data_dir / "session_archives"

        if not archives_dir.exists():
            return {"date": end_date[:10], "path": str(archives_dir), "exists": False, "anomalies": []}

        target_date = end_date[:10]
        anomalies = []
        file_count = 0

        for f in sorted(archives_dir.iterdir()):
            if not f.is_file() or f.suffix != ".md":
                continue
            if target_date not in f.name:
                continue
            file_count += 1

            name = f.stem
            parts = name.split("_", 2)
            file_user = parts[1] if len(parts) > 1 else ""

            content = _read_text_file(f)
            if content is None:
                continue

            lines = content.split("\n")

            # 策略 1: 找 final_status 为 FAILED/BLOCKED/ABORTED 的 pipeline 输出
            in_block = False
            block_lines: list[str] = []
            for line in lines:
                if "final_status" in line or self._PIPELINE_HEADER_RE.search(line):
                    if self._FAILURE_KEYWORDS.search(line):
                        in_block = True
                        block_lines = [line]
                elif in_block:
                    block_lines.append(line)
                    if line.strip() == "" or line.startswith("---") or line.startswith("##"):
                        text = "\n".join(block_lines).strip()
                        if len(text) > 10:
                            anomalies.append({
                                "user": file_user,
                                "session": f.name,
                                "type": "pipeline_failure",
                                "excerpt": text[:_MAX_PREVIEW],
                            })
                        in_block = False
                    elif len(block_lines) > 10:
                        in_block = False

            # 策略 2: 找工具调用失败
            for i, line in enumerate(lines):
                if '"error"' in line.lower() or '"is_success": false' in line.lower():
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    ctx = "\n".join(lines[start:end]).strip()
                    if len(ctx) > 10:
                        anomalies.append({
                            "user": file_user,
                            "session": f.name,
                            "type": "tool_failure",
                            "excerpt": ctx[:_MAX_PREVIEW],
                        })

            if len(anomalies) >= 100:
                break

        return {
            "date": target_date,
            "path": str(archives_dir),
            "exists": True,
            "target_files_scanned": file_count,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[:100],
        }


# ══════════════════════════════════════════════════════════════════════════════
# 便捷函数（import 模式）
# ══════════════════════════════════════════════════════════════════════════════


async def collect_snapshot(end_date: str, *, days: int = 1, db_url: str = "") -> dict:
    """采集统一快照（便捷函数）。

    Args:
        end_date: 统计截止日期 YYYY-MM-DD
        days: 复盘天数，默认 1
        db_url: PostgreSQL 连接 URL

    Returns:
        完整快照 dict
    """
    collector = SnapshotCollector(db_url=db_url)
    return await collector.collect(end_date, days=days)


# ══════════════════════════════════════════════════════════════════════════════
# CLI 入口（直接运行脚本）
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from .__main__ import main
    main()
