"""OpsScheduler — 运维调度执行器。

由 ProjectAgent._do_tick() 在 advisory lock 保护下同步调用。
不启动独立 asyncio.Task，不获取/释放 advisory lock。

关键约束：
  • run_tick() 是纯同步方法（非 async）
  • 不持有 asyncio 引用（不启动后台任务）
  • 不获取 PostgreSQL advisory lock（由 ProjectAgent 管理）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .probe_base import TickContext
from .probe_registry import ProbeRegistry

if TYPE_CHECKING:
    from .config import OpsConfig

logger = logging.getLogger("emily.ops")


class OpsScheduler:
    """运维调度执行器。由 ProjectAgent._do_tick() 在 advisory lock 保护下同步调用。"""

    def __init__(
        self,
        config: "OpsConfig",
        db_repo,
        fallback,
        email_service=None,
        outbound_bus=None,
    ):
        self._config = config
        self._registry = ProbeRegistry()
        self._db_repo = db_repo
        self._fallback = fallback
        self._email_service = email_service
        self._outbound_bus = outbound_bus
        self._consecutive_failures: dict[str, int] = {}

    def register_probe(self, probe) -> None:
        """注册一个运维探针。"""
        self._registry.register(probe)

    def run_tick(self, tick_id: str, tick_number: int) -> dict:
        """执行一轮 Tick 巡检。

        遍历所有启用的探针，安全运行每个探针（失败隔离），
        持久化结果，必要时生成冷启动报告。

        Args:
            tick_id: 本轮 Tick 的唯一 ID（UUID 字符串）
            tick_number: 累计 Tick 计数

        Returns:
            dict: {"probes_run", "findings_total", "errors"}
        """
        ctx = TickContext(
            tick_id=tick_id,
            tick_number=tick_number,
            start_time=datetime.now(timezone.utc),
        )

        probe_results = []
        for probe in self._registry.get_enabled_probes():
            result = self._run_probe_safe(probe, ctx)
            probe_results.append(result)

        self._persist_results(ctx, probe_results)

        if self._is_cold_start():
            self._generate_startup_report(ctx, probe_results)

        return {
            "probes_run": len(probe_results),
            "findings_total": sum(
                r.get("findings_count", 0) for r in probe_results
            ),
            "errors": [r for r in probe_results if r["status"] == "FAILED"],
        }

    def _run_probe_safe(self, probe, ctx: TickContext) -> dict:
        """安全运行单个探针。失败不影响其他探针。"""
        try:
            if not probe.should_run(ctx):
                return {"probe": probe.name(), "status": "SKIPPED"}

            findings = probe.run(ctx)
            ctx.set_last_run_time(probe.name(), datetime.now(timezone.utc))
            self._consecutive_failures[probe.name()] = 0
            return {
                "probe": probe.name(),
                "status": "SUCCESS",
                "findings_count": len(findings) if findings else 0,
                "findings": findings,
            }
        except Exception as e:
            failures = self._consecutive_failures.get(probe.name(), 0) + 1
            self._consecutive_failures[probe.name()] = failures
            if failures >= 3:
                logger.warning(
                    "Probe '%s' failed %d consecutive times",
                    probe.name(), failures,
                )
            return {"probe": probe.name(), "status": "FAILED", "error": str(e)}

    def _persist_results(self, ctx: TickContext, results: list[dict]) -> None:
        """持久化 Tick 结果（DB 优先，失败降级到本地文件）。"""
        try:
            self._db_repo.save_tick_results(ctx, results)
        except Exception as e:
            logger.error("DB persist failed, fallback to MD: %s", e)
            try:
                self._fallback.write_tick_results(ctx, results)
            except Exception as fe:
                logger.error("Fallback write also failed: %s", fe)

    def _is_cold_start(self) -> bool:
        """查 DB 判断是否为冷启动（最近 24h 无启动报告）。

        不依赖内存计数器（_tick_count），避免进程重启误判。
        """
        try:
            return self._db_repo.get_latest_startup_report(hours=24) is None
        except Exception:
            # DB 不可用时假设为冷启动（保守策略）
            return True

    def _generate_startup_report(
        self, ctx: TickContext, probe_results: list[dict]
    ) -> None:
        """生成冷启动报告，双通道发送 + DB 持久化。"""
        from .startup_report import generate_startup_report

        report = generate_startup_report(ctx, self._config)
        sent_any = False

        # 通道 1: 邮件
        if self._email_service and self._config.mailbox_enabled:
            try:
                _send_email_report_sync(self._email_service, report)
                sent_any = True
            except Exception as e:
                logger.warning("Startup report email send failed: %s", e)

        # 通道 2: IM
        if self._outbound_bus:
            try:
                self._outbound_bus.publish(
                    "reply", {"text": report["report_content"]}
                )
                sent_any = True
            except Exception as e:
                logger.warning("Startup report IM send failed: %s", e)

        # 持久化到 DB
        try:
            self._db_repo.save_startup_report(report)
        except Exception as e:
            logger.error("Save startup report failed, fallback to MD: %s", e)
            self._fallback.write_startup_report(report)
            return

        # 标记已发送
        if sent_any:
            try:
                self._db_repo.mark_report_sent(report["tick_id"])
            except Exception:
                pass

    def status(self) -> dict:
        """返回运维模块的运行状态摘要。"""
        return {
            "enabled": self._config.enabled,
            "probes_registered": len(self._registry.get_all()),
            "probes_enabled": len(self._registry.get_enabled_probes()),
            "consecutive_failures": dict(self._consecutive_failures),
        }


def _send_email_report_sync(email_service, report: dict) -> None:
    """同步发送邮件报告（桥接异步 EmailService.send()）。

    使用新线程 + asyncio.run() 在同步上下文中调用异步发送。
    """
    import asyncio
    import concurrent.futures
    from emily_core.providers.email.base import EmailCredentials

    # 从 report 中提取收件人信息（如有配置）
    # 此处使用最小化凭证（空值=不可用时不发送）
    creds = EmailCredentials(
        smtp_host="",
        smtp_port=465,
        imap_host="",
        imap_port=993,
        username="",
        password="",
    )

    async def _send():
        # 实际发送需要有效的 SMTP 凭证，此处为占位
        # 启动报告通过 IM 通道发送为主要路径
        pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, _send())
            return future.result(timeout=30)
    else:
        return asyncio.run(_send())
