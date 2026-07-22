"""调度作业下次执行时间计算（reschedule）。

- ONCE：一次性，不重排（返回 ""，由调用方置作业 INACTIVE）。
- INTERVAL：now + interval_seconds。
- CRON：优先 croniter 解析 cron_expression；croniter 不可用时回退极简解析器
  （仅支持「分 时 * * 周」/「分 时 * * *」五字段形式）。

cron 表达式按北京时间解释（种子 `0 9 * * 1` = 北京时间周一 09:00），
返回值统一为 UTC-aware ISO 字符串（带 +00:00），与 SchedulerEngine._is_due
的 aware-aware 比较对齐，不再产生 naive 时间串（见踩坑 2.13）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("emily.scheduler.next_execution")

_BEIJING_TZ = timezone(timedelta(hours=8))


def calc_next_execution(job, now: datetime) -> str:
    """返回下次执行时间 UTC-aware ISO 字符串；ONCE / 无法计算返回 ""。

    Args:
        job: SchedulerJob ORM 对象（读 job_type / interval_seconds / cron_expression）。
        now: UTC-aware datetime，reschedule 基准时间。
    """
    jt = getattr(job, "job_type", "ONCE")
    if jt == "INTERVAL":
        secs = int(getattr(job, "interval_seconds", 0) or 0)
        if secs <= 0:
            return ""
        return (now + timedelta(seconds=secs)).isoformat()
    if jt == "CRON":
        cron = getattr(job, "cron_expression", "") or ""
        if not cron:
            return ""
        nxt = _next_cron(cron, now)
        return nxt.isoformat() if nxt else ""
    return ""  # ONCE：一次性，不重排


def _next_cron(cron_expr: str, now: datetime) -> datetime | None:
    """cron 表达式按北京时间解释，返回下次触发的 UTC-aware datetime。"""
    now_bj = now.astimezone(_BEIJING_TZ)
    try:
        import croniter
    except ImportError:
        nxt_bj = _simple_next_cron(cron_expr, now_bj)
        if nxt_bj is None:
            logger.warning(
                "croniter 未安装且极简解析器无法解析 cron '%s'，作业不重排", cron_expr
            )
        return nxt_bj.astimezone(timezone.utc) if nxt_bj else None
    try:
        cron = croniter.croniter(cron_expr, now_bj)
        return cron.get_next(datetime).astimezone(timezone.utc)
    except Exception as e:
        logger.warning("croniter 解析 '%s' 失败：%s，回退极简解析器", cron_expr, e)
        nxt_bj = _simple_next_cron(cron_expr, now_bj)
        return nxt_bj.astimezone(timezone.utc) if nxt_bj else None


def _simple_next_cron(cron_expr: str, now_bj: datetime) -> datetime | None:
    """极简 cron 解析：仅支持「分 时 * * 周」/「分 时 * * *」五字段形式。

    不支持逗号/范围/步进，仅覆盖当前种子 ``0 9 * * 1`` / ``0 8 * * *`` 这类
    无歧义表达式。周 = 0-7（0/7=周日）。返回北京时间 aware datetime。
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return None
    minute_s, hour_s, dom_s, month_s, dow_s = parts
    if dom_s != "*" or month_s != "*":
        return None
    try:
        minute = int(minute_s)
        hour = int(hour_s)
    except ValueError:
        return None
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return None

    # cron dow → Python weekday(): 0/7=周日→6, 1..6=周一..周六→0..5
    target_py_dow: int | None = None
    if dow_s != "*":
        try:
            d = int(dow_s)
        except ValueError:
            return None
        if d in (0, 7):
            target_py_dow = 6
        elif 1 <= d <= 6:
            target_py_dow = d - 1
        else:
            return None

    candidate = now_bj.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_bj:
        candidate += timedelta(days=1)

    if target_py_dow is not None:
        for _ in range(8):
            if candidate.weekday() == target_py_dow:
                break
            candidate += timedelta(days=1)
        else:
            return None
    return candidate
