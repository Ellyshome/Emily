"""
插入两个公司数据：
1. 意景园林景观设计公司
2. 鸿丰景观工程有限公司
"""

import sys
import os

# 添加 emily-core 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emily-core"))

from emily_core.infrastructure.database.session import get_session
from emily_core.infrastructure.database.models import CompanyInfo, User


def get_admin_user():
    """获取一个管理员用户作为创建者和负责人"""
    with get_session() as session:
        # 优先找系统管理员
        admin = session.query(User).filter(User.is_admin == True).first()
        if admin:
            return admin.id
        # 没有管理员就找任意用户
        user = session.query(User).first()
        if user:
            return user.id
        # 如果没有用户，返回一个默认值（会在SQL中处理）
        return "system_admin"


def company_exists(company_name):
    """检查公司是否已存在"""
    with get_session() as session:
        exists = session.query(CompanyInfo).filter(
            CompanyInfo.company_name == company_name,
            CompanyInfo.is_deleted == False
        ).first()
        return exists is not None


def insert_companies():
    """插入公司数据"""
    
    creator_id = get_admin_user()
    print(f"使用创建者ID: {creator_id}")
    
    companies = [
        {
            "company_name": "意景园林景观设计公司",
            "unified_code": "91310000MA1K3YYY01",
            "business_desc": "园林景观设计、绿化工程设计、环境艺术设计",
            "type": "设计单位",
            "status": "active",
            "scope": '["景观设计","绿化设计","方案设计"]',
            "department": '["设计一部", "设计二部", "方案组"]',
        },
        {
            "company_name": "鸿丰景观工程有限公司",
            "unified_code": "91310000MA1K3YYY02",
            "business_desc": "景观工程施工、绿化工程、园林养护",
            "type": "分包",
            "status": "active",
            "scope": '["景观施工","绿化种植","园林养护"]',
            "department": '["工程部", "养护部", "采购部"]',
        }
    ]
    
    inserted_count = 0
    for company_data in companies:
        name = company_data["company_name"]
        
        if company_exists(name):
            print(f"⚠️  公司已存在，跳过: {name}")
            continue
        
        with get_session() as session:
            company = CompanyInfo(
                company_name=company_data["company_name"],
                unified_code=company_data["unified_code"],
                business_desc=company_data["business_desc"],
                project_leader_id=creator_id,
                creator_id=creator_id,
                type=company_data["type"],
                status=company_data["status"],
                scope=company_data["scope"],
                department=company_data["department"],
                partners="[]",
                function_scope="{}",
            )
            session.add(company)
            print(f"✅ 成功插入公司: {name}")
            inserted_count += 1
    
    print(f"\n总计: 插入 {inserted_count} 家公司")
    
    # 显示所有公司
    with get_session() as session:
        all_companies = session.query(CompanyInfo).filter(
            CompanyInfo.is_deleted == False
        ).all()
        print(f"\n当前数据库中的公司列表 ({len(all_companies)} 家):")
        for c in all_companies:
            print(f"  - {c.company_name} ({c.type})")


if __name__ == "__main__":
    print("=" * 60)
    print("插入公司数据")
    print("=" * 60)
    insert_companies()
    print("=" * 60)
