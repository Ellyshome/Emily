"""挂起与多轮续接模块（M5）—— 挂起即对话状态。

定位（计划 M5 / PRD D3、R7、US-06）：
  - 挂起 = 对话中"待回答的能力调用"，由能力返回 needs_input 触发；
  - 续接判定沿用"相关则续、无关则新话题"口径，犹豫时倾向于不续接；
  - 多能力并发挂起按"谁发起谁确认"归属；
  - **纯内存**：不落库、不引入任何持久化机制（PRD §4.4-6 / D3 书面接受的缺口：
    进程重启后未完成的能力调用即失效，用户需重新提出）。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("emily.session.suspend_registry")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PendingCall:
    """一条待回答的能力调用。"""

    call_id: str = ""
    capability: str = ""                       # 能力名（续接时继续调用它）
    params: dict = field(default_factory=dict)  # 已收集的部分入参
    question: str = ""                          # 已向用户提出的问题
    initiator_user_id: str = ""                 # 谁发起（谁确认）
    step_id: str = ""                           # 若来自能力调用计划，记录步骤
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.call_id:
            self.call_id = f"PC-{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = _now_iso()


_CONTINUATION_PROMPT = (
    "判断用户当前消息是否在回答系统上一轮的提问。只输出 JSON：{\"continuation\": true|false}。"
    "仅当用户消息与提问**直接相关**（在补充所缺信息）时输出 true；"
    "犹豫不决时输出 false —— 宁可新开话题，不要误判续接。"
)


class SuspendRegistry:
    """会话内挂起登记表（每 Session 一个实例）。"""

    def __init__(self, llm_client=None, config=None) -> None:
        self._llm = llm_client
        self._config = config
        self._pending: list = []

    # ── 登记 / 作废 ──

    def register(self, pending: PendingCall) -> None:
        self._pending.append(pending)
        logger.info("SuspendRegistry: register %s capability=%s initiator=%s",
                    pending.call_id, pending.capability, pending.initiator_user_id or "?")

    def discard_all(self) -> int:
        n = len(self._pending)
        self._pending = []
        if n:
            logger.info("SuspendRegistry: discarded %d pending call(s)", n)
        return n

    def discard(self, call_id: str) -> bool:
        before = len(self._pending)
        self._pending = [p for p in self._pending if p.call_id != call_id]
        return len(self._pending) != before

    # ── 归属（谁发起谁确认）──

    def match(self, actor_user_id: str) -> PendingCall | None:
        """取该操作者最近一条挂起。"""
        if not actor_user_id:
            return self._pending[-1] if self._pending else None
        for p in reversed(self._pending):
            if p.initiator_user_id == actor_user_id:
                return p
        return None

    def claim(self, call_id: str, actor_user_id: str = "") -> PendingCall | None:
        """认领挂起（转入续接执行）并从登记表移除。"""
        p = next((x for x in self._pending if x.call_id == call_id), None)
        if p is None:
            return None
        if actor_user_id and p.initiator_user_id and p.initiator_user_id != actor_user_id:
            logger.warning("SuspendRegistry: %s claimed by wrong user (%s != %s)",
                           call_id, actor_user_id, p.initiator_user_id)
            return None
        self._pending = [x for x in self._pending if x.call_id != call_id]
        return p

    # ── 续接判定 ──

    async def is_continuation(self, user_input: str, actor_user_id: str = "") -> bool:
        """用户消息是否是对挂起提问的回答。

        LLM 不可用 / 无挂起 / 判定失败 → False（倾向于新话题）。
        """
        pending = self.match(actor_user_id)
        if pending is None:
            return False
        if self._llm is None:
            return False
        try:
            result = await self._llm.chat_messages([
                {"role": "system", "content": _CONTINUATION_PROMPT},
                {"role": "user", "content": (
                    f"## 系统上一轮提问\n{pending.question}\n\n"
                    f"## 用户当前消息\n{user_input}"
                )},
            ], json_mode=True)
            data = (result or {}).get("data") or {}
            cont = bool(data.get("continuation")) if isinstance(data, dict) else False
            logger.info("SuspendRegistry: continuation=%s (pending=%s)", cont, pending.call_id)
            return cont
        except Exception as e:  # noqa: BLE001
            logger.warning("SuspendRegistry: continuation judge failed: %s", e)
            return False

    # ── 状态 ──

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def list_all(self) -> list:
        return list(self._pending)
