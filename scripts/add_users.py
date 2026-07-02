"""
插入4个用户：
- 意景园林景观设计公司：1个主管 + 1个参建
- 鸿丰景观工程有限公司：1个主管 + 1个参建
"""

import sys
import os

# 添加 emily-core 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import CompanyInfo, User


def get_company_id(company_name):
    """获取公司ID"""
    with get_session() as session:
        company = session.query(CompanyInfo).filter(
            CompanyInfo.company_name == company_name,
            CompanyInfo.is_deleted == False
        ).first()
        return company.id if company else None


def user_exists(username):
    """检查用户是否已存在"""
    with get_session() as session:
        exists = session.query(User).filter(
            User.username == username,
            User.is_deleted == False
        ).first()
        return exists is not None


def insert_users():
    """插入用户数据"""
    
    # 获取两家公司的ID
    company1_id = get_company_id("意景园林景观设计公司")
    company2_id = get_company_id("鸿丰景观工程有限公司")
    
    print(f"意景园林景观设计公司 ID: {company1_id}")
    print(f"鸿丰景观工程有限公司 ID: {company2_id}")
    
    if not company1_id or not company2_id:
        print("❌ 找不到公司，请先创建公司")
        return
    
    users = [
        # 意景园林 - 主管（permission_level = 3）
        {
            "username": "design_liu",
            "real_name": "刘设计",
            "phone": "13900001001",
            "email": "liusj@yj-design.com",
            "qq": "10001001",
            "wechat": "wx_design_liu",
            "remark": "意景园林景观设计公司设计主管",
            "permission_level": 3,  # 参建管理
            "org_category": 4,  # 管理组
            "company_id": company1_id,
            "position": '["设计主管","景观设计师"]',
        },
        # 意景园林 - 参建（permission_level = 2）
        {
            "username": "designer_chen",
            "real_name": "陈设计师",
            "phone": "13900001002",
            "email": "chensj@yj-design.com",
            "qq": "10001002",
            "wechat": "wx_designer_chen",
            "remark": "意景园林景观设计公司设计师",
            "permission_level": 2,  # 参建执行
            "org_category": 2,  # 工程组
            "company_id": company1_id,
            "position": '["景观设计师","方案设计师"]',
        },
        # 鸿丰景观 - 主管（permission_level = 3）
        {
            "username": "project_wang",
            "real_name": "王工头",
            "phone": "13900002001",
            "email": "wanggt@hongfeng.com",
            "qq": "10002001",
            "wechat": "wx_project_wang",
            "remark": "鸿丰景观工程有限公司项目经理",
            "permission_level": 3,  # 参建管理
            "org_category": 4,  # 管理组
            "company_id": company2_id,
            "position": '["项目经理","施工主管"]',
        },
        # 鸿丰景观 - 参建（permission_level = 2）
        {
            "username": "worker_li",
            "real_name": "李施工",
            "phone": "13900002002",
            "email": "lisg@hongfeng.com",
            "qq": "10002002",
            "wechat": "wx_worker_li",
            "remark": "鸿丰景观工程有限公司施工员",
            "permission_level": 2,  # 参建执行
            "org_category": 2,  # 工程组
            "company_id": company2_id,
            "position": '["施工员","绿化技术员"]',
        },
    ]
    
    inserted_count = 0
    for user_data in users:
        username = user_data["username"]
        
        if user_exists(username):
            print(f"⚠️  用户已存在，跳过: {user_data['real_name']} ({username})")
            continue
        
        with get_session() as session:
            user = User(
                username=user_data["username"],
                real_name=user_data["real_name"],
                phone=user_data["phone"],
                email=user_data["email"],
                qq=user_data["qq"],
                wechat=user_data["wechat"],
                remark=user_data["remark"],
                status="active",
                is_admin=False,
                gender=1,
                creator_id="system",
                is_deleted=False,
                perm_list='["task.read","task.write"]',
                org_category=user_data["org_category"],
                permission_level=user_data["permission_level"],
                company=user_data["company_id"],
                position=user_data["position"],
            )
            session.add(user)
            print(f"✅ 成功插入用户: {user_data['real_name']} (QQ: {user_data['qq']})")
            inserted_count += 1
    
    print(f"\n总计: 插入 {inserted_count} 个用户")
    
    # 显示所有用户
    with get_session() as session:
        all_users = session.query(User).filter(
            User.is_deleted == False
        ).join(CompanyInfo, User.company == CompanyInfo.id, isouter=True).all()
        
        print(f"\n当前数据库中的用户列表 ({len(all_users)} 人):")
        for u in all_users:
            company_name = "未分配"
            if u.company:
                company = session.query(CompanyInfo).filter(CompanyInfo.id == u.company).first()
                if company:
                    company_name = company.company_name
            
            level_name = {
                1: "访客",
                2: "参建执行",
                3: "参建管理",
                4: "建设主管",
                5: "管理员",
                6: "系统管理员"
            }.get(u.permission_level, f"Level {u.permission_level}")
            
            print(f"  - {u.real_name} | {level_name} | {company_name} | QQ: {u.qq}")


if __name__ == "__main__":
    print("=" * 70)
    print("插入用户数据")
    print("=" * 70)
    insert_users()
    print("=" * 70)
