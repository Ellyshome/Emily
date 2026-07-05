"""fetch_session_data.py —— 快速查看 Session 采集数据。

用法：
  uv run python scripts/fetch_session_data.py <user-id>
  uv run python scripts/fetch_session_data.py chenzhe-jyzx-2026-0001
  uv run python scripts/fetch_session_data.py chenzhe-jyzx-2026-0001 --db-url postgresql://emily:emily_secret_2026@localhost:25432/emily
"""

import sys
import json
import argparse
from pathlib import Path

# 将 emily-core/ 加入 sys.path，使 emily_core 包可被 import
_project_root = Path(__file__).resolve().parent.parent
_emily_core_dir = _project_root / "emily-core"
if _emily_core_dir.is_dir() and str(_emily_core_dir) not in sys.path:
    sys.path.insert(0, str(_emily_core_dir))

DB_URL_DEFAULT = "postgresql://emily:emily_secret_2026@localhost:25432/emily"


def main():
    parser = argparse.ArgumentParser(description="快速查看 Session 采集数据")
    parser.add_argument("user_id", help="用户 ID")
    parser.add_argument("--db-url", default=DB_URL_DEFAULT, help="PostgreSQL 连接 URL")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")

    from emily_core.infrastructure.database import init_db
    init_db(db_url=args.db_url)

    from emily_core.session.session_data_fetcher import SessionDataFetcher
    result = SessionDataFetcher.fetch(user_id=args.user_id)

    snap = result["session_snapshot"]

    print("=== 基本信息 ===")
    print(f"  user_name:         {snap.get('user_name', '?')}")
    print(f"  user_position:     {snap.get('user_position', '?')}")
    print(f"  permission_level:  {snap.get('permission_level', '?')}")
    print(f"  project_name:      {snap.get('project_name', '?')}")
    print(f"  company_name:      {snap.get('company_name', '?')}")
    print()
    print("=== 原子化能力 ===")
    print(f"  available_tools:   {snap.get('available_tools', [])}")
    print(f"  visible_schema:\n{snap.get('visible_schema_summary', '（空）')}")
    print(f"  visible_files:     {snap.get('visible_files_summary', '（空）')}")
    print(f"  rag_available:     {snap.get('rag_available', False)}")
    print(f"  rag_collections:   {snap.get('rag_collections', [])}")
    print()
    if result.get("errors"):
        print(f"  errors: {result['errors']}")

    # 同时输出完整 JSON 到文件
    json_path = f"session_data_{args.user_id[:16]}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整 JSON 已写入: {json_path}")


if __name__ == "__main__":
    main()
