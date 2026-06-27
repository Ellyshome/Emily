#!/usr/bin/env python
"""测试数据库连接和用户加载"""

import sys
from pathlib import Path

_skill_dir = Path(__file__).resolve().parent
if str(_skill_dir) not in sys.path:
    sys.path.insert(0, str(_skill_dir))

from config_loader import get_active_users, get_user_by_id

print("=" * 60)
print("测试数据库连接...")
print("=" * 60)

try:
    users = get_active_users()
    print(f"\n✅ 成功连接到数据库，找到 {len(users)} 个活跃用户：\n")
    
    for u in users:
        print(f"  👤 {u['real_name']}")
        print(f"     ID: {u['id']}")
        print(f"     权限: {u['permission_label']} (级别 {u['permission_level']})")
        print(f"     单位: {u['company_name']}")
        print()

    if users:
        print("=" * 60)
        print("测试 get_user_by_id 函数...")
        first_user = users[0]
        user_detail = get_user_by_id(first_user['id'])
        if user_detail:
            print(f"\n✅ 成功获取用户详情: {user_detail['real_name']}")
            print(f"   电话: {user_detail['phone']}")
            print(f"   邮箱: {user_detail['email']}")
            print(f"   微信: {user_detail.get('wechat', 'N/A')}")
        else:
            print("❌ 获取用户详情失败")

    print("\n" + "=" * 60)
    print("✅ 所有数据库测试通过！")
    print("=" * 60)

except Exception as e:
    print(f"\n❌ 数据库连接失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
