"""emytest_chat.py — 消息模拟器：以 astrbot 插件同构方式向 emily-core 发送一条消息并观察回复。

定位：ScriptManager 脚本控制台（emily-core `/console/`）里的「特殊工具」——
不是批处理脚本，而是一次入站消息的端到端模拟：

    POST /api/v1/message/send     → 200 短路同步回复 / 204 异步处理
    GET  /api/v1/events/outbound  → SSE 取回异步 reply / progress / file_send

会话上下文由 emily-core 的 SessionPoolManager 按 sender_id 维持，因此**同一发送者**
连续执行即可完成「拟录入 → 确认 → 查询」这类多轮确认测试。

⚠ 必须使用 users 表中的真实用户：伪造 sender_id 会让 Core 自动 INSERT 一个 level=1
   访客，PermissionSnapshot 为空、业务全走降级路径，测的不是生产场景。
   故本脚本发送前强制校验用户存在（数据库不可达时降级为告警，不阻断）。

双通道：
    CLI    : uv run python scripts/emytest_chat.py --message "你好" --sender <UUID/用户名>
    控制台 : /console/ 左侧选「消息模拟器」，填消息内容 + 发送者下拉 + 参数复选框
             （writes_db=true → 执行前弹二次确认；未确认时只跑 --dry-run 预览）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_CORE_URL = "http://127.0.0.1:18080"
PLATFORMS = ["simulator", "napcat", "wechat", "dingtalk", "feishu"]
LEVEL_LABELS = {1: "访客", 2: "参建执行", 3: "参建管理", 4: "建设主管", 5: "管理员", 6: "系统管理员"}

# 直连 Core：绕过 HTTP(S)_PROXY（容器内设有 mitmproxy 代理）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _core_url(explicit: str = "") -> str:
    """Core 地址：显式参数 > EMILY_CORE_URL > 本机默认端口。"""
    return (explicit or os.environ.get("EMILY_CORE_URL", DEFAULT_CORE_URL)).rstrip("/")


def _headers() -> dict:
    """请求头（含可选的 X-Emily-Token）。"""
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("EMILY_API_TOKEN", "")
    if token:
        headers["X-Emily-Token"] = token
    return headers


def lookup_user(value: str) -> tuple[dict | None, str]:
    """按 UUID 或用户名从 users 表取真实用户。

    Returns:
        (user, verdict)，verdict ∈ {"found", "missing", "unknown"}：
        "unknown" = 数据库不可达，无法校验（调用方应告警但不阻断）。
    """
    dsn = os.environ.get("EMILY_DATABASE_URL", "")
    if not dsn:
        print("⚠ 未设置 EMILY_DATABASE_URL，跳过 users 表存在性检查", file=sys.stderr)
        return None, "unknown"
    try:
        import psycopg2

        conn = psycopg2.connect(dsn, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT u.id, u.username, u.level, u.status, u.is_deleted, "
                    "       COALESCE(c.company_name, '') "
                    "FROM users u LEFT JOIN company_info c ON c.id = u.company "
                    "WHERE u.id = %s OR u.username = %s",
                    (value, value),
                )
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as ex:  # noqa: BLE001 — 校验失败不阻断主流程
        print(f"⚠ 无法校验发送者（{ex}），跳过 users 表存在性检查", file=sys.stderr)
        return None, "unknown"

    if not row:
        return None, "missing"
    return {
        "id": row[0],
        "username": row[1] or "",
        "level": row[2],
        "active": row[3] == "active" and not row[4],
        "company": row[5],
    }, "found"


class _SseCollector(threading.Thread):
    """后台读取 /api/v1/events/outbound，收集与本次发送相关的事件。

    reply / progress / file_send 三类事件都按 conversation_id（兜底
    reply_to_message_id）过滤，避免多路并发时串台。
    """

    def __init__(self, url: str, headers: dict, conversation_id: str, message_id: str):
        super().__init__(daemon=True)
        self._url = url
        self._headers = headers
        self._conv_id = conversation_id
        self._msg_id = message_id

        self.ready = threading.Event()      # 连接已建立（或已失败）
        self.connected = False
        self.reply_event = threading.Event()
        self.reply: dict | None = None
        self.progress: list[str] = []
        self.files: list[dict] = []
        self.error = ""
        self._resp: Any = None

    def run(self) -> None:
        try:
            req = urllib.request.Request(self._url, headers=self._headers, method="GET")
            self._resp = _OPENER.open(req, timeout=30)
            self.connected = True
            self.ready.set()

            event_type, data_buf = "message", []
            for raw in self._resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    if data_buf:
                        self._handle(event_type, "\n".join(data_buf))
                    event_type, data_buf = "message", []
                elif line.startswith(":"):
                    continue                      # 心跳注释
                elif line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_buf.append(line[len("data:"):].strip())
        except Exception as ex:  # noqa: BLE001 — 连接被关闭/超时属正常收尾
            self.error = str(ex)
        finally:
            self.ready.set()

    def _handle(self, event_type: str, data_str: str) -> None:
        try:
            data = json.loads(data_str) if data_str else {}
        except json.JSONDecodeError:
            return
        if data.get("conversation_id", "") != self._conv_id \
                and data.get("reply_to_message_id") != self._msg_id:
            return

        if event_type == "reply":
            self.reply = data
            self.reply_event.set()
        elif event_type == "progress":
            text = data.get("content", "")
            if text:
                self.progress.append(text)
        elif event_type == "file_send":
            caption = data.get("caption", "")
            for fp in data.get("file_paths") or []:
                self.files.append({"path": fp, "name": Path(fp).name, "caption": caption})

    def close(self) -> None:
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:  # noqa: BLE001
            pass


def simulate(
    message: str,
    sender: str,
    *,
    platform: str = "simulator",
    conversation_type: str = "private",
    group_id: str = "",
    is_at_bot: bool = False,
    timeout: int = 120,
    dry_run: bool = False,
    core_url: str = "",
) -> dict:
    """发送一条模拟入站消息并取回 Emily 的回复。

    Args:
        message: 消息文本。
        sender: 发送者（users 表 UUID 或用户名），最终以用户 UUID 作为 sender_id。
        platform: 来源平台标识。
        conversation_type: "private" / "group"。
        group_id: 群号（群聊时生效）。
        is_at_bot: 群聊时是否 @机器人。
        timeout: 等待异步回复的秒数。
        dry_run: 只打印将发送的报文，不实际发送。
        core_url: Core 地址，空则取 EMILY_CORE_URL。

    Returns:
        {"success", "reply", "progress", "files", "status", "elapsed", "error"}
    """
    core_url = _core_url(core_url)
    is_group = conversation_type == "group"
    gid = (group_id or "").strip() or None

    # ── 发送者校验：必须是 users 表中的真实用户 ──
    user, verdict = lookup_user(sender)
    if verdict == "missing":
        print(f"❌ 发送者 '{sender}' 不在 users 表中。伪造 sender_id 会让 Core 自动创建 "
              f"level=1 访客并降级到访客路径，测试结果不可信。")
        print("   请从控制台「发送者」下拉中选择真实用户，或改用 users 表中的 UUID / 用户名。")
        return {"success": False, "reply": None, "progress": [], "files": [],
                "status": None, "elapsed": 0.0, "error": "sender not found"}

    sender_id = user["id"] if user else sender
    sender_name = user["username"] if user else sender
    conv_id = gid if (is_group and gid) else sender_id

    msg_id = f"emytest_{uuid.uuid4().hex[:12]}"
    payload = {
        "message_id": msg_id,
        "platform": platform,
        "conversation_type": conversation_type,
        "conversation_id": conv_id,
        "sender_id": sender_id,
        "sender_name": sender_name,
        "group_id": gid if is_group else None,
        "group_name": None,
        "content": message,
        "is_at_bot": bool(is_at_bot) if is_group else False,
        "mentioned_user_ids": [],
        "msg_type": 1,
        "attachments": [],
        "event_id": f"emytest_{uuid.uuid4().hex[:12]}",
    }

    header = _print_header(user, sender_id, message, platform, conversation_type, gid, is_group, is_at_bot)

    if dry_run:
        print("\n".join(header))
        print(f"🔍 预览模式（未发送）。将 POST {core_url}/api/v1/message/send")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return {"success": True, "reply": None, "progress": [], "files": [],
                "status": None, "elapsed": 0.0, "error": "", "dry_run": True}

    # ── 先连 SSE 再发消息：确保异步回复不会在订阅建立前被广播掉 ──
    collector = _SseCollector(f"{core_url}/api/v1/events/outbound", _headers(), conv_id, msg_id)
    collector.start()
    collector.ready.wait(timeout=10)
    if not collector.connected:
        collector.close()
        print("\n".join(header))
        print(f"❌ 无法连接 emily-core SSE（{core_url}）：{collector.error or '连接失败'}")
        print("   请确认容器已启动（docker-compose up -d emily-core）。")
        return {"success": False, "reply": None, "progress": [], "files": [],
                "status": None, "elapsed": 0.0, "error": collector.error or "sse connect failed"}

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            f"{core_url}/api/v1/message/send",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=_headers(),
            method="POST",
        )
        with _OPENER.open(req, timeout=timeout) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as ex:
        collector.close()
        detail = ex.read().decode("utf-8", errors="replace")[:300]
        print("\n".join(header))
        print(f"❌ 发送失败：HTTP {ex.code} {detail}")
        return {"success": False, "reply": None, "progress": [], "files": [],
                "status": ex.code, "elapsed": time.monotonic() - t0, "error": detail}
    except Exception as ex:  # noqa: BLE001
        collector.close()
        print("\n".join(header))
        print(f"❌ 发送失败：{ex}")
        return {"success": False, "reply": None, "progress": [], "files": [],
                "status": None, "elapsed": time.monotonic() - t0, "error": str(ex)}

    # ── 取回复：200 同步短路回复；204 等 SSE reply 事件 ──
    reply_content: str | None = None
    if status == 200 and body.strip():
        try:
            reply_content = json.loads(body).get("content", "")
        except json.JSONDecodeError:
            reply_content = body.strip()
    elif status == 204:
        if collector.reply_event.wait(timeout):
            reply_content = (collector.reply or {}).get("content", "")

    elapsed = time.monotonic() - t0
    collector.close()

    print("\n".join(header))
    print(f"📡 HTTP     : {status}   （耗时 {elapsed:.1f}s）")
    print("─" * 56)
    if collector.progress:
        print("📈 前导消息 :")
        for text in collector.progress:
            print(f"   · {text}")
    if collector.files:
        print("📎 发送文件 :")
        for f in collector.files:
            label = f["name"] + (f" — {f['caption']}" if f["caption"] else "")
            print(f"   · {label}  ({f['path']})")
    if reply_content is not None:
        print("🤖 Emily 回复 :")
        print(reply_content or "（空回复）")
    else:
        print(f"⏸ 未收到回复 : {timeout}s 内无 SSE reply 事件（未接管，或处理耗时超过等待上限）")
        print(f"   会话 {conv_id} 的上下文已由 Core 保留，可加大 --timeout 后重试。")

    return {"success": True, "reply": reply_content, "progress": collector.progress,
            "files": collector.files, "status": status, "elapsed": elapsed, "error": ""}


def _print_header(user: dict | None, sender_id: str, message: str, platform: str,
                  conversation_type: str, gid: str | None, is_group: bool,
                  is_at_bot: bool) -> list[str]:
    """构造结果头（信息块），返回待打印行。"""
    if user:
        label = LEVEL_LABELS.get(user.get("level"), f"L{user.get('level')}")
        who = f"{user.get('username') or sender_id}  [{label} · {user.get('company') or '未分配单位'}]"
    else:
        who = sender_id
    convo = conversation_type + (f"  群号={gid}" if is_group and gid else "")
    return [
        "═" * 56,
        "Emily 消息模拟器 — 直连 emily-core（模拟 astrbot 插件）",
        "═" * 56,
        f"👤 发送者   : {who}",
        f"🆔 sender_id: {sender_id}",
        f"📨 会话     : {convo}   平台={platform}   @机器人={'是' if is_group and is_at_bot else '否'}",
        f"💬 消息     : {message}",
        "─" * 56,
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(description="Emily 消息模拟器（模拟 astrbot 插件收发消息）")
    parser.add_argument("--message", default="", help="消息文本")
    parser.add_argument("--sender", default="", help="发送者：users 表 UUID 或用户名")
    parser.add_argument("--platform", default="simulator", choices=PLATFORMS, help="来源平台")
    parser.add_argument("--conversation-type", default="private", choices=["private", "group"])
    parser.add_argument("--group-id", default="", help="群号（群聊时生效）")
    parser.add_argument("--at-bot", action="store_true", dest="at_bot", help="群聊时 @机器人")
    parser.add_argument("--timeout", type=int, default=120, help="等待异步回复的秒数")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="只打印将发送的报文，不实际发送")
    args = parser.parse_args(argv)

    # 控制台的写库预览通道只传 check_arg（即 --dry-run），此时不带任何业务参数
    if args.dry_run and not (args.message and args.sender):
        print("预览模式：未提供 --message / --sender，无可发送内容。")
        print("在控制台填入「消息内容」+ 选择「发送者」后点「执行」，弹窗确认后才真正发送。")
        return 0

    if not args.message:
        print("错误：缺少 --message（消息文本）", file=sys.stderr)
        return 1
    if not args.sender:
        print("错误：缺少 --sender（users 表 UUID 或用户名）", file=sys.stderr)
        return 1

    result = simulate(
        args.message,
        args.sender,
        platform=args.platform,
        conversation_type=args.conversation_type,
        group_id=args.group_id,
        is_at_bot=args.at_bot,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
