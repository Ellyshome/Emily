"""用户自动绑定服务 —— 将 IM 平台用户映射为 Emily 系统用户。

M2 规则：
- 已绑定 → 直接返回已有用户
- 未绑定 → 自动创建用户并绑定
- 用户名先用 IM 昵称填充，后续可人工补全
"""

import logging
from typing import Tuple

from ..repositories.user_repo import UserRepository
from ..infrastructure.database.models import User, UserImBinding

logger = logging.getLogger("emily.service.user_binding")


class UserBindingService:
    """用户自动绑定业务服务。"""

    def __init__(self):
        self.repo = UserRepository()

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
        """
        existing = self.repo.get_by_im(im_platform, im_user_id)
        if existing:
            logger.debug(
                "User already bound: %s -> %s (%s)",
                im_user_id, existing.id, existing.username,
            )
            return existing, False

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
