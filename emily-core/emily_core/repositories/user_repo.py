"""用户 & IM 绑定关系读写抽象层。

Service 层只调 repo 方法，不碰 SQL。
"""

import logging
import uuid
from typing import Tuple

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import User, UserImBinding

logger = logging.getLogger("emily.repo.user")


class UserRepository:
    """用户和 IM 绑定关系的 CRUD 操作。"""

    #: 通道 → 人事档案里的账号列（users.qq / users.wechat）
    _CONTACT_COLUMN_BY_PLATFORM = {
        "napcat": "qq", "qq": "qq", "aiocqhttp": "qq", "onebot": "qq",
        "wecom": "wechat", "wechat": "wechat", "wxmp": "wechat", "wechat_mp": "wechat",
    }

    @staticmethod
    def get_by_im(im_platform: str, im_user_id: str) -> User | None:
        """根据 IM 平台 + IM 用户 ID 查找已绑定的系统用户。

        Args:
            im_platform: IM 平台，如 "napcat"
            im_user_id: IM 用户 ID（QQ 号）

        Returns:
            已绑定的 User 对象，未找到返回 None。
        """
        with get_session() as session:
            binding = (
                session.query(UserImBinding)
                .filter(
                    UserImBinding.im_platform == im_platform,
                    UserImBinding.im_user_id == im_user_id,
                    UserImBinding.status == "active",
                )
                .first()
            )
            if binding:
                return session.query(User).filter(User.id == binding.user_id).first()
            return None

    @staticmethod
    def find_by_contact(im_platform: str, im_user_id: str) -> User | None:
        """按通道账号反查人员（users.qq / users.wechat 兜底）。

        用于「人事档案已登记该通道账号、但 user_im_bindings 尚未建行」的情况
        （历史数据只填了 users.qq / users.wechat，没人建绑定行）。按平台取对应
        账号列精确匹配；平台未知时按 qq → wechat → phone 顺序兜底。

        Args:
            im_platform: IM 平台，如 "napcat" / "wecom"
            im_user_id: 通道内用户 ID（QQ 号 / 微信号 / 企业微信 userid）

        Returns:
            匹配到的 User 对象，未找到返回 None。
        """
        value = (im_user_id or "").strip()
        if not value:
            return None
        column = UserRepository._CONTACT_COLUMN_BY_PLATFORM.get(
            (im_platform or "").strip().lower())
        columns = [column] if column else ["qq", "wechat", "phone"]
        with get_session() as session:
            for col in columns:
                field = getattr(User, col, None)
                if field is None:
                    continue
                user = (
                    session.query(User)
                    .filter(field == value, User.is_deleted == False, User.status == "active")
                    .first()
                )
                if user:
                    logger.info(
                        "User resolved by contact column users.%s: %s -> %s",
                        col, value, user.id,
                    )
                    return user
            return None

    @staticmethod
    def ensure_binding(
        user_id: str,
        im_platform: str,
        im_user_id: str,
        im_display_name: str | None = None,
    ) -> bool:
        """确保 (平台, 通道用户 ID) → user_id 的生效绑定存在（幂等补建）。

        仅在绑定行不存在时补建；已存在但指向他人（人工改绑/冲突）不覆盖，
        只记 warning 交人工处理。用于「按人事档案账号解析成功」后固化绑定，
        后续消息即可走绑定表快路径。

        Returns:
            bool: 本次是否补建（或把非 active 的同人绑定恢复为 active）。
        """
        if not user_id or not im_platform or not im_user_id:
            return False
        with get_session() as session:
            existing = (
                session.query(UserImBinding)
                .filter(
                    UserImBinding.im_platform == im_platform,
                    UserImBinding.im_user_id == im_user_id,
                )
                .first()
            )
            if existing is not None:
                if existing.user_id != user_id:
                    logger.warning(
                        "ensure_binding conflict: %s/%s already bound to %s, skip %s",
                        im_platform, im_user_id, existing.user_id, user_id,
                    )
                    return False
                if existing.status != "active":
                    existing.status = "active"
                    logger.info(
                        "ensure_binding reactivated: %s/%s -> %s",
                        im_platform, im_user_id, user_id,
                    )
                    return True
                return False
            session.add(UserImBinding(
                user_id=user_id,
                im_platform=im_platform,
                im_user_id=im_user_id,
                im_display_name=im_display_name or None,
            ))
            logger.info(
                "Auto-bound channel account %s/%s -> user %s",
                im_platform, im_user_id, user_id,
            )
            return True

    @staticmethod
    def create_user_and_bind(
        im_platform: str,
        im_user_id: str,
        im_display_name: str | None = None,
    ) -> Tuple[User, UserImBinding]:
        """创建新系统用户并绑定 IM 账号。

        M2 规则：首次发消息给 Emy 自动创建用户，
        username 先用 im_display_name 填充，后续可人工补全信息。

        Args:
            im_platform: IM 平台
            im_user_id: IM 用户 ID
            im_display_name: IM 昵称

        Returns:
            (User, UserImBinding) 元组
        """
        with get_session() as session:
            # 创建 User（自注册：creator_id = 自身 id）
            new_id = str(uuid.uuid4())
            user = User(
                id=new_id,
                username=im_display_name or im_user_id,
                creator_id=new_id,
            )
            session.add(user)
            session.flush()  # 获取 user.id

            # 创建 Binding
            binding = UserImBinding(
                user_id=user.id,
                im_platform=im_platform,
                im_user_id=im_user_id,
                im_display_name=im_display_name,
            )
            session.add(binding)
            session.flush()  # 确保 binding 也有 id

            logger.info(
                "Created user %s (%s) and bound to %s/%s",
                user.id, user.username, im_platform, im_user_id,
            )
            return user, binding

    @staticmethod
    def get(user_id: str) -> User | None:
        """按 ID 查找用户。"""
        with get_session() as session:
            return session.query(User).filter(User.id == user_id).first()

    @staticmethod
    def get_by_id(user_id: str) -> User | None:
        """按 ID 查找用户（get 的语义化别名，供 Service 层统一调用）。"""
        return UserRepository.get(user_id)

    @staticmethod
    def find_by_name(name: str) -> User | None:
        """按用户名查找用户（执行人姓名 → user_id 解析，best-effort）。

        匹配：username 精确。返回首个匹配，未找到返回 None。
        注：用户显示名统一使用 username。
        """
        if not name:
            return None
        with get_session() as session:
            return (
                session.query(User)
                .filter(User.username == name, User.is_deleted == False)
                .first()
            )

    @staticmethod
    def update_user(user_id: str, **kwargs) -> User | None:
        """更新用户信息（后续补全资料用）。

        Args:
            user_id: 用户 ID
            **kwargs: 要更新的字段键值对
        """
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user:
                for k, v in kwargs.items():
                    if hasattr(user, k):
                        setattr(user, k, v)
            return user

    @staticmethod
    def get_binding(im_platform: str, im_user_id: str) -> UserImBinding | None:
        """按 IM 平台+用户ID 查找绑定记录。"""
        with get_session() as session:
            return (
                session.query(UserImBinding)
                .filter(
                    UserImBinding.im_platform == im_platform,
                    UserImBinding.im_user_id == im_user_id,
                )
                .first()
            )

    # ── M5 查询 ──

    @staticmethod
    def list_users(status: str = "active", limit: int = 50) -> list[User]:
        """列出系统用户。"""
        with get_session() as session:
            q = session.query(User)
            if status:
                q = q.filter(User.status == status)
            return q.order_by(User.created_at.desc()).limit(limit).all()

    @staticmethod
    def count_users(status: str = "active") -> int:
        """用户总数。"""
        with get_session() as session:
            q = session.query(User)
            if status:
                q = q.filter(User.status == status)
            return q.count()

    # ── 人员台账（console 人员管理模块，只读 + 窄写）──

    @staticmethod
    def list_personnel(limit: int = 2000) -> list[dict]:
        """人员台账五列查询：人员ID / 姓名 / 所属企业 / 登记时间 / 录入人。

        一次连接查询取齐 company_name 与 creator_name（自连接），避免 N+1。
        后台视角全量（仅排除软删），不做可见范围收敛（需求基线 Q6）。

        Returns:
            dict 列表，字段：user_id / username / company_id / company_name /
            created_at / creator_id / creator_name（空值以空串返回）。
        """
        from sqlalchemy.orm import aliased

        from ..infrastructure.database.models import CompanyInfo

        Creator = aliased(User)
        with get_session() as session:
            rows = (
                session.query(
                    User.id.label("user_id"),
                    User.username.label("username"),
                    User.company.label("company_id"),
                    CompanyInfo.company_name.label("company_name"),
                    User.created_at.label("created_at"),
                    User.creator_id.label("creator_id"),
                    Creator.username.label("creator_name"),
                    User.level.label("level"),
                )
                .outerjoin(CompanyInfo, CompanyInfo.id == User.company)
                .outerjoin(Creator, Creator.id == User.creator_id)
                .filter(User.is_deleted.isnot(True))
                .order_by(User.created_at.desc())
                .limit(limit)
                .all()
            )
        return [
            {
                "user_id": r.user_id or "",
                "username": r.username or "",
                "company_id": r.company_id or "",
                "company_name": r.company_name or "",
                "created_at": r.created_at or "",
                "creator_id": r.creator_id or "",
                "creator_name": r.creator_name or "",
                "level": int(r.level or 0),
            }
            for r in rows
        ]

    @staticmethod
    def set_level(user_id: str, level: int) -> User | None:
        """只写 users.level（窄写，无校验无授权——校验/授权在 service 层）。"""
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user is None:
                return None
            user.level = level
            return user

    @staticmethod
    def set_company(user_id: str, company_id: str | None) -> User | None:
        """只写 users.company（窄写；company_id 为空表清空归属）。"""
        with get_session() as session:
            user = session.query(User).filter(User.id == user_id).first()
            if user is None:
                return None
            user.company = company_id or None
            return user
