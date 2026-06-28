"""FallbackWriter — 优雅降级写入器。

当 DB 持久化失败时，自动将运维数据写入本地文件（.md + .jsonl），
确保数据不丢失。支持三种数据类型：tick 结果、启动报告、邮箱错误。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("emily.ops.fallback")


class FallbackWriter:
    """优雅降级写入器。

    当 DB 不可用时，将运维数据写入本地文件目录：
    {log_dir}/ops_degraded/

    每个写入操作同时生成 .md 和 .jsonl 两种格式。
    """

    def __init__(self, log_dir: str = "logs/"):
        self._log_dir = Path(log_dir)
        self._degraded_dir = self._log_dir / "ops_degraded"
        self._degraded_dir.mkdir(parents=True, exist_ok=True)

    def _make_filename(self, data_type: str, tick_number: int) -> str:
        """生成降级文件名。

        Args:
            data_type: "tick" / "startup" / "mailbox"
            tick_number: 当前 Tick 编号
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"ops_fallback_{data_type}_{tick_number:06d}_{ts}"

    def _write_jsonl(self, path: Path, entry: dict) -> None:
        """追加一行 JSONL。"""
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _write_md(self, path: Path, content: str) -> None:
        """写入 Markdown 文件。"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    # ── 公开接口 ──

    def write_tick_results(self, ctx, results: list[dict]) -> None:
        """降级写入 Tick 结果（.md + .jsonl）。"""
        base = self._make_filename("tick", ctx.tick_number)
        md_path = self._degraded_dir / f"{base}.md"
        jsonl_path = self._degraded_dir / f"{base}.jsonl"

        # Markdown 摘要
        lines = [
            f"# Ops Tick 降级日志 — Tick #{ctx.tick_number}",
            f"",
            f"- **Tick ID**: {ctx.tick_id}",
            f"- **开始时间**: {ctx.start_time.isoformat()}",
            f"- **探针数**: {len(results)}",
            f"",
            f"## 探针结果",
            f"",
        ]
        for r in results:
            status = r.get("status", "UNKNOWN")
            probe = r.get("probe", "unknown")
            icon = "✅" if status == "SUCCESS" else ("⚠️" if status == "SKIPPED" else "❌")
            lines.append(f"- {icon} **{probe}**: {status}")
            if status == "FAILED":
                lines.append(f"  - 错误: {r.get('error', '')}")
            if status == "SUCCESS":
                lines.append(f"  - 发现数: {r.get('findings_count', 0)}")
                for f in r.get("findings", []):
                    if hasattr(f, 'message'):
                        lines.append(f"    - {f.finding_type}: {f.message}")
        lines.append("")

        self._write_md(md_path, "\n".join(lines))

        # JSONL（可机器解析）
        for r in results:
            entry = {
                "tick_id": ctx.tick_id,
                "tick_number": ctx.tick_number,
                "probe": r.get("probe", "unknown"),
                "status": r.get("status", "UNKNOWN"),
                "findings_count": r.get("findings_count", 0),
                "error": r.get("error", ""),
            }
            self._write_jsonl(jsonl_path, entry)

        logger.info(
            "Fallback: tick results written to %s",
            self._degraded_dir / base,
        )

    def write_startup_report(self, report: dict) -> None:
        """降级写入冷启动报告。"""
        tick_number = report.get("tick_number", 0)
        base = self._make_filename("startup", tick_number)
        md_path = self._degraded_dir / f"{base}.md"

        self._write_md(md_path, report.get("report_content", ""))

        logger.info(
            "Fallback: startup report written to %s",
            md_path,
        )

    def write_mail_error(self, ctx, error_msg: str) -> None:
        """降级写入邮箱错误。"""
        base = self._make_filename("mailbox", ctx.tick_number)
        md_path = self._degraded_dir / f"{base}.md"
        jsonl_path = self._degraded_dir / f"{base}.jsonl"

        self._write_md(md_path, f"# Ops Mail Error\n\n{error_msg}\n")

        self._write_jsonl(jsonl_path, {
            "tick_id": ctx.tick_id,
            "tick_number": ctx.tick_number,
            "error": error_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        logger.info(
            "Fallback: mail error written to %s",
            md_path,
        )
