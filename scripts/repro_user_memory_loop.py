"""repro_user_memory_loop.py — 复现/回归：用户长期记忆"写而不读"缺陷。

写入通道：Agent 调 write_user_memory → UserMemoryService.save_memory 落文件。
读取通道：SessionDataFetcher.fetch() → session_snapshot.long_term_memory → {user_memory} 变量。

修复前：load_memory_context 零调用者；snapshot.long_term_memory 读 users.long_term_memory 列（无写入方）→ 恒空。
修复后：文件记忆优先注入 snapshot.long_term_memory。

用法:
    python scripts/repro_user_memory_loop.py --user-id <uuid> [--username <名>]
退出码: 0 = 写后可见（修复后预期）; 1 = 缺陷存在（两通道断开）; 2 = 参数/环境错误。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORE = _ROOT / "emily-core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

_MARK = "复现记忆条目：每周一 9 点提交周报"


def main() -> int:
    import os
    p = argparse.ArgumentParser()
    p.add_argument("--user-id", required=True, help="DB 中已存在的用户 UUID")
    p.add_argument("--username", default="", help="用户名（缺省从 DB 解析）")
    p.add_argument("--db-url", default="", help="Postgres 连接串（缺省读 EMILY_DATABASE_URL / 本机默认）")
    p.add_argument("--legacy", action="store_true",
                   help="模拟修复前：fetch 不传 core（不读记忆文件），仅看 DB 列")
    args = p.parse_args()

    db_url = args.db_url or os.environ.get("EMILY_DATABASE_URL") \
        or "postgresql://emily:emily_secret_2026@127.0.0.1:25432/emily?connect_timeout=5"

    from emily_core import bootstrap
    core = bootstrap.init({"database_url": db_url})
    core._ensure_initialized()

    svc = getattr(core, "_user_memory_service", None)
    if svc is None or not getattr(svc, "enabled", False):
        print("UNEXPECTED: UserMemoryService 未初始化或被禁用")
        return 2
    print(f"[0] UserMemoryService memory_dir={svc.memory_dir}")

    user_name = args.username
    if not user_name:
        from emily_core.repositories.user_repo import UserRepository
        u = UserRepository.get(args.user_id)
        user_name = getattr(u, "username", "") or ""
    if not user_name:
        print("UNEXPECTED: 无法解析用户名")
        return 2

    # [1] 写入通道
    title = svc.save_memory(user_name, _MARK, title="复现条目")
    if not title:
        print("UNEXPECTED: save_memory 写入失败（检查目录权限）")
        return 2
    file_memory = svc.load_memory_context(user_name)
    print(f"[1] 文件通道 load_memory_context: {len(file_memory)} 字符，含标记={_MARK in file_memory}")

    # [2] 提示词数据源通道
    from emily_core.session.session_data_fetcher import SessionDataFetcher
    result = SessionDataFetcher.fetch(args.user_id, core=(None if args.legacy else core))
    injected = (result.get("session_snapshot") or {}).get("long_term_memory", "")
    print(f"[2] 提示词数据源 session_snapshot.long_term_memory: {len(injected)} 字符，"
          f"含标记={_MARK in injected}" + ("（--legacy：不传 core）" if args.legacy else ""))

    if file_memory and _MARK in injected:
        print("PASS: 写入后 {user_memory} 数据源包含记忆内容，链路连通")
        return 0
    if file_memory and not injected:
        print("REPRO OK: 缺陷复现 — 记忆已写入文件，但 snapshot.long_term_memory 为空；"
              "load_memory_context 零调用者。")
        return 1
    if file_memory and injected and _MARK not in injected:
        print("REPRO OK: 缺陷复现 — snapshot 取的是 DB 列值（无写入方），未包含文件记忆。")
        return 1
    print("UNEXPECTED: 文件内容为空或判定异常")
    return 2


if __name__ == "__main__":
    sys.exit(main())
