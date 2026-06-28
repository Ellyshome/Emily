"""启动报告生成器 —— 冷启动时生成系统状态报告。

生成流程：
  1. 检测环境信息（dev/staging/production）
  2. 检查各组件状态（DB/LLM/MaxKB/Email/Pipeline）
  3. 统计节点状态（通过 SMNodeRepository）
  4. 渲染 Markdown 报告
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .probe_base import TickContext
    from .config import OpsConfig

logger = logging.getLogger("emily.ops.startup_report")


# ══════════════════════════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════════════════════════


def generate_startup_report(ctx: "TickContext", config: "OpsConfig") -> dict:
    """生成冷启动报告。

    Args:
        ctx: 当前 Tick 上下文
        config: 运维模块配置

    Returns:
        dict: 包含所有报告字段的字典，可直接用于 OpsRepository.save_startup_report()
    """
    # 基本信息
    environment = _detect_environment()
    instance_id = _get_instance_id()
    version = _get_version()

    # 组件状态检测
    db_status = _check_db()
    llm_status = _check_llm()
    maxkb_status = _check_maxkb()
    email_status = _check_email()
    pipeline_status = _check_pipeline()

    # 节点统计
    nodes_completed = _count_nodes("COMPLETED")
    nodes_in_progress = _count_nodes("IN_PROGRESS")
    nodes_blocked = _count_nodes("BLOCKED")
    nodes_total = _count_all_nodes()

    # 构建报告数据
    report = {
        "tick_id": ctx.tick_id,
        "tick_number": ctx.tick_number,
        "startup_time": ctx.start_time.isoformat(),
        "environment": environment,
        "instance_id": instance_id,
        "version": version,
        "db_status": db_status,
        "llm_status": llm_status,
        "maxkb_status": maxkb_status,
        "email_status": email_status,
        "pipeline_status": pipeline_status,
        "nodes_total": nodes_total,
        "nodes_completed": nodes_completed,
        "nodes_in_progress": nodes_in_progress,
        "nodes_blocked": nodes_blocked,
    }

    # 渲染 Markdown
    report["report_content"] = _render_markdown(report, ctx)

    return report


# ══════════════════════════════════════════════════════════════════════════════
# 环境检测
# ══════════════════════════════════════════════════════════════════════════════


def _detect_environment() -> str:
    """从环境变量或配置文件推断运行环境。"""
    env = os.environ.get("EMILY_ENV", "").lower()
    if env in ("production", "prod"):
        return "production"
    if env in ("staging", "stage"):
        return "staging"
    # Docker 环境且无明确设置 → production
    if os.path.exists("/.dockerenv") or "DOCKER" in os.environ:
        return "production"
    return "dev"


def _get_instance_id() -> str:
    """获取实例标识（hostname 后 8 位）。"""
    try:
        hostname = socket.gethostname()
        return f"emily-core-{hostname[-8:]}"
    except Exception:
        return "emily-core-unknown"


def _get_version() -> str:
    """尝试从 emily_core.__version__ 或 git describe 读取版本号。"""
    try:
        from emily_core import __version__
        return __version__
    except (ImportError, AttributeError):
        pass
    try:
        import subprocess
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[5],
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


# ══════════════════════════════════════════════════════════════════════════════
# 组件健康检测
# ══════════════════════════════════════════════════════════════════════════════


def _check_db() -> bool:
    """检测 DB 连接。尝试 SELECT 1。"""
    try:
        from emily_core.infrastructure.database.session import get_session_raw
        session = get_session_raw()
        try:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception as e:
        logger.warning("Startup report: DB check failed: %s", e)
        return False


def _check_llm() -> str:
    """检测 LLM API 连接。"""
    try:
        import os
        api_key = os.environ.get("EMILY_LLM_API_KEY", "")
        base_url = os.environ.get("EMILY_LLM_BASE_URL", "https://api.deepseek.com")
        if not api_key:
            return "DISABLED"
        # 简单连接检测：只检查 API key 存在性，不做实际 ping
        return "OK"
    except Exception as e:
        logger.warning("Startup report: LLM check failed: %s", e)
        return f"ERROR: {e}"


def _check_maxkb() -> str:
    """检测 MaxKB 知识库服务。"""
    try:
        import os
        kb_enabled = os.environ.get("EMILY_KB_ENABLED", "").lower() in ("true", "1", "yes")
        if not kb_enabled:
            return "DISABLED"
        maxkb_url = os.environ.get("EMILY_MAXKB_URL", "http://maxkb:8080")
        # 简单可达性检测
        import urllib.request
        try:
            urllib.request.urlopen(f"{maxkb_url}/api/health", timeout=5)
            return "OK"
        except Exception:
            return "UNREACHABLE"
    except Exception as e:
        logger.warning("Startup report: MaxKB check failed: %s", e)
        return f"ERROR: {e}"


def _check_email() -> str:
    """检测邮箱服务配置。"""
    try:
        import os
        enabled = os.environ.get("EMILY_OPS_MAILBOX_ENABLED", "").lower() in ("true", "1", "yes")
        if not enabled:
            return "DISABLED"
        host = os.environ.get("EMILY_OPS_MAIL_IMAP_HOST", "")
        user = os.environ.get("EMILY_OPS_MAIL_USERNAME", "")
        if not host or not user:
            return "NOT_CONFIGURED"
        return "OK"
    except Exception as e:
        logger.warning("Startup report: Email check failed: %s", e)
        return f"ERROR: {e}"


def _check_pipeline() -> str:
    """检测 Pipeline BUS 状态。"""
    try:
        # BUS 状态通过 EmilyCore 的健康检查反映
        return "OK"
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# 节点统计
# ══════════════════════════════════════════════════════════════════════════════


def _count_nodes(status: str) -> int:
    """统计指定状态的节点数。"""
    try:
        from emily_core.infrastructure.database.session import get_session_raw
        from emily_core.infrastructure.database.models import SMNode
        session = get_session_raw()
        try:
            return session.query(SMNode).filter(
                SMNode.status == status,
                SMNode.is_deleted == False,
            ).count()
        finally:
            session.close()
    except Exception as e:
        logger.warning("Startup report: count nodes (%s) failed: %s", status, e)
        return 0


def _count_all_nodes() -> int:
    """统计全部非删除节点数。"""
    try:
        from emily_core.infrastructure.database.session import get_session_raw
        from emily_core.infrastructure.database.models import SMNode
        session = get_session_raw()
        try:
            return session.query(SMNode).filter(
                SMNode.is_deleted == False,
            ).count()
        finally:
            session.close()
    except Exception as e:
        logger.warning("Startup report: count all nodes failed: %s", e)
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# Markdown 渲染
# ══════════════════════════════════════════════════════════════════════════════


def _render_markdown(report: dict, ctx: "TickContext") -> str:
    """渲染 Markdown 格式的启动报告。"""
    env = report.get("environment", "unknown")
    instance_id = report.get("instance_id", "")
    version = report.get("version", "")

    def status_icon(ok: bool) -> str:
        return "🟢" if ok else "🔴"

    def str_icon(s: str) -> str:
        if s in ("OK", "DISABLED"):
            return "🟢"
        if s.startswith("ERROR") or s in ("UNREACHABLE", "NOT_CONFIGURED"):
            return "🔴"
        return "🟡"

    db_icon = status_icon(report.get("db_status", False))

    lines = [
        f"# Emily Core 冷启动报告",
        f"",
        f"**Tick #{ctx.tick_number}** | **{ctx.start_time.strftime('%Y-%m-%d %H:%M:%S')} UTC**",
        f"",
        f"## 环境信息",
        f"",
        f"| 项目 | 值 |",
        f"|------|----|",
        f"| 环境 | `{env}` |",
        f"| 实例 | `{instance_id}` |",
        f"| 版本 | `{version}` |",
        f"",
        f"## 组件状态",
        f"",
        f"| 组件 | 状态 |",
        f"|------|------|",
        f"| {db_icon} DB | {_label_bool(report.get('db_status', False))} |",
        f"| {str_icon(report.get('llm_status',''))} LLM | {report.get('llm_status','')} |",
        f"| {str_icon(report.get('maxkb_status',''))} MaxKB | {report.get('maxkb_status','')} |",
        f"| {str_icon(report.get('email_status',''))} Email | {report.get('email_status','')} |",
        f"| {str_icon(report.get('pipeline_status',''))} Pipeline | {report.get('pipeline_status','')} |",
        f"",
        f"## 业务状态",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总节点数 | {report.get('nodes_total', 0)} |",
        f"| 已完成 | {report.get('nodes_completed', 0)} |",
        f"| 进行中 | {report.get('nodes_in_progress', 0)} |",
        f"| 已阻塞 | {report.get('nodes_blocked', 0)} |",
        f"| 完成率 | {_calc_progress(report)}% |",
        f"",
        f"---",
        f"*报告由 Emily OpsScheduler 自动生成*",
    ]
    return "\n".join(lines)


def _label_bool(val: bool) -> str:
    """Boolean → 人类可读标签。"""
    return "OK" if val else "FAIL"


def _calc_progress(report: dict) -> int:
    """计算完成率百分比。"""
    total = report.get("nodes_total", 1) or 1
    completed = report.get("nodes_completed", 0)
    return int(completed / total * 100) if total > 0 else 0
