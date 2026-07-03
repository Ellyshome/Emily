"""用户自动绑定服务 —— 将 IM 平台用户映射为 Emily 系统用户。

M2 规则：
- 已绑定 → 直接返回已有用户
- 未绑定 → 根据 auto_create_user 配置决定是否自动创建
- 用户名先用 IM 昵称填充，后续可人工补全

BUG-002 修复：增加准入门禁，可通过配置关闭自动创建。
"""

import logging
from typing import Tuple

from ..repositories.user_repo import UserRepository
from ..infrastructure.database.models import User, UserImBinding

logger = logging.getLogger("emily.service.user_binding")


class UserNotAllowedError(Exception):
    """未知 IM 用户被拒绝自动创建时抛出。"""
    pass


class UserBindingService:
    """用户自动绑定业务服务。"""

    def __init__(self, auto_create: bool = True, whitelist: list | None = None):
        self.repo = UserRepository()
        self._auto_create = auto_create
        self._whitelist = whitelist or []

    def get_or_create_user(
        self,
        im_platform: str,
        im_user_id: str,
        im_display_name: str | None = None,
    ) -> Tuple[User, bool]:
        """获取或创建用户（自动绑定）。

        Args:
            im_platform: IM 平台，如 "napcat"
            im_user_id: IM 用户 ID（QQ 号）
            im_display_name: IM 昵称

        Returns:
            (User, is_new): 用户对象，是否新创建

        Raises:
            UserNotAllowedError: 未知用户且 auto_create=False + 不在白名单时
        """
        existing = self.repo.get_by_im(im_platform, im_user_id)
        if existing:
            logger.debug(
                "User already bound: %s -> %s (%s)",
                im_user_id, existing.id, existing.username,
            )
            return existing, False

        # BUG-002: 准入门禁 — 检查是否允许自动创建
        if not self._allow_auto_create(im_platform, im_user_id):
            logger.warning(
                "User auto-create denied: platform=%s im_user_id=%s "
                "(auto_create=%s, whitelist=%s)",
                im_platform, im_user_id,
                self._auto_create, bool(self._whitelist),
            )
            raise UserNotAllowedError(
                f"未授权的发送者 {im_user_id}，请联系管理员注册"
            )

        # 自动创建新用户
        user, _ = self.repo.create_user_and_bind(
            im_platform=im_platform,
            im_user_id=im_user_id,
            im_display_name=im_display_name,
        )
        logger.info(
            "Auto-created user: %s (%s) bound to %s/%s",
            user.id, user.username, im_platform, im_user_id,
        )
        return user, True

    def _allow_auto_create(self, im_platform: str, im_user_id: str) -> bool:
        """检查是否允许为该 IM 用户自动创建系统用户。

        策略：
        - auto_create=True → 允许（默认，开发/测试用）
        - auto_create=False → 仅白名单内 ID 允许
        """
        if self._auto_create:
            return True
        # auto_create=False 时检查白名单
        return im_user_id in self._whitelist
