"""backfill_audit_source.py — 存量留痕回填流量性质（操作留痕治理 FR-13）。

规则（见 `issues/操作留痕治理/操作留痕治理_计划_V1.md` §2.7）：

  business_event_logs
    - `event_action` 以 `console_` 开头        → ops
    - 其余                                     → user
      （改造前 business_event_logs 的写入点只有 IM 链路 + console 三处，
        故非 console_* 即为 IM 真实流量）
    - `result` **不回填**（不猜三态，存量为空表示"改造前无此维度"）

  messages
    - `event_id` 以 `console_chat_` 开头       → test
    - `direction='user_to_agent'` 的其余行     → user
    - 出站行（`direction<>'user_to_agent'`）   → 继承同会话最近一条入站行的 source，取不到 → auto

用法：
    # 预览（默认，不写库）
    uv run python scripts/backfill_audit_source.py

    # 实际写入
    uv run python scripts/backfill_audit_source.py --apply

    # 预览样本条数（默认 20）
    uv run python scripts/backfill_audit_source.py --sample 40
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CORE_DIR = _HERE.parent / "emily-core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

CONSOLE_ACTION_PREFIX = "console_"
CONSOLE_EVENT_PREFIX = "console_chat_"

SQL_BEL_COUNTS = """
SELECT
  count(*) FILTER (WHERE COALESCE(source, '') = '' AND event_action LIKE :console_like) AS to_ops,
  count(*) FILTER (WHERE COALESCE(source, '') = '' AND event_action NOT LIKE :console_like) AS to_user,
  count(*) FILTER (WHERE COALESCE(source, '') <> '') AS already
FROM business_event_logs
"""

SQL_MSG_COUNTS = """
SELECT
  count(*) FILTER (WHERE COALESCE(source, '') = '' AND event_id LIKE :chat_like) AS to_test,
  count(*) FILTER (WHERE COALESCE(source, '') = ''
                     AND event_id NOT LIKE :chat_like
                     AND direction = 'user_to_agent') AS to_user,
  count(*) FILTER (WHERE COALESCE(source, '') = ''
                     AND direction <> 'user_to_agent') AS outbound,
  count(*) FILTER (WHERE COALESCE(source, '') <> '') AS already
FROM messages
"""

SQL_BEL_SAMPLE = """
SELECT id, event_action, COALESCE(source, '') AS source,
       CASE WHEN event_action LIKE :console_like THEN 'ops' ELSE 'user' END AS target
FROM business_event_logs
WHERE COALESCE(source, '') = ''
ORDER BY created_at DESC
LIMIT :lim
"""

SQL_MSG_SAMPLE = """
SELECT event_id, direction, COALESCE(source, '') AS source,
       CASE WHEN event_id LIKE :chat_like THEN 'test'
            WHEN direction = 'user_to_agent' THEN 'user'
            ELSE '(继承会话)' END AS target
FROM messages
WHERE COALESCE(source, '') = ''
ORDER BY created_at DESC
LIMIT :lim
"""

SQL_UPD_BEL_OPS = """
UPDATE business_event_logs SET source = 'ops'
WHERE COALESCE(source, '') = '' AND event_action LIKE :console_like
"""

SQL_UPD_BEL_USER = """
UPDATE business_event_logs SET source = 'user'
WHERE COALESCE(source, '') = '' AND event_action NOT LIKE :console_like
"""

SQL_UPD_MSG_TEST = """
UPDATE messages SET source = 'test'
WHERE COALESCE(source, '') = '' AND event_id LIKE :chat_like
"""

SQL_UPD_MSG_USER = """
UPDATE messages SET source = 'user'
WHERE COALESCE(source, '') = ''
  AND event_id NOT LIKE :chat_like
  AND direction = 'user_to_agent'
