"""Application 层用户信息工具。

提供跨 Application 共享的用户名解析逻辑，避免在各 handler 中重复实现。
"""

import logging

logger = logging.getLogger("emily.app.user_utils")


def resolve_user_name(user_id: str) -> str:
    """根据 user_id (UUID) 查询真实姓名。

    用于 journal.append() 或其他需要展示用户可读姓名的场景。
    参考 EventApplication.handle_confirmation() 中已验证的实现。

    Args:
        user_id: 用户 UUID（可以是 UUID 或空字符串）

    Returns:
        用户真实姓名，查询失败返回空字符串
    """
    if not user_id:
        return ""
    try:
        from ..repositories.user_repo import UserRepository
        u = UserRepository.get(user_id)
        if u:
            return getattr(u, "real_name", "") or getattr(u, "username", "") or ""
    except Exception as e:
        logger.debug("resolve_user_name failed for %s: %s", user_id, e)
    return ""
