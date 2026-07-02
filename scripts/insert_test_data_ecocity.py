"""
生态城26#地项目测试数据插入脚本

执行方式：
cd d:\app\Emily\emily-core
python ..\scripts\insert_test_data_ecocity.py
"""

import sys
import os
from datetime import datetime, timezone

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'emily-core'))

from emily_core.infrastructure.database import get_session
from emily_core.infrastructure.database.models import User, Project, CompanyInfo


def insert_ecocity_test_data():
    """插入生态城26#地项目测试数据"""

    with get_session() as session:
        try:
            # ==========================================
            # 1. 创建公司信息（城投置业）
            # ==========================================
            print("【1/3】创建公司信息...")
            company = session.query(CompanyInfo).filter(
                CompanyInfo.company_name == "天津生态城投资开发有限公司"
            ).first()

            if not company:
                company = CompanyInfo(
                    company_name="天津生态城投资开发有限公司",
                    unified_code="91120116668837667E",
                    business_desc="天津生态城城市开发建设运营",
                    project_leader_id="",  # 后面更新
                    creator_id="system",
                    type="建设单位",
                    status="active",
                    scope='["土地开发", "基础设施建设", "配套商业"]',
                    partners='[]',
                )
                session.add(company)
                session.flush()
                print(f"  ✓ 公司创建成功，ID: {company.id}")
            else:
                print(f"  ✓ 公司已存在，ID: {company.id}")

            # ==========================================
            # 2. 创建项目总用户 - 王建国
            # ==========================================
            print("\n【2/3】创建项目总用户...")
            project_manager = session.query(User).filter(
                User.username == "wangjianguo"
            ).first()

            if not project_manager:
                project_manager = User(
                    username="wangjianguo",
                    real_name="王建国",
                    phone="13802168899",
                    email="wangjianguo@tjedc.com",
                    status="active",
                    is_admin=False,
                    gender=1,  # 1=男
                    id_card="120104198006156677",
                    qq="88552211",
                    wechat="wangjg_ecocity",
                    remark="天津生态城26#地项目总，高级工程师，15年工程管理经验",
                    creator_id="system",
                    is_deleted=False,
                    perm_list='["project:read", "project:write", "event:create", "task:assign"]',
                    org_category=4,  # 4=管理组
                    permission_level=4,  # 4=建设主管
                    supervisor_id=None,  # 项目总，无直接上级
                    company=company.id,
                    position='["项目总经理", "工程总指挥"]',
                )
                session.add(project_manager)
                session.flush()
                print(f"  ✓ 用户创建成功，ID: {project_manager.id}")
                print(f"    姓名: {project_manager.real_name}")
                print(f"    职位: 项目总经理")
                print(f"    权限层级: 4级（建设主管）")
                print(f"    电话: {project_manager.phone}")
            else:
                print(f"  ✓ 用户已存在，ID: {project_manager.id}")

            # 更新公司的项目负责人ID
            if not company.project_leader_id:
                company.project_leader_id = project_manager.id
                print(f"  ✓ 更新公司项目负责人ID")

            # ==========================================
            # 3. 创建生态城26#地项目
            # ==========================================
            print("\n【3/3】创建生态城26#地项目...")
            project = session.query(Project).filter(
                Project.code == "ECO-CITY-26-2024"
            ).first()

            if not project:
                project = Project(
                    code="ECO-CITY-26-2024",
                    name="天津生态城26#地块开发项目",
                    description="""
天津生态城26#地块项目位于中新天津生态城核心区域，占地面积约8.6万平方米，
总建筑面积约25万平方米，包括住宅、商业配套、社区服务中心等。

当前处于景观施工阶段，主要工作内容：
1. 小区园林景观施工（绿化、水景、铺装）
2. 主入口广场及配套设施建设
3. 社区活动中心周边景观
4. 儿童游乐区、健身区设施安装
5. 园区照明及智慧安防系统
                    """.strip(),
                    status="active",
                    address="天津市滨海新区中新天津生态城26#地块",
                    city="天津",
                    lifecycle_stage=2,  # 2=工程施工阶段
                    creator_id=project_manager.id,
                    is_deleted=False,
                )
                session.add(project)
                session.flush()
                print(f"  ✓ 项目创建成功，ID: {project.id}")
                print(f"    项目名称: {project.name}")
                print(f"    项目编号: {project.code}")
                print(f"    项目地点: {project.address}")
                print(f"    当前阶段: 景观施工阶段 (lifecycle_stage=2)")
                print(f"    创建人: {project_manager.real_name}")
            else:
                print(f"  ✓ 项目已存在，ID: {project.id}")

            # 关联用户到项目
            if not project_manager.project_id:
                project_manager.project_id = project.id
                print(f"  ✓ 关联项目总到项目 {project.code}")

            session.commit()
            print("\n" + "=" * 60)
            print("✅ 测试数据插入成功！")
            print("=" * 60)

            # 显示汇总信息
            print("\n📊 数据汇总：")
            print("  公司信息：")
            print(f"    名称：{company.company_name}")
            print(f"    类型：{company.type}")
            print(f"    统一信用代码：{company.unified_code}")
            print()
            print("  用户信息（项目总）：")
            print(f"    用户名：{project_manager.username}")
            print(f"    姓名：{project_manager.real_name}")
            print(f"    职位：项目总经理")
            print(f"    权限层级：{project_manager.permission_level}级（建设主管）")
            print(f"    所属公司：{company.company_name}")
            print()
            print("  项目信息：")
            print(f"    项目名称：{project.name}")
            print(f"    项目编号：{project.code}")
            print(f"    所在城市：{project.city}")
            print(f"    项目地址：{project.address}")
            print(f"    当前阶段：景观施工阶段")
            print(f"    项目状态：{project.status}")
            print(f"    创建人：{project_manager.real_name}")

            return True

        except Exception as e:
            session.rollback()
            print(f"\n❌ 插入失败：{str(e)}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == "__main__":
    print("=" * 60)
    print("天津生态城26#地项目 - 测试数据插入脚本")
    print("=" * 60)
    print()

    success = insert_ecocity_test_data()

    if success:
        print("\n🎉 脚本执行完成！")
    else:
        print("\n⚠️ 脚本执行失败，请检查错误信息。")
        sys.exit(1)
