#!/usr/bin/env python
"""
生成测试数据脚本
==================
创建测试用的：
  1. 公司信息（建设单位、设计单位、总包、监理、供应商）
  2. 用户信息（系统管理员、建设主管、参建管理、参建执行、访客）
  3. 项目信息
  4. 模拟通讯消息（入站 + 出站）

执行方法：
  cd /d/app/Emily
  python scripts/generate_test_data.py
"""

import sys
import os
import json
import uuid
from datetime import datetime, timezone, timedelta

# 确保能导入 emily-core
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def get_db_connection():
    """获取数据库连接"""
    # 尝试多种连接方式
    urls_to_try = [
        "postgresql://emily:emily_secret_2026@localhost:25432/emily",
        "postgresql://emily:emily_secret_2026@127.0.0.1:25432/emily",
        "postgresql://emily@localhost:25432/emily",
    ]

    for url in urls_to_try:
        try:
            engine = create_engine(url, echo=False)
            # 测试连接
            with engine.connect():
                print(f"✅ 数据库连接成功: {url}")
                return engine
        except Exception as e:
            print(f"⚠️  连接失败: {url} - {e}")

    raise Exception("所有数据库连接尝试失败，请确认容器运行和端口映射")


def generate_id():
    """生成短 UUID"""
    return str(uuid.uuid4())


def utc_now_iso():
    """当前 UTC 时间 ISO 格式"""
    return datetime.now(timezone.utc).isoformat()


