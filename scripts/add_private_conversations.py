#!/usr/bin/env python
"""
添加私聊对话测试数据
====================
为每个用户创建独立的私聊会话，模拟一对一咨询场景
"""

import sys
import os
import json
import uuid
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def get_db_session():
    url = "postgresql://emily:emily_secret_2026@localhost:25432/emily"
    engine = create_engine(url, echo=False)
    Session = sessionmaker(bind=engine)
    return Session()


def generate_id():
    return str(uuid.uuid4())


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_users(session):
    """获取测试用户"""
    result = session.execute(text("""
        SELECT id, real_name, username, permission_level
        FROM users
        WHERE username IN (
            'admin_wang', 'pm_li', 'designer_zhao',
            'engineer_zhang', 'supervisor_chen',
            'worker_sun', 'guest_zhou'
        )
        ORDER BY permission_level DESC
    """))
    users = []
    for row in result:
        users.append({
            "id": row[0],
            "real_name": row[1],
            "username": row[2],
            "permission_level": row[3],
        })
    return users


def create_private_conversation(session, user, conv_index):
    """为单个用户创建私聊会话"""
    conv_id = generate_id()
    conv_biz_id = f"private_{user['username']}_{conv_index}"

    # 创建会话
    session.execute(text("""
        INSERT INTO conversations (
            id, im_platform, conversation_type, conversation_id,
            title, takeover_mode, created_at, updated_at
        ) VALUES (
            :id, 'simulator', 'private', :conv_biz_id,
            :title, 'collaborate', :now, :now
        )
        ON CONFLICT DO NOTHING
    """), {
        "id": conv_id,
        "conv_biz_id": conv_biz_id,
        "title": f"Emy 助手 - {user['real_name']}",
        "now": utc_now_iso(),
    })
    session.commit()

    # 获取会话 ID
    conv_result = session.execute(text(
        "SELECT id FROM conversations WHERE conversation_id = :cid"
    ), {"cid": conv_biz_id}).fetchone()
    actual_conv_id = conv_result[0] if conv_result else conv_id

    print(f"\n👤 {user['real_name']} 的私聊对话")

    # 根据权限级别生成不同的对话内容
    messages = generate_private_messages(user, actual_conv_id)

    msg_count = 0
    for i, msg in enumerate(messages):
        sql = text("""
            INSERT INTO messages (
                id, event_id, conversation_id, sender_im_id, sender_name,
                sender_user_id, content, message_type, direction,
                is_at_bot, takeover, status, created_at
            ) VALUES (
                :id, :event_id, :conv_id, :sender_im_id, :sender_name,
                :sender_user_id, :content, 'text', :direction,
                :is_at_bot, :takeover, 'processed', :created_at
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        msg_id = generate_id()
        created_at = (datetime.now(timezone.utc) -
                      timedelta(hours=len(messages)-i, minutes=conv_index*30)).isoformat()

        result = session.execute(sql, {
            "id": msg_id,
            "event_id": f"priv_{conv_index}_{msg_id[:8]}",
            "conv_id": actual_conv_id,
            "sender_im_id": msg["sender_im"],
            "sender_name": msg["sender_name"],
            "sender_user_id": msg.get("sender_user_id"),
            "content": msg["content"],
            "direction": msg["direction"],
            "is_at_bot": msg["is_at_bot"],
            "takeover": msg["takeover"],
            "created_at": created_at,
        })

        if result.fetchone():
            msg_count += 1
            icon = "👤" if msg["direction"] == "user_to_agent" else "🤖"
            print(f"  {icon} {msg['content'][:50]}...")

    session.commit()
    return msg_count


def generate_private_messages(user, conv_id):
    """根据用户权限级别生成不同的对话内容"""
    name = user["real_name"]
    pl = user["permission_level"]
    im_id = f"sim_{user['username']}"

    # 系统管理员 - 完整权限对话
    if pl == 6:
        return [
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "Emy，帮我查询整个系统的用户情况。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": f"好的{name}！当前系统用户概况：\n\n👥 总用户数：23 人\n📊 权限分布：\n  - 系统管理员：1 人\n  - 建设主管：2 人\n  - 参建管理：6 人\n  - 参建执行：2 人\n  - 访客：12 人\n\n🏢 公司数量：5 家\n🏗️  项目数量：3 个\n💬 总消息数：14 条\n\n需要查看详细的用户列表吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "把所有系统管理员列出来。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": "📋 系统管理员列表：\n\n1. 王总（admin_wang）\n   - 公司：XX地产集团有限公司\n   - 权限：最高级（全功能）\n   - 手机：13800000001\n   - 邮箱：wangzong@xxdc.com\n\n2. 测试管理员（test_admin）\n   - 公司：系统内置\n   - 备注：测试专用\n\n需要我为哪个管理员修改权限吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
        ]

    # 建设主管 - 项目管理
    elif pl == 4:
        return [
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "Emy，帮我查看锦绣花园项目的整体情况。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": f"好的{name}！锦绣花园住宅小区项目概况：\n\n📊 项目状态：进行中\n📍 位置：上海市浦东新区\n📈 整体进度：42%\n⏱️ 当前阶段：设计阶段\n\n👥 项目团队：\n  - 甲方：李经理（您本人）\n  - 设计院：赵工\n  - 总包：张工\n  - 监理：陈监理\n\n📋 本周新增任务：3 个\n⚠️ 待处理问题：1 个\n\n需要查看详细的进度报表吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "导出项目周报。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": "📋 锦绣花园项目周报已生成！\n\n📄 报告类型：PDF 格式\n📊 报告内容：\n  - 本周完成工作总结\n  - 下周工作计划\n  - 风险预警与建议\n  - 资源需求统计\n\n🔗 下载链接：（已生成并发送到您的邮箱 lijingli@xxdc.com）\n\n需要我调整报告格式或补充内容吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
        ]

    # 参建管理 - 专业负责人
    elif pl == 3:
        # 区分设计师、总包、监理
        if "design" in user["username"] or "赵" in name:
            return [
                {
                    "sender_im": im_id,
                    "sender_name": name,
                    "sender_user_id": user["id"],
                    "content": "Emy，帮我查看建筑设计相关的任务。",
                    "direction": "user_to_agent",
                    "is_at_bot": True,
                    "takeover": True,
                },
                {
                    "sender_im": "Emy",
                    "sender_name": "Emy",
                    "sender_user_id": None,
                    "content": f"你好赵工！你的设计任务清单：\n\n📋 待处理：2 个\n  1. 施工图第四版优化（待办）\n     - 截止：2024-07-10\n     - 重点：地下室排水设计优化\n  2. 设计交底材料准备（待办）\n     - 截止：2024-07-05\n\n✅ 本周已完成：\n  - 建筑施工图第三版优化（已完成）\n  - 节能专篇评审（已完成）\n\n📅 即将到来：\n  - 下周一设计评审会（下午2点）\n\n需要我帮你创建新的任务吗？",
                    "direction": "agent_to_user",
                    "is_at_bot": False,
                    "takeover": True,
                },
                {
                    "sender_im": im_id,
                    "sender_name": name,
                    "sender_user_id": user["id"],
                    "content": "任务状态更新。",
                    "direction": "user_to_agent",
                    "is_at_bot": True,
                    "takeover": True,
                },
                {
                    "sender_im": "Emy",
                    "sender_name": "Emy",
                    "sender_user_id": None,
                    "content": "好的赵工！请告诉我：\n\n1️⃣ 哪个任务完成了？\n   - 施工图优化\n   - 设计交底准备\n   - 其他...\n\n2️⃣ 或者需要更新进度百分比？\n\n3️⃣ 需要我帮你标记遇到的问题吗？\n\n请描述具体的更新内容～",
                    "direction": "agent_to_user",
                    "is_at_bot": False,
                    "takeover": True,
                },
            ]
        else:  # 总包项目经理 / 监理
            return [
                {
                    "sender_im": im_id,
                    "sender_name": name,
                    "sender_user_id": user["id"],
                    "content": "Emy，查看我的待办任务。",
                    "direction": "user_to_agent",
                    "is_at_bot": True,
                    "takeover": True,
                },
                {
                    "sender_im": "Emy",
                    "sender_name": "Emy",
                    "sender_user_id": None,
                    "content": f"你好{name}！你的待办任务：\n\n📋 待处理任务：3 个\n━━━━━━━━━━━━━━━━━━\n1. 3号楼钢筋间距问题整改 ⚠️\n   - 优先级：高\n   - 截止：2024-07-03\n   - 剩余：3 天\n   - 来源：陈监理 质量检查\n\n2. 桩基工程验收资料整理 📄\n   - 优先级：中\n   - 截止：2024-07-05\n   - 剩余：5 天\n\n3. 下周施工计划编排 📅\n   - 优先级：中\n   - 截止：2024-07-01\n   - 剩余：1 天\n\n✅ 本周已完成：2 个任务\n\n需要我帮你处理哪个任务？",
                    "direction": "agent_to_user",
                    "is_at_bot": False,
                    "takeover": True,
                },
            ]

    # 参建执行 - 施工员
    elif pl == 2:
        return [
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "Emy，今天的施工任务有哪些？",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": f"孙师傅你好！今日施工任务安排：\n\n📅 2024年6月27日 星期四\n\n📍 今日任务：\n  1. 2号楼地下室防水施工 🔄\n     - 班组：防水一组\n     - 预计完成：今日下班前\n     - 验收人：陈监理\n\n  2. 3号楼桩基施工（第12-15根） 🔄\n     - 班组：桩基二组\n     - 目标：完成4根\n\n  3. 1号楼钢筋绑扎继续 🔄\n     - 班组：钢筋一组\n\n⚠️ 注意事项：\n  - 今日有小雨，注意防滑\n  - 地下室注意通风\n\n需要我帮你上传施工照片到任务吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "上传照片，2号楼防水施工完成了。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": "📸 照片已接收并上传！\n\n✅ 2号楼地下室防水施工\n   - 照片数量：3 张\n   - 拍摄时间：2024-06-27 15:30\n   - 已关联任务：2号楼防水施工\n   - 状态：已自动更新为「待验收」\n\n🔔 已通知：陈监理（验收人）\n\n验收通过后会自动通知你！需要我帮你更新其他任务吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
        ]

    # 访客 - 供应商/外部人员
    else:  # pl == 1
        return [
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "Emy你好，我想咨询一下项目招标信息。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": f"您好周业务员！欢迎咨询～\n\n🔍 当前可公开的招标信息：\n\n📋 锦绣花园项目\n  - 电梯采购：公示中（截止7月10日）\n  - 外墙材料：待发布\n  - 智能化系统：筹备中\n\n📋 滨江商务区项目\n  - 幕墙采购：意向登记中\n\n📄 获取详细招标文件：\n  1. 登录采购平台 www.xxdc-buy.com\n  2. 注册供应商账号\n  3. 搜索项目编号下载文件\n\n📞 采购联系人：李经理\n   - 电话：021-8888xxxx\n   - 邮箱：procurement@xxdc.com\n\n需要我帮你查询某个具体标段的情况吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
            {
                "sender_im": im_id,
                "sender_name": name,
                "sender_user_id": user["id"],
                "content": "帮我看看我的报价审核状态。",
                "direction": "user_to_agent",
                "is_at_bot": True,
                "takeover": True,
            },
            {
                "sender_im": "Emy",
                "sender_name": "Emy",
                "sender_user_id": None,
                "content": "🔒 权限提示：\n\n抱歉，报价审核状态属于项目内部信息。\n\n您当前是访客权限，可以查询：\n  ✅ 公开招标信息\n  ✅ 采购联系方式\n  ✅ 供应商注册指南\n\n无法查询：\n  ❌ 其他供应商报价信息\n  ❌ 内部审核状态\n  ❌ 中标结果（待公示期）\n\n📞 建议：直接联系采购部李经理咨询贵公司的报价状态\n\n需要我帮你联系预约吗？",
                "direction": "agent_to_user",
                "is_at_bot": False,
                "takeover": True,
            },
        ]


def main():
    print("="*60)
    print("💬 添加私聊对话测试数据")
    print("="*60)

    session = get_db_session()

    # 获取用户
    users = get_users(session)
    print(f"\n📊 找到 {len(users)} 个测试用户")

    total_msgs = 0
    for i, user in enumerate(users):
        msg_count = create_private_conversation(session, user, i)
        total_msgs += msg_count

    print("\n" + "="*60)
    print(f"✅ 私聊对话添加完成！")
    print("="*60)
    print(f"\n📊 新增数据统计：")
    print(f"  💬 私聊会话：{len(users)} 个")
    print(f"  📨 私聊消息：{total_msgs} 条")

    # 验证
    print("\n" + "="*60)
    print("📋 消息汇总（群聊 + 私聊）")
    print("="*60)

    result = session.execute(text(
        "SELECT conversation_type, COUNT(*) FROM messages m "
        "JOIN conversations c ON m.conversation_id = c.id "
        "GROUP BY c.conversation_type"
    ))
    for row in result:
        label = "👥 群聊消息" if row[0] == "group" else "👤 私聊消息"
        print(f"  {label}：{row[1]} 条")

    session.close()


if __name__ == "__main__":
    main()
