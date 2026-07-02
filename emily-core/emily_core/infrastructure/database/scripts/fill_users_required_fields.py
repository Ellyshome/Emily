"""
填充 users 表必填字段的 Python 脚本
运行方式：python -m emily_core.infrastructure.database.scripts.fill_users_required_fields
"""

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from emily_core.infrastructure.database.session import get_session, init_db
from emily_core.infrastructure.database.models import User
from sqlalchemy import text, func

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fill_users_fields")


def _utc_now() -> str:
    """生成 UTC 时间字符串（与 models.py 保持一致）"""
    return datetime.now(timezone.utc).isoformat()


def get_or_create_system_user(session) -> str:
    """获取或创建系统用户，作为默认创建者"""
    system_user = session.query(User).filter(User.username == "system").first()
    
    if not system_user:
        logger.info("创建系统用户...")
        now = _utc_now()
        system_user = User(
            username="system",
            phone="13800000000",
            email="system@emily.local",
            status="active",
            is_admin=True,
            gender=1,
            id_card="",
            qq="",
            wechat="",
            remark="系统内置用户，用于数据迁移和自动操作",
            creator_id="",  # 稍后更新
            is_deleted=False,
            perm_list='["*"]',
            org_category=4,  # 管理组
            permission_level=6,  # 系统管理员
            created_at=now,
            updated_at=now,
        )
        session.add(system_user)
        session.flush()
        
        # 更新 creator_id 为自引用
        system_user.creator_id = system_user.id
        session.add(system_user)
        session.flush()
        logger.info(f"系统用户已创建，ID: {system_user.id}")
    else:
        logger.info(f"系统用户已存在，ID: {system_user.id}")
    
    return system_user.id


def fill_created_at(session):
    """填充 created_at 空值"""
    now = _utc_now()
    result = session.query(User).filter(
        (User.created_at.is_(None)) | 
        (User.created_at == "") | 
        (User.created_at == "None")
    ).update(
        {"created_at": now},
        synchronize_session=False
    )
    session.flush()
    logger.info(f"填充 created_at: {result} 条记录")
    return result


def fill_updated_at(session):
    """填充 updated_at 空值（使用 created_at 的值）"""
    # 先查出所有需要更新的记录
    users_to_update = session.query(User).filter(
        (User.updated_at.is_(None)) | 
        (User.updated_at == "") | 
        (User.updated_at == "None")
    ).all()
    
    count = 0
    for user in users_to_update:
        user.updated_at = user.created_at or _utc_now()
        session.add(user)
        count += 1
    
    session.flush()
    logger.info(f"填充 updated_at: {count} 条记录")
    return count


def fill_creator_id(session, system_user_id: str):
    """填充 creator_id 空值"""
    # 更新系统用户的 creator_id 为自引用
    system_result = session.query(User).filter(
        User.username == "system",
        (User.creator_id.is_(None)) | (User.creator_id == "") | (User.creator_id == "None")
    ).update(
        {"creator_id": system_user_id},
        synchronize_session=False
    )
    
    # 更新其他用户的 creator_id 为系统用户
    other_result = session.query(User).filter(
        User.username != "system",
        (User.creator_id.is_(None)) | (User.creator_id == "") | (User.creator_id == "None")
    ).update(
        {"creator_id": system_user_id},
        synchronize_session=False
    )
    
    session.flush()
    total = system_result + other_result
    logger.info(f"填充 creator_id: {total} 条记录")
    return total