def generate_companies(session):
    """生成测试公司数据"""
    print("\n" + "="*60)
    print("📋 生成公司信息")
    print("="*60)

    companies = [
        {
            "name": "XX地产集团有限公司",
            "type": "建设单位",
            "code": "XXDC-001",
            "desc": "房地产开发与经营，项目投资管理",
            "scope": json.dumps(["立项审批", "投资控制", "工程管理"]),
            "dept": json.dumps(["总裁办", "工程部", "成本部", "设计管理部", "采购部"])
        },
        {
            "name": "上海建筑设计研究院有限公司",
            "type": "设计单位",
            "code": "SJY-001",
            "desc": "建筑工程设计、规划设计、景观设计",
            "scope": json.dumps(["方案设计", "施工图设计", "现场配合"]),
            "dept": json.dumps(["建筑所", "结构所", "设备所", "景观所"])
        },
        {
            "name": "中天建设集团有限公司",
            "type": "总包",
            "code": "ZTJS-001",
            "desc": "房屋建筑工程总承包、市政工程",
            "scope": json.dumps(["主体施工", "机电安装", "装饰装修"]),
            "dept": json.dumps(["项目部", "技术部", "安全部", "质量部"])
        },
        {
            "name": "恒大工程监理有限公司",
            "type": "监理",
            "code": "HDJL-001",
            "desc": "工程监理、项目管理、技术咨询",
            "scope": json.dumps(["质量监理", "进度监理", "安全监理"]),
            "dept": json.dumps(["监理一部", "监理二部"])
        },
        {
            "name": "鑫达建材供应有限公司",
            "type": "供应商",
            "code": "XDJC-001",
            "desc": "建筑材料供应、设备租赁",
            "scope": json.dumps(["钢材供应", "混凝土供应", "五金建材"]),
            "dept": json.dumps(["销售部", "物流部", "仓储部"])
        },
    ]

    company_ids = {}

    for comp in companies:
        company_id = generate_id()
        company_ids[comp["type"]] = {
            "id": company_id,
            "name": comp["name"],
            "type": comp["type"],
        }

        sql = text("""
            INSERT INTO company_info (
                id, company_name, unified_code, business_desc,
                type, status, scope, department,
                project_leader_id, creator_id, created_at, updated_at, is_deleted
            ) VALUES (
                :id, :name, :code, :desc,
                :type, 'active', :scope, :dept,
                'system', 'system', :now, :now, false
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        result = session.execute(sql, {
            "id": company_id,
            "name": comp["name"],
            "code": comp["code"],
            "desc": comp["desc"],
            "type": comp["type"],
            "scope": comp["scope"],
            "dept": comp["dept"],
            "now": utc_now_iso(),
        })

        inserted = result.fetchone()
        if inserted:
            print(f"  ✅ {comp['type']}: {comp['name']}")
        else:
            print(f"  ⏭️  {comp['type']}: {comp['name']} (已存在)")

    session.commit()
    return company_ids


def generate_users(session, company_ids):
    """生成测试用户数据"""
    print("\n" + "="*60)
    print("👥 生成用户信息")
    print("="*60)

    build_co = company_ids["建设单位"]["id"]
    design_co = company_ids["设计单位"]["id"]
    zongbao_co = company_ids["总包"]["id"]
    jianli_co = company_ids["监理"]["id"]
    gongying_co = company_ids["供应商"]["id"]

    users = [
        # 系统管理员 (权限级别 6)
        {
            "username": "admin_wang",
            "real_name": "王总",
            "phone": "13800000001",
            "email": "wangzong@xxdc.com",
            "company": build_co,
            "perm_list": json.dumps(["*"]),
            "org_category": 4,  # 管理组
            "permission_level": 6,
            "position": json.dumps(["系统管理员", "项目总监"]),
            "desc": "系统最高权限管理员",
        },
        # 建设主管 (权限级别 4)
        {
            "username": "pm_li",
            "real_name": "李经理",
            "phone": "13800000002",
            "email": "lijingli@xxdc.com",
            "company": build_co,
            "perm_list": json.dumps(["project.read", "project.write", "task.assign", "review.approve"]),
            "org_category": 4,  # 管理组
            "permission_level": 4,
            "position": json.dumps(["工程部经理", "甲方代表"]),
            "desc": "甲方工程部负责人",
        },
        # 参建管理 - 设计师 (权限级别 3)
        {
            "username": "designer_zhao",
            "real_name": "赵工",
            "phone": "13800000003",
            "email": "zhaogong@shanghai-design.com",
            "company": design_co,
            "perm_list": json.dumps(["design.read", "design.write", "file.upload"]),
            "org_category": 2,  # 工程组
            "permission_level": 3,
            "position": json.dumps(["建筑设计师", "设计负责人"]),
            "desc": "建筑设计专业负责人",
        },
        # 参建管理 - 总包项目经理 (权限级别 3)
        {
            "username": "engineer_zhang",
            "real_name": "张工",
            "phone": "13800000004",
            "email": "zhanggong@ztjs.com",
            "company": zongbao_co,
            "perm_list": json.dumps(["task.read", "task.write", "progress.update"]),
            "org_category": 2,  # 工程组
            "permission_level": 3,
            "position": json.dumps(["项目经理", "土建工程师"]),
            "desc": "总包现场项目经理",
        },
        # 参建管理 - 监理工程师 (权限级别 3)
        {
            "username": "supervisor_chen",
            "real_name": "陈监理",
            "phone": "13800000005",
            "email": "chenjianli@hdjl.com",
            "company": jianli_co,
            "perm_list": json.dumps(["quality.check", "progress.review", "issue.report"]),
            "org_category": 2,  # 工程组
            "permission_level": 3,
            "position": json.dumps(["监理工程师", "质量监督员"]),
            "desc": "现场监理工程师",
        },
        # 参建执行 - 施工员 (权限级别 2)
        {
            "username": "worker_sun",
            "real_name": "孙师傅",
            "phone": "13800000006",
            "email": "sunshifu@ztjs.com",
            "company": zongbao_co,
            "perm_list": json.dumps(["progress.report", "photo.upload"]),
            "org_category": 2,  # 工程组
            "permission_level": 2,
            "position": json.dumps(["施工员", "班组长"]),
            "desc": "土建施工班组长",
        },
        # 访客 - 供应商 (权限级别 1)
        {
            "username": "guest_zhou",
            "real_name": "周业务员",
            "phone": "13800000007",
            "email": "zhouwuye@xdjc.com",
            "company": gongying_co,
            "perm_list": json.dumps(["public.read"]),
            "org_category": 1,  # 访客组
            "permission_level": 1,
            "position": json.dumps(["业务员", "供应商联系人"]),
            "desc": "建材供应商联系人",
        },
    ]

    user_ids = {}

    for user in users:
        user_id = generate_id()
        user_ids[user["username"]] = {
            "id": user_id,
            "real_name": user["real_name"],
            "permission_level": user["permission_level"],
        }

        sql = text("""
            INSERT INTO users (
                id, username, real_name, phone, email,
                company, perm_list, org_category, permission_level,
                position, remark, creator_id, status, is_admin, is_deleted,
                created_at, updated_at
            ) VALUES (
                :id, :username, :real_name, :phone, :email,
                :company, :perm_list, :org_category, :permission_level,
                :position, :desc, 'system', 'active', :is_admin, false,
                :now, :now
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        result = session.execute(sql, {
            "id": user_id,
            "username": user["username"],
            "real_name": user["real_name"],
            "phone": user["phone"],
            "email": user["email"],
            "company": user["company"],
            "perm_list": user["perm_list"],
            "org_category": user["org_category"],
            "permission_level": user["permission_level"],
            "position": user["position"],
            "desc": user["desc"],
            "is_admin": user["permission_level"] >= 5,
            "now": utc_now_iso(),
        })

        inserted = result.fetchone()
        perm_label = {
            6: "系统管理员",
            4: "建设主管",
            3: "参建管理",
            2: "参建执行",
            1: "访客",
        }.get(user["permission_level"], "未知")

        if inserted:
            print(f"  ✅ {user['real_name']} ({perm_label})")
        else:
            print(f"  ⏭️  {user['real_name']} ({perm_label}) (已存在)")

    session.commit()
    return user_ids


def generate_projects(session, user_ids):
    """生成测试项目数据"""
    print("\n" + "="*60)
    print("🏗️  生成项目信息")
    print("="*60)

    projects = [
        {
            "code": "PRJ-2024-001",
            "name": "锦绣花园住宅小区项目",
            "desc": "大型住宅开发项目，总建筑面积 30 万平方米",
            "city": "上海市",
            "address": "浦东新区张江高科技园区",
            "stage": 2,  # 设计阶段
        },
        {
            "code": "PRJ-2024-002",
            "name": "滨江商务区写字楼项目",
            "desc": "甲级商务办公楼，总建筑面积 15 万平方米",
            "city": "上海市",
            "address": "黄浦区滨江大道 888 号",
            "stage": 1,  # 立项阶段
        },
        {
            "code": "PRJ-2023-003",
            "name": "城市综合体项目一期",
            "desc": "商业 + 办公 + 酒店综合体",
            "city": "杭州市",
            "address": "杭州市西湖区",
            "stage": 3,  # 施工阶段
        },
    ]

    project_ids = {}
    admin_id = user_ids["admin_wang"]["id"]

    for proj in projects:
        project_id = generate_id()
        project_ids[proj["code"]] = {
            "id": project_id,
            "name": proj["name"],
        }

        sql = text("""
            INSERT INTO projects (
                id, code, name, description, status,
                city, address, lifecycle_stage, creator_id, is_deleted,
                created_at, updated_at
            ) VALUES (
                :id, :code, :name, :desc, 'active',
                :city, :address, :stage, :creator_id, false,
                :now, :now
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        result = session.execute(sql, {
            "id": project_id,
            "code": proj["code"],
            "name": proj["name"],
            "desc": proj["desc"],
            "city": proj["city"],
            "address": proj["address"],
            "stage": proj["stage"],
            "creator_id": admin_id,
            "now": utc_now_iso(),
        })

        inserted = result.fetchone()
        if inserted:
            print(f"  ✅ {proj['name']}")
        else:
            print(f"  ⏭️  {proj['name']} (已存在)")

    session.commit()
    return project_ids


def generate_user_im_bindings(session, user_ids):
    """生成用户 IM 绑定记录"""
    print("\n" + "="*60)
    print("🔗 生成用户 IM 绑定")
    print("="*60)

    for username, user_info in user_ids.items():
        sql = text("""
            INSERT INTO user_im_bindings (
                id, user_id, im_platform, im_user_id,
                im_display_name, status, created_at, updated_at
            ) VALUES (
                :id, :user_id, 'simulator', :im_user_id,
                :display_name, 'active', :now, :now
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        result = session.execute(sql, {
            "id": generate_id(),
            "user_id": user_info["id"],
            "im_user_id": f"sim_{username}",
            "display_name": user_info["real_name"],
            "now": utc_now_iso(),
        })

        inserted = result.fetchone()
        if inserted:
            print(f"  ✅ {user_info['real_name']} -> sim_{username}")
        else:
            print(f"  ⏭️  {user_info['real_name']} (已绑定)")

    session.commit()


def generate_simulated_conversations(session, user_ids, project_ids):
    """生成模拟对话消息"""
    print("\n" + "="*60)
    print("💬 生成模拟通讯消息")
    print("="*60)

    # 先创建会话
    conv_id = generate_id()
    conv_sql = text("""
        INSERT INTO conversations (
            id, im_platform, conversation_type, conversation_id,
            title, takeover_mode, created_at, updated_at
        ) VALUES (
            :id, 'simulator', 'group', :conv_biz_id,
            '项目管理工作群', 'collaborate', :now, :now
        )
        ON CONFLICT DO NOTHING
        RETURNING id
    """)

    result = session.execute(conv_sql, {
        "id": conv_id,
        "conv_biz_id": "project_group_001",
        "now": utc_now_iso(),
    })
    session.commit()

    # 获取实际插入的 ID
    conv_result = session.execute(text(
        "SELECT id FROM conversations WHERE conversation_id = 'project_group_001'"
    )).fetchone()
    actual_conv_id = conv_result[0] if conv_result else conv_id

    # 模拟对话消息
    conversations = [
        # 王总 (管理员)
        {
            "sender": "admin_wang",
            "content": "大家好，锦绣花园项目今天正式启动，请各位负责人跟进各自的任务。",
            "is_at_bot": False,
            "takeover": False,
        },
        # 李经理 (建设主管)
        {
            "sender": "pm_li",
            "content": "收到王总，设计图纸评审安排在下周一下午 2 点，请设计院做好准备。",
            "is_at_bot": False,
            "takeover": False,
        },
        # 赵工 (设计师)
        {
            "sender": "designer_zhao",
            "content": "好的李经理，我们会准备好方案。Emy，帮我记录一下下周一的设计评审会。",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复
        {
            "sender": "Emy",
            "content": "好的赵工，已为你创建会议提醒：下周一（6月29日）下午2点的设计评审会。需要我帮你做其他安排吗？",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
        # 张工 (总包项目经理)
        {
            "sender": "engineer_zhang",
            "content": "Emy，帮我查询一下当前桩基工程的进度情况。",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复
        {
            "sender": "Emy",
            "content": "根据状态机数据，桩基工程当前进度如下：\n\n📊 整体进度：35%\n✅ 已完成节点：\n  - 桩基试桩（100%）\n  - 桩基施工方案评审（100%）\n🚧 进行中：\n  - 工程桩施工（进行中，35%）\n⏱️ 预计完成时间：7月15日",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
        # 陈监理
        {
            "sender": "supervisor_chen",
            "content": "Emy，我要记录一个质量问题：3号楼钢筋间距不符合规范要求，请安排整改。",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复
        {
            "sender": "Emy",
            "content": "已为你记录质量问题：3号楼钢筋间距不符合规范。\n\n📋 已创建质量整改任务：\n  - 负责人：张工（总包）\n  - 要求完成时间：3个工作日内\n  - 抄送：李经理（甲方）、赵工（设计院）\n\n需要我帮你做其他安排吗？",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
        # 孙师傅 (施工员)
        {
            "sender": "worker_sun",
            "content": "Emy，2号楼地下室底板混凝土浇筑已完成，请帮我更新一下进度。",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复
        {
            "sender": "Emy",
            "content": "好的孙师傅，已更新2号楼地下室底板混凝土浇筑状态为 ✅ 完成。\n\n📊 节点状态已同步到全局状态机，下游节点前置条件已满足：\n  - 地下室防水施工（可启动）\n  - 基础验收（待组织）",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
        # 周业务员 (供应商)
        {
            "sender": "guest_zhou",
            "content": "Emy您好，我是鑫达建材的小周，请问锦绣花园项目下周的钢材需求计划出来了吗？",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复 (访客权限，只返回公开信息)
        {
            "sender": "Emy",
            "content": "您好周业务员！关于锦绣花园项目的钢材需求计划：\n\n📋 公开信息：\n  - 本月钢材采购计划正在审批中\n  - 预计需求总量约 500 吨\n  - 具体招标信息将在采购平台公开\n\n🔒 详细的供应商入围名单等内部信息需联系甲方工程部。",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
        # 王总 总结
        {
            "sender": "admin_wang",
            "content": "Emy，生成一下本周项目进度周报。",
            "is_at_bot": True,
            "takeover": True,
        },
        # Emy 回复 (管理员权限，完整信息)
        {
            "sender": "Emy",
            "content": "📊 锦绣花园项目本周进度周报（第25周）\n\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n【整体进度】42% ↑ 3%\n\n【各专业进度】\n  🏗️ 土建工程：45% (桩基35%→42%)\n  🎨 设计工作：80% (施工图优化中)\n  📋 报批报建：60% (规划许可审批中)\n\n【本周完成】\n  ✅ 2号楼地下室底板浇筑\n  ✅ 桩基工程验收通过\n  ✅ 施工图第三版优化完成\n\n【下周计划】\n  📌 3号楼桩基开始施工\n  📌 规划许可证预计6月30日取得\n  📌 总包单位进场准备\n\n【风险预警】\n  ⚠️ 钢筋供货周期可能延迟（供应商反馈）\n  ⚠️ 近期雨水天气可能影响室外作业\n\n【人员动态】\n  👤 本周活跃人员：8人\n  💬 消息总数：127条\n  📎 文件上传：15个\n\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n需要我生成详细的 PDF 报告吗？",
            "is_at_bot": False,
            "takeover": True,
            "direction": "agent_to_user",
        },
    ]

    msg_count = 0
    for i, msg in enumerate(conversations):
        sender = msg["sender"]
        sender_id = user_ids.get(sender, {}).get("id") or "system"
        sender_name = user_ids.get(sender, {}).get("real_name") or sender
        direction = msg.get("direction", "user_to_agent")

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
        created_at = (datetime.now(timezone.utc) - timedelta(hours=len(conversations)-i)).isoformat()

        result = session.execute(sql, {
            "id": msg_id,
            "event_id": f"sim_event_{i}_{msg_id[:8]}",
            "conv_id": actual_conv_id,
            "sender_im_id": f"sim_{sender}",
            "sender_name": sender_name,
            "sender_user_id": sender_id if sender != "Emy" else None,
            "content": msg["content"],
            "direction": direction,
            "is_at_bot": msg["is_at_bot"],
            "takeover": msg["takeover"],
            "created_at": created_at,
        })

        if result.fetchone():
            msg_count += 1
            print(f"  ✅ {sender_name}: {msg['content'][:30]}...")

    session.commit()
    print(f"\n  📊 共生成 {msg_count} 条模拟消息")


def generate_events_and_tasks(session, user_ids, project_ids):
    """生成测试事件和任务"""
    print("\n" + "="*60)
    print("📝 生成测试事件和任务")
    print("="*60)

    prj1_id = list(project_ids.values())[0]["id"]
    creator = user_ids["admin_wang"]["id"]

    # 生成事件
    events = [
        {
            "type": "progress",
            "title": "桩基工程进度更新",
            "desc": "桩基施工完成 35%，预计7月15日全部完成",
            "creator": creator,
        },
        {
            "type": "quality",
            "title": "3号楼钢筋质量问题",
            "desc": "3号楼底板钢筋间距不符合规范要求，已下达整改通知",
            "creator": user_ids["supervisor_chen"]["id"],
        },
        {
            "type": "meeting",
            "title": "每周工程例会",
            "desc": "时间：周一上午9点，地点：项目部会议室",
            "creator": creator,
        },
    ]

    event_count = 0
    for event in events:
        sql = text("""
            INSERT INTO events (
                id, event_no, event_type, title, description,
                project_id, user_id, status, created_at
            ) VALUES (
                :id, :event_no, :event_type, :title, :desc,
                :project_id, :user_id, 'pending', :now
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        event_id = generate_id()
        event_no = f"EVT-{datetime.now().strftime('%Y%m%d')}-{event_count+1:04d}"

        result = session.execute(sql, {
            "id": event_id,
            "event_no": event_no,
            "event_type": event["type"],
            "title": event["title"],
            "desc": event["desc"],
            "project_id": prj1_id,
            "user_id": event["creator"],
            "now": utc_now_iso(),
        })

        if result.fetchone():
            event_count += 1
            print(f"  ✅ 事件: {event['title']}")

    session.commit()

    # 生成任务
    tasks = [
        {
            "title": "3号楼钢筋间距问题整改",
            "desc": "3号楼底板钢筋间距不符合规范要求，需在3个工作日内完成整改并报监理复查",
            "owner": user_ids["engineer_zhang"]["id"],
            "owner_name": "张工",
            "creator": user_ids["supervisor_chen"]["id"],
            "due": "2024-07-03",
        },
        {
            "title": "施工图第四版优化",
            "desc": "根据评审意见优化建筑施工图，重点优化地下室排水设计",
            "owner": user_ids["designer_zhao"]["id"],
            "owner_name": "赵工",
            "creator": user_ids["pm_li"]["id"],
            "due": "2024-07-10",
        },
        {
            "title": "桩基工程验收资料整理",
            "desc": "整理桩基工程验收资料，准备报监理验收",
            "owner": user_ids["engineer_zhang"]["id"],
            "owner_name": "张工",
            "creator": user_ids["pm_li"]["id"],
            "due": "2024-07-05",
        },
    ]

    task_count = 0
    for task in tasks:
        sql = text("""
            INSERT INTO tasks (
                id, task_no, title, description, project_id,
                owner_id, owner_text, created_by, status, due_date, created_at
            ) VALUES (
                :id, :task_no, :title, :desc, :project_id,
                :owner_id, :owner_name, :creator_id, 'todo', :due_date, :now
            )
            ON CONFLICT DO NOTHING
            RETURNING id
        """)

        task_id = generate_id()
        task_no = f"TASK-{datetime.now().strftime('%Y%m%d')}-{task_count+1:04d}"

        result = session.execute(sql, {
            "id": task_id,
            "task_no": task_no,
            "title": task["title"],
            "desc": task["desc"],
            "project_id": prj1_id,
            "owner_id": task["owner"],
            "owner_name": task["owner_name"],
            "creator_id": task["creator"],
            "due_date": task["due"],
            "now": utc_now_iso(),
        })

        if result.fetchone():
            task_count += 1
            print(f"  ✅ 任务: {task['title']} (负责人: {task['owner_name']})")

    session.commit()

    print(f"\n  📊 共生成 {event_count} 个事件，{task_count} 个任务")


def main():
    print("="*60)
    print("🚀 Emily 项目测试数据生成")
    print("="*60)

    try:
        # 连接数据库
        engine = get_db_connection()
        Session = sessionmaker(bind=engine)
        session = Session()

        # 1. 生成公司
        company_ids = generate_companies(session)

        # 2. 生成用户
        user_ids = generate_users(session, company_ids)

        # 3. 生成项目
        project_ids = generate_projects(session, user_ids)

        # 4. 生成用户 IM 绑定
        generate_user_im_bindings(session, user_ids)

        # 5. 生成模拟对话
        generate_simulated_conversations(session, user_ids, project_ids)

        # 6. 生成事件和任务
        generate_events_and_tasks(session, user_ids, project_ids)

        print("\n" + "="*60)
        print("✅ 所有测试数据生成完成！")
        print("="*60)
        print("\n📋 数据汇总：")
        print(f"  🏢 公司：{len(company_ids)} 家")
        print(f"  👥 用户：{len(user_ids)} 人")
        print(f"  🏗️  项目：{len(project_ids)} 个")
        print(f"  💬 对话：1 个群聊")
        print(f"  📝 事件和任务：各 3 个")
        print("\n🎯 建议使用以下用户进行测试：")
        for username, info in user_ids.items():
            perm_label = {
                6: "系统管理员",
                4: "建设主管",
                3: "参建管理",
                2: "参建执行",
                1: "访客",
            }.get(info["permission_level"], "未知")
            print(f"  - {info['real_name']} ({perm_level}): sim_{username}")

        session.close()

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
