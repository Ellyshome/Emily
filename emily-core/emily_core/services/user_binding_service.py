"""用户自动绑定服务 —— 将 IM 平台用户映射为 Emily 系统用户。

M2 规则：
- 已绑定 → 直接返回已有用户
- UUID 直查 → sender_id 为 UUID 格式时先查 users 表（避免重复创建）
- 通道账号兜底 → 按平台匹配 users.qq / users.wechat（档案已登记账号但未建绑定行），命中即补建绑定
- 未绑定 → 根据 auto_create_user 配置决定是否自动创建
- 用户名先用 IM 昵称填充，后续可人工补全

BUG-002 修复：增加准入门禁，可通过配置关闭自动创建。
BUG-003 修复：增加 UUID 格式检测，emy-test 等工具传入 UUID 时走直查路径。
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

    # ── UUID 格式检测 ──

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        """判断 sender_id 是否看起来像 UUID（emy-test 等工具使用 UUID 作为 sender_id）。

        支持格式：
        - 标准 UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx（36 字符）
        - 无连字符 UUID: 32 字符十六进制
        """
        if not value:
            return False
        parts = value.split("-")
        if len(parts) == 5 and all(p.isalnum() for p in parts):
            return True
        if len(value) == 32 and value.isalnum():
            return True
        return False

    def get_or_create_user(
        self,
        im_platform: str,
        im_user_id: str,
        im_display_name: str | None = None,
    ) -> Tuple[User, bool]:
        """获取或创建用户（自动绑定）。

        查找优先级（BUG-003 + 通道账号兜底）：
        1. IM 绑定表查找（im_platform + im_user_id）
        2. UUID 直查：若 sender_id 为 UUID 格式，查 users 表（避免重复创建）
        3. 通道账号兜底：按平台匹配 users.qq / users.wechat（人事档案已登记账号，
           但没人建绑定行）；命中即补建绑定行，后续走 ① 快路径
        4. 自动创建新用户

        Args:
            im_platform: IM 平台，如 "napcat"
            im_user_id: IM 用户 ID（QQ 号或 UUID）
            im_display_name: IM 昵称

        Returns:
            (User, is_new): 用户对象，是否新创建

        Raises:
            UserNotAllowedError: 已知账号对不上人且 auto_create=False + 不在白名单时
        """
        # ① IM 绑定表查找
        existing = self.repo.get_by_im(im_platform, im_user_id)
        if existing:
            logger.debug(
                "User already bound: %s -> %s (%s)",
                im_user_id, existing.id, existing.username,
            )
            return existing, False

        # ② UUID 直查（BUG-003: emy-test 传 UUID 时不创建重复用户）
        if self._looks_like_uuid(im_user_id):
            direct_user = self.repo.get_by_id(im_user_id)
            if direct_user:
                logger.debug(
                    "User resolved by UUID: %s -> %s (%s)",
                    im_user_id, direct_user.id, direct_user.username,
                )
                return direct_user, False

        # ③ 通道账号兜底（人事档案 users.qq / users.wechat 已登记该通道账号）
        try:
            contact_user = self.repo.find_by_contact(im_platform, im_user_id)
        except Exception as e:  # noqa: BLE001 — 兜底查询失败按未命中处理
            logger.warning("find_by_contact failed (%s/%s): %s", im_platform, im_user_id, e)
            contact_user = None
        if contact_user:
            try:
                self.repo.ensure_binding(
                    user_id=contact_user.id,
                    im_platform=im_platform,
                    im_user_id=im_user_id,
                    im_display_name=im_display_name,
                )
            except Exception as e:  # noqa: BLE001 — 补绑定失败不影响本轮身份
                logger.warning("ensure_binding failed (%s/%s): %s", im_platform, im_user_id, e)
            return contact_user, False

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