def generate_username(user: User, existing_usernames: set) -> str:
    """为用户生成逻辑自洽的用户名"""
    # 策略1: 使用手机号后4位
    if user.phone and user.phone.strip() and user.phone.strip().lower() != "none":
        digits = ''.join(c for c in user.phone if c.isdigit())
        if len(digits) >= 4:
            base = f"user_{digits[-4:]}"
        else:
            base = f"user_{digits}"
    # 策略2: 使用邮箱前缀
    elif user.email and user.email.strip() and user.email.strip().lower() != "none":
        email_prefix = user.email.split('@')[0]
        import re
        base = re.sub(r'[^a-zA-Z0-9_]', '_', email_prefix)
        if len(base) > 20:
            base = base[:20]
    # 策略3: 使用 UUID 前8位
    else:
        base = f"user_{user.id[:8]}"
    
    # 处理重名
    final_name = base
    counter = 0
    while final_name in existing_usernames:
        counter += 1
        final_name = f"{base}_{counter}"
    
    existing_usernames.add(final_name)
    return final_name


def fill_username(session):
    """填充 username 空值"""
    # 获取所有现有用户名用于去重
    existing_usernames = set()
    for row in session.query(User.username).filter(
        User.username.is_not(None),
        User.username != "",
        User.username != "None"
    ).all():
        existing_usernames.add(row.username)
    
    # 查询需要填充用户名的记录
    users_to_update = session.query(User).filter(
        (User.username.is_(None)) | 
        (User.username == "") | 
        (User.username == "None")
    ).order_by(User.created_at).all()
    
    count = 0
    for user in users_to_update:
        new_username = generate_username(user, existing_usernames)
        user.username = new_username
        session.add(user)
        logger.info(f"  用户 {user.id} -> 用户名: {new_username}")
        count += 1
    
    session.flush()
    logger.info(f"填充 username: {count} 条记录")
    return count


def verify_fill_result(session):
    """验证填充结果"""
    logger.info("\n" + "="*60)
    logger.info("验证填充结果")
    logger.info("="*60)
    
    # 检查各字段空值数量
    null_username = session.query(User).filter(
        (User.username.is_(None)) | (User.username == "") | (User.username == "None")
    ).count()
    
    null_creator_id = session.query(User).filter(
        (User.creator_id.is_(None)) | (User.creator_id == "") | (User.creator_id == "None")
    ).count()
    
    null_created_at = session.query(User).filter(
        (User.created_at.is_(None)) | (User.created_at == "") | (User.created_at == "None")
    ).count()
    
    null_updated_at = session.query(User).filter(
        (User.updated_at.is_(None)) | (User.updated_at == "") | (User.updated_at == "None")
    ).count()
    
    if all(x == 0 for x in [null_username, null_creator_id, null_created_at, null_updated_at]):
        logger.info("✅ 所有必填字段填充完成！")
    else:
        logger.warning("⚠️  仍存在空值记录：")
        logger.warning(f"   - username: {null_username} 条")
        logger.warning(f"   - creator_id: {null_creator_id} 条")
        logger.warning(f"   - created_at: {null_created_at} 条")
        logger.warning(f"   - updated_at: {null_updated_at} 条")
    
    # 显示数据摘要
    logger.info("\n" + "="*60)
    logger.info("数据摘要（前20条）：")
    logger.info("="*60)
    
    users = session.query(User).order_by(User.created_at.desc()).limit(20).all()
    for user in users:
        logger.info(f"  {user.username:<20} | created: {user.created_at[:19]} | creator: {user.creator_id[:8]}")
    
    total = session.query(User).count()
    logger.info(f"\n总用户数: {total}")


def main():
    logger.info("="*60)
    logger.info("开始填充 users 表必填字段")
    logger.info("="*60)
    
    # 初始化数据库
    init_db()
    
    with get_session() as session:
        # 1. 获取或创建系统用户
        system_user_id = get_or_create_system_user(session)
        
        # 2. 按依赖顺序填充字段
        fill_created_at(session)       # 先填充 created_at
        fill_updated_at(session)       # 再填充 updated_at（依赖 created_at）
        fill_creator_id(session, system_user_id)  # 填充 creator_id
        fill_username(session)         # 填充 username
        
        # 3. 验证结果
        verify_fill_result(session)
        
        logger.info("\n" + "="*60)
        logger.info("填充完成！")
        logger.info("="*60)


if __name__ == "__main__":
    main()
