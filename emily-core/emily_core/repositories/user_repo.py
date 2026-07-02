"""用户 & IM 绑定关系读写抽象层。

Service 层只调 repo 方法，不碰 SQL。
"""

import logging
from typing import Tuple

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import User, UserImBinding

logger = logging.getLogger("emily.repo.user")


class UserRepository:
    """用户和 IM 绑定关系的 CRUD 操作。"""

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
            # 创建 User
            user = User(
                username=im_display_name or im_user_id,
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
        注：原 real_name 字段已移除，统一使用 username。
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
