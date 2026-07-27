"""CLI 入口 —— argparse / demo / REPL。

用法: python emys_tester.py [options]

用户模拟策略（贴近 AstrBot 真实行为）：
  - 自动从 users 表枚举活跃用户供选择
  - 用 QQ 号作为 sender_id（与 AstrBot 行为一致）
  - platform 默认 "napcat"
  - 私聊 conversation_id = QQ号（与 AstrBot 行为一致）

用例：
uv run python .claude/skills/emy-test/cli.py --managed --llm --message "帮我查一下翠湖庭院项目的整体进度情况" --sender "李景利"

"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from config_loader import get_llm_config, get_active_users
from tester import EmysTester


def _resolve_sender(
    sender_name: str | None,
    sender_id: str | None,
    qq: str | None,
) -> dict:
    """解析发送者信息，自动从 users 表补全。

    优先级：
    1. --qq 指定 QQ 号 → 用 QQ 号作为 sender_id（与 AstrBot 行为一致）
    2. --sender-id 指定 → 直接使用（走 UUID 直查路径，需在 users 表中存在）
    3. --sender 指定用户名 → 从 users 表查找匹配用户，提取 QQ 号
    4. 都未指定 → 交互式选择

    Returns:
        dict: {sender_id, sender_name, qq, platform, user_record}
    """
    result = {
        "sender_id": "",
        "sender_name": "Tester",
        "qq": "",
        "platform": "napcat",
        "user_record": None,
    }

    # ① --qq 指定 QQ 号 → 直接用
    if qq:
        result["sender_id"] = qq
        result["qq"] = qq
        result["sender_name"] = sender_name or f"QQ用户{qq}"
        return result

    # ② --sender-id 指定 → 直接使用（UUID 或其他 ID）
    if sender_id:
        result["sender_id"] = sender_id
        result["sender_name"] = sender_name or sender_id[:8]
        return result

    # ③ --sender 指定用户名 → 从 users 表查找
    if sender_name:
        users = get_active_users()
        for u in users:
            uname = u.get("display_name", "") or u.get("username", "")
            if uname == sender_name:
                result["user_record"] = u
                result["sender_name"] = uname
                # 优先用 IM 绑定的用户 ID（即真实 QQ 号）作为 sender_id
                # 回退到 users.qq → users.phone → UUID
                im_uid = u.get("im_user_id", "")
                uqq = im_uid or u.get("qq", "") or u.get("phone", "")
                if uqq:
                    result["sender_id"] = uqq
                    result["qq"] = uqq
                    result["platform"] = u.get("im_platform", "") or "napcat"
                else:
                    result["sender_id"] = u["id"]
                return result

    # ④ 都未指定 → 交互式枚举选择
    return _interactive_user_selection()


def _interactive_user_selection() -> dict:
    """交互式用户选择：枚举 users 表活跃用户，让测试者选择。

    Returns:
        dict: {sender_id, sender_name, qq, platform, user_record}
    """
    result = {
        "sender_id": "",
        "sender_name": "Tester",
        "qq": "",
        "platform": "napcat",
        "user_record": None,
    }

    users = get_active_users()
    if not users:
        print("⚠️  未找到活跃用户，使用默认测试身份")
        result["sender_id"] = f"test_{uuid.uuid4().hex[:8]}"
        return result

    print("\n📋 可选测试用户：")
    print("─" * 60)
    for i, u in enumerate(users, 1):
        uname = u.get("display_name", "") or u.get("username", "未知")
        level = u.get("permission_label", f"L{u.get('permission_level', '?')}")
        company = u.get("company_name", "未分配单位")
        uqq = u.get("qq", "") or u.get("phone", "")
        qq_display = f"QQ:{uqq}" if uqq else "无QQ号"
        uid_short = u["id"][:8]
        print(f"  {i}. {uname} ({level}, {company}) [{qq_display}, ID:{uid_short}...]")
    print(f"  0. 使用自定义身份（不选用户）")
    print("─" * 60)

    while True:
        try:
            choice = input("请选择用户编号 [0-{}]: ".format(len(users))).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            sys.exit(0)

        if choice == "0":
            result["sender_id"] = f"test_{uuid.uuid4().hex[:8]}"
            return result

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(users):
                u = users[idx]
                result["user_record"] = u
                result["sender_name"] = u.get("display_name", "") or u.get("username", "")
                # 优先用 IM 绑定的用户 ID（即真实 QQ 号）作为 sender_id
                im_uid = u.get("im_user_id", "")
                uqq = im_uid or u.get("qq", "") or u.get("phone", "")
                if uqq:
                    result["sender_id"] = uqq
                    result["qq"] = uqq
                    result["platform"] = u.get("im_platform", "") or "napcat"
                else:
                    result["sender_id"] = u["id"]
                print(f"✅ 已选择: {result['sender_name']} (sender_id={result['sender_id']})")
                return result
            else:
                print(f"  ❌ 无效编号，请输入 0-{len(users)}")
        except ValueError:
            print("  ❌ 请输入数字")


def demo():
    """运行演示：用 EmysTester 模拟几条消息（使用真实用户身份）。"""
    print("=" * 60)
    print("  EmysTester Demo — Emily Core 容器接口测试")
    print("=" * 60)

    # 从 users 表获取真实用户
    users = get_active_users()
    if not users:
        print("⚠️  未找到活跃用户，使用默认测试身份")
        demo_users = [
            {"sender_id": "test_user_1", "sender_name": "Alice"},
            {"sender_id": "test_user_2", "sender_name": "Bob"},
        ]
    else:
        # 取前两个用户（按权限排序）
        demo_users = []
        for u in users[:2]:
            uqq = u.get("qq", "") or u.get("phone", "") or u["id"]
            demo_users.append({
                "sender_id": uqq,
                "sender_name": u.get("display_name", "") or u.get("username", ""),
            })

    with EmysTester() as emy:
        # ── 1. 私聊问候（接管） ──
        print(f"\n[1] 私聊消息（用户: {demo_users[0]['sender_name']}）")
        reply = emy.send_sync(
            "你好",
            sender_id=demo_users[0]["sender_id"],
            sender_name=demo_users[0]["sender_name"],
        )
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None)")

        # ── 2. 群聊 @bot（接管）
        print(f"\n[2] 群聊消息（用户: {demo_users[1]['sender_name']}）")
        reply = emy.send_sync(
            "@Emily 你是谁",
            sender_id=demo_users[1]["sender_id"],
            sender_name=demo_users[1]["sender_name"],
            conversation_type="group",
            group_id="group_001",
            is_at_bot=True,
        )
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None)")

        # ── 3. 查看注册用户 ──
        print("\n[3] 已注册用户:")
        try:
            db_users = emy.get_users()
            for u in db_users:
                print(
                    f"  {u['user_id'][:8]}... {u['display_name']} "
                    f"(IM: {u['im_platform']}/{u['im_user_id']})"
                )
        except Exception as e:
            print(f"  (无法查询: {e})")

    print("\n" + "=" * 60)
    print("  Demo 完成 [OK]")
    print("=" * 60)


def repl(emy: "EmysTester", cid: str, sender_name: str, sender_id: str = "", platform: str = "napcat") -> None:
    """交互式 REPL 模式。在同一进程内持续对话，保持上下文。

    Args:
        emy: 已启动的 EmysTester 实例。
        cid: 会话 ID。
        sender_name: 发送者名称。
        sender_id: 发送者 ID（QQ 号或 UUID）。
        platform: IM 平台，默认 "napcat"。
    """
    llm_cfg = get_llm_config()
    print(f"╔══════════════════════════════════════════════════════╗")
    print(f"║   EmysTester REPL — 输入消息与 Emily 持续对话      ║")
    print(f"║   发送者: {sender_name:<38} ║")
    print(f"║   sender_id: {sender_id[:36]:<36} ║")
    print(f"║   会话ID: {cid:<38} ║")
    if llm_cfg:
        print(f"║   LLM:    {llm_cfg.get('model', '?'):<38} ║")
    else:
        print(f"║   模式:    Mock（无 LLM）                           ║")
    print(f"╠══════════════════════════════════════════════════════╣")
    print(f"║  命令: /exit 退出  /reset 清除上下文               ║")
    print(f"║         /users 用户  /msgs 消息  /group 群聊模式   ║")
    print(f"║         /private 私聊模式  /at 切换@bot  /help 帮助  ║")
    print(f"╚══════════════════════════════════════════════════════╝")
    print()

    state = {
        "conversation_type": "private",
        "group_id": "group_001",
        "is_at_bot": True,
    }

    while True:
        try:
            raw = input("You → ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[退出]")
            break

        if not raw:
            continue

        # ── 命令处理 ──
        if raw.startswith("/"):
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "/exit":
                break
            elif cmd == "/help":
                print("  命令: /exit /reset /users /msgs /group /private /at /help")
            elif cmd == "/reset":
                emy.reset_conversation(cid)
                print("[已清除会话上下文]")
            elif cmd == "/users":
                try:
                    users = emy.get_users()
                    if users:
                        for u in users:
                            print(f"  {u['user_id'][:8]}... {u['display_name']} "
                                  f"(IM: {u['im_platform']}/{u['im_user_id']})")
                    else:
                        print("  (无注册用户)")
                except Exception as e:
                    print(f"  (查询失败: {e})")
            elif cmd == "/msgs":
                try:
                    msgs = emy.get_messages(cid, limit=20)
                    if msgs:
                        for m in reversed(msgs):
                            who = m['sender_name'] or "Emily"
                            print(f"  [{who}] {m['content'][:100]}")
                    else:
                        print("  (无消息记录)")
                except Exception as e:
                    print(f"  (查询失败: {e})")
            elif cmd == "/group":
                state["conversation_type"] = "group"
                print(f"[切换到群聊模式, group_id={state['group_id']}, "
                      f"is_at_bot={state['is_at_bot']}]")
            elif cmd == "/private":
                state["conversation_type"] = "private"
                print("[切换到私聊模式]")
            elif cmd == "/at":
                state["is_at_bot"] = not state["is_at_bot"]
                print(f"[is_at_bot = {state['is_at_bot']}]")
            else:
                print(f"  未知命令: {cmd} （/help 查看帮助）")
            continue

        # ── 发送消息 ──
        is_group = state["conversation_type"] == "group"
        reply = emy.send_sync(
            raw,
            sender_id=sender_id or None,
            sender_name=sender_name,
            platform=platform,
            conversation_id=cid,
            conversation_type=state["conversation_type"],
            group_id=state["group_id"] if is_group else None,
            is_at_bot=state["is_at_bot"] if is_group else False,
        )

        if reply:
            print(f"Emily → {reply.content}")
        else:
            print("[不接管，无回复]")
        print()


def main():
    """脚本入口。"""
    parser = argparse.ArgumentParser(
        description="EmysTester — Emily Core 生产环境实战测试工具"
    )

    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="交互式 REPL 模式（同一进程中持续多轮对话，保持上下文）",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="启用 LLM 模式（当前 LLM 配置由 emily-core 服务端控制，此标志保留向后兼容）",
    )
    parser.add_argument(
        "--managed",
        action="store_true",
        help="使用 managed 接管模式（接管所有消息）",
    )
    parser.add_argument(
        "--message",
        type=str,
        default=None,
        help="发送单条消息并打印回复",
    )
    # ── 用户身份参数（三选一，优先级: --qq > --sender-id > --sender）──
    parser.add_argument(
        "--qq",
        type=str,
        default=None,
        help="发送者 QQ 号（作为 sender_id 传入，与 AstrBot 行为一致。推荐使用此参数）",
    )
    parser.add_argument(
        "--sender-id",
        type=str,
        default=None,
        help="发送者 UUID（走 Core UUID 直查路径，需在 users 表中存在）",
    )
    parser.add_argument(
        "--sender",
        type=str,
        default=None,
        help="发送者名称（从 users 表自动查找匹配用户，提取 QQ 号作为 sender_id）",
    )
    parser.add_argument(
        "--cid",
        type=str,
        default=None,
        help="会话 ID（与 --message 配合，跨进程多轮需保持一致）",
    )
    parser.add_argument(
        "--file",
        type=str,
        action="append",
        default=None,
        dest="files",
        help="附件文件路径（可多次指定），模拟 QQ/微信发送文件",
    )
    # ── 群聊模拟参数 ──
    parser.add_argument(
        "--conversation-type",
        type=str,
        choices=["private", "group"],
        default=None,
        help="会话类型: private(私聊) / group(群聊)，不指定时根据 --cid 自动推断",
    )
    parser.add_argument(
        "--group-id",
        type=str,
        default=None,
        help="群号（群聊时有效，默认自动生成 group_001）",
    )
    parser.add_argument(
        "--no-at",
        action="store_true",
        default=False,
        help="群聊时不 @机器人（默认群聊 @机器人）。用于测试静默收集场景",
    )

    args = parser.parse_args()

    # ── 解析发送者身份（自动从 users 表枚举选择）──
    sender_info = _resolve_sender(
        sender_name=args.sender,
        sender_id=args.sender_id,
        qq=args.qq,
    )

    # ── 推导 conversation_id（与 AstrBot 行为一致）──
    # 私聊: conversation_id = sender_id（QQ 号）
    # 群聊: conversation_id = group_id
    # 如果用户指定了 --cid 则优先使用
    # 自动推断：--cid 以 group_ 开头 → 群聊模式
    if args.conversation_type is None:
        if args.cid and args.cid.startswith("group_"):
            args.conversation_type = "group"
        else:
            args.conversation_type = "private"
    is_group = args.conversation_type == "group"
    group_id = args.group_id or "group_001"

    if args.cid:
        cid = args.cid
    elif is_group:
        cid = group_id
    elif sender_info["qq"]:
        # 有 QQ 号时用 QQ 号作为 conversation_id（与 AstrBot 行为一致）
        cid = sender_info["qq"]
    else:
        cid = f"once_{uuid.uuid4().hex[:8]}"

    if args.interactive:
        # ── REPL 模式 ──
        use_llm = args.llm or bool(get_llm_config())
        with EmysTester(use_llm=use_llm) as emy:
            repl(emy, cid, sender_info["sender_name"], sender_info["sender_id"], sender_info.get("platform", "napcat"))
    elif args.message:
        # ── 单条消息模式 ──
        use_llm = args.llm or bool(get_llm_config())

        with EmysTester(use_llm=use_llm) as emy:
            # 构建附件
            attachments = None
            if args.files:
                attachments = []
                for fpath in args.files:
                    p = Path(fpath)
                    if not p.exists():
                        print(f"[警告] 文件不存在: {fpath}", file=sys.stderr)
                        continue
                    # 推断附件类型
                    ext = p.suffix.lower()
                    atype = 3  # 默认 file
                    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
                        atype = 2  # image
                    elif ext in (".mp3", ".wav", ".ogg", ".aac", ".m4a"):
                        atype = 4  # voice
                    elif ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
                        atype = 5  # video
                    attachments.append({
                        "type": atype,
                        "url": p.as_uri(),
                        "file_name": p.name,
                        "file_size": p.stat().st_size,
                    })

            reply = emy.send_sync(
                args.message,
                sender_id=sender_info["sender_id"],
                sender_name=sender_info["sender_name"],
                platform=sender_info.get("platform", "napcat"),
                conversation_id=cid,
                conversation_type=args.conversation_type,
                group_id=group_id if is_group else None,
                is_at_bot=(not args.no_at) if is_group else False,
                attachments=attachments,
            )
            if reply:
                print(reply.content)
            else:
                print("[不接管，无回复]")

            # 显示 Bot 发出的文件
            sent_files = emy.sent_files
            if sent_files:
                print("\n📎 Emily 发送的文件:")
                for sf in sent_files:
                    name = sf.get("name", "file")
                    caption = sf.get("caption", "")
                    label = f"  {name}" + (f" — {caption}" if caption else "")
                    print(f"{label}")
                    print(f"    路径: {sf['path']}")
    else:
        # ── 演示模式 ──
        demo()


if __name__ == "__main__":
    main()
