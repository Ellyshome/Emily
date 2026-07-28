"""snapshot — 统一信息快照采集模块（进化模块专用）。

一次采集，供进化闭环、认知闭环两个模块共同消费。
晨报模块独立采集，不在此处。

用法（import）:
    from emily_core.snapshot import SnapshotCollector
    collector = SnapshotCollector()
    snapshot = await collector.collect("2026-07-27", days=1)

用法（CLI）:
    uv run python scripts/snapshot.py --date 2026-07-27
    uv run python scripts/snapshot.py --date 2026-07-27 --json
"""

from .collector import SnapshotCollector, collect_snapshot

__all__ = ["SnapshotCollector", "collect_snapshot"]
