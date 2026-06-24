"""CLI 入口 —— argparse / demo / REPL。

用法: python emys_tester.py [options]
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

from config_loader import get_llm_config
from tester import EmysTester


def demo():
    """运行演示：用 EmysTester 模拟几条消息。"""
    print("=" * 60)
    print("  EmysTester Demo — Emily Core 容器接口测试")
    print("=" * 60)

    with EmysTester() as emy:
        # ── 1. 私聊问候（接管） ──
        print("\n[1] 私聊消息（总是接管）")
        reply = emy.send_sync("你好", sender_name="Alice")
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None)")

        # ── 2. 群聊未 @bot（不接管） ──
        print("\n[2] 群聊消息（未 @bot，不接管）")
        reply = emy.send_sync(
            "今天天气不错",
            conversation_type="group",
            group_id="group_001",
            sender_name="Bob",
            is_at_bot=False,
        )
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None) -- 符合预期")

        # ── 3. 群聊 @bot（接管） ──
        print("\n[3] 群聊消息（@bot，接管）")
        reply = emy.send_sync(
            "@Emily 你是谁",
            conversation_type="group",
            group_id="group_001",
            sender_name="Bob",
            is_at_bot=True,
        )
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None)")

        # ── 4. 自我介绍（快速通道回复） ──
        print("\n[4] 自我介绍请求")
        reply = emy.send_sync("你叫什么名字", sender_name="Charlie")
        if reply:
            print(f"  Emily → {reply.content!r}")
        else:
            print("  [不接管] (None)")

        # ── 5. 查看持久化消息 ──
        print("\n[5] 已持久化的消息:")
        try:
            msgs = emy.get_messages()
            for m in msgs:
                print(
                    f"  [{m['created_at']}] {m['sender_name']}: "
                    f"{m['content'][:50]} → takeover={m['takeover']}"
                )
        except Exception as e:
            print(f"  (无法查询: {e})")

        # ── 6. 查看注册用户 ──
        print("\n[6] 已注册用户:")
        try:
            users = emy.get_users()
            for u in users:
                print(
                    f"  {u['user_id']}: {u['real_name']} "
                    f"(IM: {u['im_platform']}/{u['im_user_id']})"
                )
        except Exception as e:
            print(f"  (无法查询: {e})")

    print("\n" + "=" * 60)
    print("  Demo 完成 [OK]")
    print("=" * 60)


def repl(emy: "EmysTester", cid: str, sender_name: str) -> None:
    """交互式 REPL 模式。在同一进程内持续对话，保持上下文。

    Args:
        emy: 已启动的 EmysTester 实例。
        cid: 会话 ID。
        sender_name: 发送者名称。
    """
    llm_cfg = get_llm_config()
    print(f"╔══════════════════════════════════════════════════════╗")
    print(f"║   EmysTester REPL — 输入消息与 Emily 持续对话      ║")
    print(f"║   发送者: {sender_name:<38} ║")
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
                            print(f"  {u['user_id'][:8]}... {u['real_name']} "
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
            sender_name=sender_name,
            conversation_type=state["conversation_type"],
            conversation_id=cid,
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
    parser.add_argument(
        "--sender",
        type=str,
        default="Tester",
        help="发送者名称（与 --message 配合）",
    )
    parser.add_argument(
        "--sender-id",
        type=str,
        default=None,
        help="发送者稳定 ID（与 --message 配合，同一发送者跨轮次保持一致）",
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

    args = parser.parse_args()

    if args.interactive:
        # ── REPL 模式 ──
        sender = args.sender
        cid = args.cid or f"repl_{uuid.uuid4().hex[:8]}"
        use_llm = args.llm or bool(get_llm_config())
        with EmysTester(use_llm=use_llm) as emy:
            repl(emy, cid, sender)
    elif args.message:
        # ── 单条消息模式 ──
        cid = args.cid or f"once_{uuid.uuid4().hex[:8]}"
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
                sender_name=args.sender,
                sender_id=args.sender_id,
                conversation_id=cid,
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
