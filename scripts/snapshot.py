"""snapshot 采集工具 — 从项目根目录运行的便捷入口。

用法:
    uv run python scripts/snapshot.py --date 2026-07-27
    uv run python scripts/snapshot.py --date 2026-07-27 --json --days 7
"""

import sys
import os

# 将 emily-core 加入 sys.path，使 emily_core 包可被发现
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from emily_core.snapshot.__main__ import main

if __name__ == "__main__":
    main()
