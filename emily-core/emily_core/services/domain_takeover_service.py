"""DomainTakeoverService —— 分域接管判断服务。

M1 阶段仅基于 @机器人 + 会话类型判断是否接管。
后续版本可扩展 project 绑定、关键词匹配等维度。
"""

import logging

from ..adapters.standard.message import StandardMessage
from ..adapters.standard.route_decision import RouteDecision
from ..config import Config

logger = logging.getLogger("emily.domain_takeover")


class DomainTakeoverService:
    """判断当前消息是否应由 Emily 接管。

    M1 规则:
        - 群聊中 @了机器人 → takeover=true
        - 私聊消息 → takeover=true
        - 观察模式 → takeover=false, 不回复 (仅在此模式)
        - 监控模式 → 全量接管但仅 @ 时回复（静默收集群聊消息与文件）
        - 其他 → takeover=false
    """

    def __init__(self, config: Config):
        self.config = config

    def decide(self, message: StandardMessage) -> RouteDecision:
        mode = self.config.takeover_mode

        # 观察模式：不接管任何消息，不回复
        if mode == "observe":
            logger.debug("takeover=false, reason=observe_mode")
            return RouteDecision(
                takeover=False,
                mode=mode,
                should_reply=False,
                reason="observe_mode",
            )

        # 监控模式：全量接管，但仅 @ 时回复（静默收集群聊消息与文件）
        if mode == "monitor":
            if message.conversation_type == "private":
                logger.info("takeover=true, reason=monitor_private")
                return RouteDecision(
                    takeover=True,
                    mode=mode,
                    should_reply=True,
                    reason="monitor_private",
                )
            if message.is_at_bot:
                logger.info("takeover=true, reason=monitor_at_bot")
                return RouteDecision(
                    takeover=True,
                    mode=mode,
                    should_reply=True,
                    reason="monitor_at_bot",
                )
            logger.info("takeover=true, reason=monitor_silent_collect")
            return RouteDecision(
                takeover=True,
                mode=mode,
                should_reply=False,
                reason="monitor_silent_collect",
            )

        # 托管模式：接管所有消息 (M1 不启用，预留)
        if mode == "managed":
            logger.info("takeover=true, reason=managed_mode")
            return RouteDecision(
                takeover=True,
                mode=mode,
                reason="managed_mode",
            )

        # 协作模式（默认）：仅接管明确提及机器人的消息
        # 私聊直接接管
        if message.conversation_type == "private":
            logger.info("takeover=true, reason=private_message")
            return RouteDecision(
                takeover=True,
                mode=mode,
                reason="private_message",
            )

        # 群聊中 @了机器人 → 接管
        if message.is_at_bot:
            logger.info("takeover=true, reason=at_bot_in_group")
            return RouteDecision(
                takeover=True,
                mode=mode,
                reason="at_bot_in_group",
            )

        # 群聊未 @机器人 → 放行（collaborate / observe 等模式的兜底）
        logger.debug("takeover=false, reason=group_message_not_at_bot")
        return RouteDecision(
            takeover=False,
            mode=mode,
            should_reply=False,
            reason="group_message_not_at_bot",
        )
