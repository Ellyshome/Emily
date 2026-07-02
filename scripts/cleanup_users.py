"""
用户表清理脚本

功能：
- 保留 wangjianguo（生态城26#地项目总）
- 逻辑删除其他所有用户

执行方式：
cd d:\app\Emily\emily-core
python ..\scripts\cleanup_users.py
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emily-core'))

from emily_core.infrastructure.database import get_session
from emily_core.infrastructure.database.models import User, UserImBinding


def cleanup_users(dry_run=True):
    """
    清理用户表

    Args:
        dry_run: 是否为试运行（只显示要删除的，不实际执行）
    """

    with get_session() as session:
        try:
            print("=" * 60)
            print("用户表清理脚本")
            print("=" * 60)

            # ==========================================
            # 1. 查看所有用户
            # ==========================================
            print("\n【1/5】查询当前所有用户...")
            all_users = session.query(User).filter(User.is_deleted == False).all()

            print(f"\n当前共有 {len(all_users)} 个活跃用户：")
            print("-" * 80)
            print(f"{'ID':<15} {'用户名':<20} {'姓名':<10} {'电话':<15} {'状态':<10}")
            print("-" * 80)

            for user in all_users:
                keep_mark = "✓ 保留" if user.username == "wangjianguo" else "  将删除"
                print(f"{user.id:<15} {user.username:<20} {user.real_name or '':<10} {user.phone or '':<15} {keep_mark}")

            # ==========================================
            # 2. 统计将要删除的用户
            # ==========================================
            users_to_delete = [u for u in all_users if u.username != "wangjianguo"]
            keep_user = next((u for u in all_users if u.username == "wangjianguo"), None)

            print("\n【2/5】统计信息：")
            print(f"  保留用户：{keep_user.real_name if keep_user else '无'} (wangjianguo)")
            print(f"  将删除用户数：{len(users_to_delete)}")

            if not users_to_delete:
                print("\n✅ 没有需要删除的用户，清理完成！")
                return True

            # ==========================================
            # 3. 确认操作
            # ==========================================
            print("\n【3/5】确认操作...")
            if dry_run:
                print("  ⚠️ 当前为试运行模式（dry_run=True），不会实际删除数据")
                print("  如需实际删除，请修改脚本中 dry_run=False")
            else:
                confirm = input("\n⚠️ 确认要删除以上用户吗？(yes/NO): ").strip().lower()
                if confirm != 'yes':
                    print("  ❌ 操作已取消")
                    return False

            # ==========================================
            # 4. 执行删除
            # ==========================================
            if not dry_run:
                print("\n【4/5】执行逻辑删除...")

                # 获取要删除的用户ID列表
                delete_ids = [u.id for u in users_to_delete]

                # 逻辑删除用户
                deleted_count = session.query(User).filter(
                    User.id.in_(delete_ids)
                ).update({
                    User.is_deleted: True,
                    User.status: 'deleted',
                }, synchronize_session=False)

                # 同时清理关联的IM绑定
                im_deleted_count = session.query(UserImBinding).filter(
                    UserImBinding.user_id.in_(delete_ids)
                ).update({
                    UserImBinding.status: 'deleted',
                }, synchronize_session=False)

                session.commit()

                print(f"  ✓ 已逻辑删除 {deleted_count} 个用户")
                print(f"  ✓ 已清理关联的 {im_deleted_count} 个IM绑定")
            else:
                print("  (试运行模式，跳过删除操作)")

            # ==========================================
            # 5. 验证结果
            # ==========================================
            print("\n【5/5】验证清理结果...")
            remaining_users = session.query(User).filter(
                User.is_deleted == False
            ).all()

            print(f"\n清理后剩余 {len(remaining_users)} 个活跃用户：")
            print("-" * 80)
            print(f"{'ID':<15} {'用户名':<20} {'姓名':<10} {'电话':<15}")
            print("-" * 80)

            for user in remaining_users:
                print(f"{user.id:<15} {user.username:<20} {user.real_name or '':<10} {user.phone or '':<15}")

            print("\n" + "=" * 60)
            if dry_run:
                print("✅ 试运行完成！确认无误后，请设置 dry_run=False 重新执行")
            else:
                print("✅ 清理完成！")
            print("=" * 60)

            return True

        except Exception as e:
            session.rollback()
            print(f"\n❌ 执行失败：{str(e)}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="用户表清理脚本")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行删除（不加此参数则为试运行）"
    )

    args = parser.parse_args()

    success = cleanup_users(dry_run=not args.execute)

    if not success:
        sys.exit(1)
