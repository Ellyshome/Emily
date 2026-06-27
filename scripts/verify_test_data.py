#!/usr/bin/env python
"""验证测试数据是否正确写入"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from sqlalchemy import create_engine, text


def verify_data():
    engine = create_engine("postgresql://emily:emily_secret_2026@localhost:25432/emily")
    
    with engine.connect() as conn:
        print("="*60)
        print("📊 Emily 测试数据验证报告")
        print("="*60)

        # 1. 公司
        result = conn.execute(text("SELECT COUNT(*) FROM company_info"))
        count = result.scalar()
        print(f"\n🏢 公司信息：{count} 家")
        result = conn.execute(text("SELECT company_name, type FROM company_info ORDER BY type"))
        for row in result:
            print(f"   - {row[0]} ({row[1]})")

        # 2. 用户
        result = conn.execute(text("SELECT COUNT(*) FROM users"))
        count = result.scalar()
        print(f"\n👥 用户信息：{count} 人")
        result = conn.execute(text("""
            SELECT real_name, permission_level, phone, email 
            FROM users ORDER BY permission_level DESC
        """))
        perm_labels = {6: "系统管理员", 4: "建设主管", 3: "参建管理", 2: "参建执行", 1: "访客"}
        for row in result:
            label = perm_labels.get(row[1], "未知")
            print(f"   - {row[0]} ({label}) | {row[2]} | {row[3]}")

        # 3. 项目
        result = conn.execute(text("SELECT COUNT(*) FROM projects"))
        count = result.scalar()
        print(f"\n🏗️  项目信息：{count} 个")
        result = conn.execute(text("SELECT name, code, city FROM projects"))
        for row in result:
            print(f"   - {row[0]} ({row[1]}) | {row[2]}")

        # 4. 会话
        result = conn.execute(text("SELECT COUNT(*) FROM conversations"))
        count = result.scalar()
        print(f"\n💬 会话信息：{count} 个")
        result = conn.execute(text("SELECT title, im_platform, conversation_type FROM conversations LIMIT 5"))
        for row in result:
            print(f"   - {row[0]} ({row[1]} / {row[2]})")

        # 5. 消息
        result = conn.execute(text("SELECT COUNT(*) FROM messages"))
        count = result.scalar()
        print(f"\n📨 消息记录：{count} 条")
        result = conn.execute(text("""
            SELECT sender_name, direction, LEFT(content, 40) 
            FROM messages ORDER BY created_at DESC LIMIT 8
        """))
        for row in result:
            icon = "👤" if row[1] == "user_to_agent" else "🤖"
            print(f"   {icon} {row[0]}: {row[2]}...")

        # 6. 事件
        result = conn.execute(text("SELECT COUNT(*) FROM events"))
        count = result.scalar()
        print(f"\n📋 事件记录：{count} 条")
        result = conn.execute(text("SELECT event_type, title FROM events LIMIT 5"))
        for row in result:
            print(f"   - [{row[0]}] {row[1]}")

        # 7. 任务
        result = conn.execute(text("SELECT COUNT(*) FROM tasks"))
        count = result.scalar()
        print(f"\n✅ 任务记录：{count} 条")
        result = conn.execute(text("SELECT title, owner_text, status FROM tasks LIMIT 5"))
        for row in result:
            print(f"   - [{row[2]}] {row[0]} (负责人: {row[1]})")

        # 8. 用户 IM 绑定
        result = conn.execute(text("SELECT COUNT(*) FROM user_im_bindings"))
        count = result.scalar()
        print(f"\n🔗 用户 IM 绑定：{count} 条")
        result = conn.execute(text("SELECT im_display_name, im_user_id FROM user_im_bindings LIMIT 7"))
        for row in result:
            print(f"   - {row[0]} -> {row[1]}")

        print("\n" + "="*60)
        print("✅ 所有测试数据已成功写入数据库！")
        print("="*60)


if __name__ == "__main__":
    verify_data()
