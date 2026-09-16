"""身份解析与测试会话判定 —— 修复前后对照探针（live，连真实容器）。

用途：同一份脚本在「修复前 / 修复后」各跑一次，逐场景对照结论。

场景（每个场景用独立 conversation_id，避开真实用户会话，不触发真实 IM 出站）：
  S1 通道账号兜底    platform=wecom，sender_id=<users.wechat 值>，conv=conv_ident_01
  S2 未知通道 ID     platform=napcat，sender_id=huang_zq_qq，conv=conv_ident_02
  S3 测试会话判定    platform=napcat，sender_id=<真实 QQ>，conv=test_guard_01

判定口径：
  S1 修复后应解析到 users 表用户（is_guest=False 且 user_id=c4b8e33f-…），
     并在 user_im_bindings 自动补一条 wecom 绑定；修复前为访客（is_guest=True）。
  S2 修复前后都应保持「未登记 → 访客」，且 users 表不得新增 huang_zq_qq 用户；
     修复后改由 CLI/工具侧在发送前拦下（见 cli.py --sender-id 校验）。
  S3 命中测试前缀的会话，SSE reply 事件应带 test_session=True（出站只走 SSE）。

用法：
    python .claude/skills/emy-test/test_identity_and_outbound.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

import aiohttp
from sqlalchemy import create_engine, text

from config_loader import get_api_token, get_core_url, get_db_url
from tester import EmysTester

REAL_USERNAME = "黄志强"
REAL_QQ = "123456010"
REAL_WECHAT = "wx_黄志强"
UNKNOWN_ID = "huang_zq_qq"

SCENARIOS = [
    {
        "id": "S1",
        "name": "通道账号兜底（wecom/wechat 列）",
        "platform": "wecom",
        "sender_id": REAL_WECHAT,
        "sender_name": REAL_USERNAME,
        "cid": "conv_ident_01",
        "text": "你好，问一下项目情况",
    },
    {
        "id": "S2",
        "name": "未知通道 ID（人名拼音）",
        "platform": "napcat",
        "sender_id": UNKNOWN_ID,
        "sender_name": REAL_USERNAME,
        "cid": "conv_ident_02",
        "text": "喂，那个...我今天干活了",
    },
    {
        "id": "S3",
        "name": "出站静默标记（test_ 前缀会话）",
        "platform": "napcat",
        "sender_id": REAL_QQ,
        "sender_name": REAL_USERNAME,
        "cid": "test_guard_01",
        "text": "你好，问一下项目情况",
    },
]


# ══════════════════════════════════════════════════════════════════
# SSE 原始帧捕获（独立于 tester：用于观测事件字段，如 channel_silent）
# ══════════════════════════════════════════════════════════════════

class _RawSSECapture:
    """后台抓取 /api/v1/events/outbound 的原始帧（event + data dict）。"""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self._task: asyncio.Task | None = None
        self._stop = False

    async def _run(self) -> None:
        url = f"{get_core_url()}/api/v1/events/outbound"
        headers = {}
        token = get_api_token()
        if token:
            headers["X-Emily-Token"] = token
        try:
            async with aiohttp.ClientSession(headers=headers) as s:
                async with s.get(url) as resp:
                    etype, buf = "message", []
                    async for raw in resp.content:
                        if self._stop:
                            break
                        line = raw.decode("utf-8", errors="ignore").rstrip("\r\n")
                        if line == "":
                            if buf:
                                self._collect(etype, "\n".join(buf))
                            etype, buf = "message", []
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            etype = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            buf.append(line[len("data:"):].strip())
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] SSE 捕获中断: {e}")

    def _collect(self, etype: str, data_str: str) -> None:
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            return
        self.frames.append({"event": etype, "data": data})

    async def __aenter__(self) -> "_RawSSECapture":
        self._task = asyncio.ensure_future(self._run())
        await asyncio.sleep(1.0)  # 等 SSE 建连
        return self

    async def __aexit__(self, *_exc) -> bool:
        self._stop = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return False

    def reply_frames(self, cid: str) -> list[dict]:
        return [f for f in self.frames
                if f["event"] == "reply" and f["data"].get("conversation_id") == cid]


# ══════════════════════════════════════════════════════════════════
# DB 观测
# ══════════════════════════════════════════════════════════════════

def _db():
    return create_engine(get_db_url())


def _archive_row(engine, cid: str) -> dict | None:
    """取该会话最新一条归档索引行（同一 conv 历史多行，取 last_active_at 最新）。"""
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT user_id, user_name, is_guest, platform, im_user_id, last_active_at "
            "FROM session_archives WHERE conversation_id = :cid "
            "ORDER BY last_active_at DESC LIMIT 1"
        ), {"cid": cid}).fetchone()
    if row is None:
        return None
    return {"user_id": row[0] or "", "user_name": row[1] or "",
            "is_guest": bool(row[2]), "platform": row[3] or "", "im_user_id": row[4] or "",
            "last_active_at": row[5] or ""}


def _binding_count(engine, platform: str, im_user_id: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM user_im_bindings "
            "WHERE im_platform = :p AND im_user_id = :u"
        ), {"p": platform, "u": im_user_id}).scalar_one()


def _user_count_by_name(engine, name: str) -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT count(*) FROM users WHERE username = :n"
        ), {"n": name}).scalar_one()


def _real_user_id(engine, username: str) -> str:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT id FROM users WHERE username = :n AND is_deleted = false"
        ), {"n": username}).scalar_one_or_none() or ""


# ══════════════════════════════════════════════════════════════════
# 前置：场景可重复所需的状态复位
# ══════════════════════════════════════════════════════════════════

def _reset_preconditions(engine) -> None:
    """复位 S1 的前置（删掉兜底自动补建的 wecom 绑定），使「绑定缺失」成立。"""
    with engine.begin() as conn:
        deleted = conn.execute(text(
            "DELETE FROM user_im_bindings "
            "WHERE im_platform = 'wecom' AND im_user_id = :u"
        ), {"u": REAL_WECHAT}).rowcount
    print(f"[reset] 删除 wecom 绑定 {REAL_WECHAT}: {deleted} 行（复现「档案有账号、绑定表无行」）")


async def _terminate_session(cid: str) -> None:
    """终止该会话（清 SessionLoopPool 缓存），保证每次探针都重新拉起上下文。"""
    url = f"{get_core_url()}/api/v1/session/terminate"
    headers = {}
    token = get_api_token()
    if token:
        headers["X-Emily-Token"] = token
    try:
        async with aiohttp.ClientSession(headers=headers) as s:
            async with s.post(url, json={"conversation_id": cid}) as resp:
                body = await resp.json()
                print(f"   [reset] terminate {cid}: {body.get('terminated')}")
    except Exception as e:  # noqa: BLE001
        print(f"   [reset] terminate {cid} 失败（忽略）: {e}")


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

async def _run() -> dict:
    engine = _db()
    real_uid = _real_user_id(engine, REAL_USERNAME)
    print("=" * 78)
    print(f"身份解析与出站静默对照探针  |  core={get_core_url()}")
    print(f"真实用户: {REAL_USERNAME} id={real_uid} qq={REAL_QQ} wechat={REAL_WECHAT}")
    print("=" * 78)

    results: dict[str, dict] = {}
    _reset_preconditions(engine)
    async with _RawSSECapture() as cap:
        for sc in SCENARIOS:
            print(f"\n── {sc['id']} {sc['name']}")
            print(f"   platform={sc['platform']} sender_id={sc['sender_id']} conv={sc['cid']}")
            outcome: dict = {"scenario": sc["id"], "name": sc["name"]}

            await _terminate_session(sc["cid"])   # 清会话缓存，强制重新拉起身份

            # ① 发送（复用 astrbot 插件的收发链路）
            with EmysTester() as emy:
                reply = await emy.send_message(
                    sc["text"], sender_id=sc["sender_id"], sender_name=sc["sender_name"],
                    platform=sc["platform"], conversation_type="private",
                    conversation_id=sc["cid"],
                )
            outcome["reply"] = (reply.content[:60] + "…") if reply and reply.content else None
            print(f"   reply: {outcome['reply']}")

            # ② 会话归属（归档索引行）
            await asyncio.sleep(1.0)
            row = _archive_row(engine, sc["cid"])
            outcome["archive"] = row
            if row:
                print(f"   archive: user_id={row['user_id'] or '(none)'} "
                      f"guest={row['is_guest']} user_name={row['user_name']}")

            # ③ 出站事件字段
            frames = cap.reply_frames(sc["cid"])
            test_flags = [f["data"].get("test_session") for f in frames]
            outcome["sse_reply_frames"] = len(frames)
            outcome["test_session"] = test_flags
            outcome["sse_keys"] = sorted(frames[0]["data"].keys()) if frames else []
            print(f"   SSE reply frames={len(frames)} test_session={test_flags}")

            # ④ 判定
            if sc["id"] == "S1":
                ok = bool(row) and row["user_id"] == real_uid and not row["is_guest"]
                outcome["expect"] = f"解析到 {real_uid}（guest=False）"
                outcome["pass"] = ok
                outcome["binding_wecom_after"] = _binding_count(engine, "wecom", REAL_WECHAT)
            elif sc["id"] == "S2":
                leaked = _user_count_by_name(engine, UNKNOWN_ID) > 0
                ok = bool(row) and row["is_guest"]
                outcome["expect"] = "未登记 → 建访客会话（guest=True）且 users 表无该 ID 用户"
                outcome["pass"] = ok and not leaked
                outcome["users_leak"] = leaked
            else:
                ok = all(flag is True for flag in test_flags) and bool(test_flags)
                outcome["expect"] = "命中测试前缀 → SSE reply 事件带 test_session=True"
                outcome["pass"] = ok

            print(f"   → expect: {outcome['expect']}")
            print(f"   → pass: {outcome['pass']}")
            results[sc["id"]] = outcome

    print("\n" + "=" * 78)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("=" * 78)
    passed = sum(1 for r in results.values() if r.get("pass"))
    print(f"通过 {passed}/{len(results)}")
    return results


if __name__ == "__main__":
    asyncio.run(_run())
