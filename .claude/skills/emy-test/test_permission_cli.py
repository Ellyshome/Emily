#!/usr/bin/env python
"""权限测试 - 命令行版本

测试不同权限级别的用户向 Emily Core 发送消息，验证权限控制逻辑。
无需 Web UI，直接在命令行运行。
"""

import sys
from pathlib import Path

_skill_dir = Path(__file__).resolve().parent
if str(_skill_dir) not in sys.path:
    sys.path.insert(0, str(_skill_dir))

from config_loader import get_active_users, get_user_by_id
from tester import EmysTester


def print_header():
    print("\n" + "=" * 70)
    print("  Emily Core 权限实战测试")
    print("=" * 70 + "\n")


def print_separator(title=""):
    if title:
        print(f"\n{'=' * 30} {title} {'=' * 30}\n")
    else:
        print("\n" + "=" * 70 + "\n")


def test_user_permission(tester, user, message):
    """测试单个用户发送消息"""
    print_separator()
    print(f"👤 测试用户: {user['real_name']}")
    print(f"   权限级别: {user['permission_label']} (级别 {user['permission_level']})")
    print(f"   所属单位: {user['company_name']}")
    print(f"\n📨 发送消息: {message}\n")

    try:
        reply = tester.send_sync(
            message,
            sender_id=user['id'],
            sender_name=user['real_name'],
            conversation_type="private",
        )

        if reply and hasattr(reply, 'content'):
            print(f"✅ Emily 回复:\n")
            print(reply.content)
            print()
            print(f"   会话ID: {reply.conversation_id}")
            # 检查是否有 metadata 中的接管标记
            if reply.metadata:
                print(f"   元数据: {reply.metadata}")
        elif reply:
            print(f"✅ Emily 回复 (原始对象): {reply}")
        else:
            print("⚠️  Emily 未接管该消息，返回 None")

    except Exception as e:
        print(f"❌ 发送失败: {e}")
        import traceback
        traceback.print_exc()
        reply = None

    return reply


def main():
    print_header()

    # 获取所有用户
    print("正在加载用户列表...")
    users = get_active_users()

    if not users:
        print("❌ 未找到任何用户，请先导入测试数据")
        return

    print(f"✅ 找到 {len(users)} 个测试用户:\n")
    for i, u in enumerate(users, 1):
        print(f"  {i}. {u['real_name']} - {u['permission_label']}")

    print()

    # 初始化测试器
    print("正在连接 Emily Core...")
    try:
        tester = EmysTester()
        tester.start()
        print("✅ 已连接到 Emily Core\n")
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("\n请检查:")
        print("  1. emily-core 容器是否运行")
        print("  2. EMILY_CORE_URL 配置是否正确")
        return

    # 测试场景
    test_messages = [
        "你好，请介绍一下你自己",
        "查询我有权限查看的项目",
    ]

    print_separator("开始权限测试")

    results = []

    for user in users:
        # 只发送第一条测试消息
        reply = test_user_permission(tester, user, test_messages[0])
        results.append({
            "user": user['real_name'],
            "permission": user['permission_label'],
            "has_reply": reply is not None and hasattr(reply, 'content'),
        })

    # 总结
    print_separator("测试总结")

    print(f"{'用户':<10} {'权限级别':<12} {'有回复':<6}")
    print("-" * 35)
    for r in results:
        print(f"{r['user']:<10} {r['permission']:<12} {'✓' if r['has_reply'] else '✗':<6}")

    print()

    # 检查是否有权限差异
    replied = [r for r in results if r['has_reply']]
    print(f"收到回复消息: {len(replied)}/{len(results)}")

    if len(replied) == len(results):
        print("⚠️ 所有用户都收到回复，当前版本权限拦截可能在消息处理后生效")
        print("   请通过查看回复内容差异来验证权限控制")
    elif len(replied) == 0:
        print("⚠️ 没有消息被处理，请检查 Emily Core 容器日志")
    else:
        print("✅ 部分用户收到回复，可能存在权限差异，请检查日志验证")

    print()

    # 停止测试器
    tester.stop()
    print("测试完成！")
    print_separator()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n测试已中断")
        sys.exit(0)