"""

# 出站行继承同会话最近一条入站行的 source（无入站命中则 auto）
SQL_UPD_MSG_OUTBOUND = """
UPDATE messages m SET source = COALESCE((
    SELECT CASE WHEN COALESCE(i.source, '') <> '' THEN i.source ELSE 'user' END
    FROM messages i
    WHERE i.conversation_id = m.conversation_id
      AND i.direction = 'user_to_agent'
    ORDER BY i.created_at DESC
    LIMIT 1
), 'auto')
WHERE COALESCE(m.source, '') = '' AND m.direction <> 'user_to_agent'
"""


def backfill(*, apply: bool = False, sample: int = 20, db_url: str = "") -> dict:
    """执行回填；apply=False 时只统计与抽样，不写库。"""
    from sqlalchemy import text as sa_text

    from emily_core.infrastructure.database.session import get_session, init_db

    init_db(db_url)

    params = {
        "console_like": f"{CONSOLE_ACTION_PREFIX}%",
        "chat_like": f"{CONSOLE_EVENT_PREFIX}%",
        "lim": max(1, int(sample)),
    }

    with get_session() as session:
        bel = session.execute(sa_text(SQL_BEL_COUNTS), params).mappings().one()
        msg = session.execute(sa_text(SQL_MSG_COUNTS), params).mappings().one()
        bel_samples = list(session.execute(sa_text(SQL_BEL_SAMPLE), params).mappings())
        msg_samples = list(session.execute(sa_text(SQL_MSG_SAMPLE), params).mappings())

        applied = {}
        if apply:
            applied["business_event_logs.ops"] = session.execute(
                sa_text(SQL_UPD_BEL_OPS), params).rowcount
            applied["business_event_logs.user"] = session.execute(
                sa_text(SQL_UPD_BEL_USER), params).rowcount
            applied["messages.test"] = session.execute(
                sa_text(SQL_UPD_MSG_TEST), params).rowcount
            applied["messages.user"] = session.execute(
                sa_text(SQL_UPD_MSG_USER), params).rowcount
            applied["messages.outbound_inherited"] = session.execute(
                sa_text(SQL_UPD_MSG_OUTBOUND), params).rowcount
            session.commit()

    return {
        "apply": apply,
        "business_event_logs": dict(bel),
        "messages": dict(msg),
        "applied": applied,
        "bel_samples": [dict(r) for r in bel_samples],
        "msg_samples": [dict(r) for r in msg_samples],
    }


def _print_report(result: dict) -> None:
    mode = "实际写入" if result["apply"] else "预览（未写库）"
    print(f"存量留痕回填 — {mode}")
    print("=" * 56)
    bel = result["business_event_logs"]
    print("\n[business_event_logs] 待回填：")
    print(f"  → ops  : {bel.get('to_ops', 0)} 条（event_action 以 console_ 开头）")
    print(f"  → user : {bel.get('to_user', 0)} 条（IM 链路）")
    print(f"  已有 source : {bel.get('already', 0)} 条")

    msg = result["messages"]
    print("\n[messages] 待回填：")
    print(f"  → test : {msg.get('to_test', 0)} 条（event_id 以 console_chat_ 开头）")
    print(f"  → user : {msg.get('to_user', 0)} 条（入站）")
    print(f"  → 继承 : {msg.get('outbound', 0)} 条（出站，继承同会话入站来源，取不到则 auto）")
    print(f"  已有 source : {msg.get('already', 0)} 条")

    if result["apply"]:
        print("\n已写入：")
        for k, v in result["applied"].items():
            print(f"  {k}: {v}")

    print(f"\n样本（business_event_logs，{len(result['bel_samples'])} 条）：")
    for r in result["bel_samples"]:
        print(f"  {r['event_action']:<24} 当前='{r['source']}' → {r['target']}")

    print(f"\n样本（messages，{len(result['msg_samples'])} 条）：")
    for r in result["msg_samples"]:
        print(f"  {str(r['event_id'])[:32]:<34} {r['direction']:<15} 当前='{r['source']}' → {r['target']}")

    if not result["apply"]:
        print("\n提示：以上为预览。确认无误后加 --apply 实际写入。")


def main() -> None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="存量留痕回填流量性质（操作留痕治理）")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    parser.add_argument("--sample", type=int, default=20, help="预览样本条数（默认 20）")
    parser.add_argument("--db-url", default="", help="PostgreSQL 连接 URL（默认取环境变量）")
    args = parser.parse_args()

    result = backfill(apply=args.apply, sample=args.sample, db_url=args.db_url)
    _print_report(result)


if __name__ == "__main__":
    main()
