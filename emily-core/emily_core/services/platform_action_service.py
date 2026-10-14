"""PlatformActionService —— 按需调用平台动作的请求-响应通道（内核侧）。

背景：QQ 侧的 OneBot 连接由薄插件（AstrBot 容器）独持，内核无法直接调用
`get_group_list` / `get_group_member_list`。本服务把"按需拉取平台数据"实现为
一次可等待的往返：

    内核 request() → OutboundEventBus.publish("platform_action")
                   → （既有 SSE 出站通道）→ 薄插件 SSEListener._handlers["platform_action"]
                   → 插件 bot.call_action(动作白名单校验)
                   → POST /api/v1/platform/action-result
                   → 本服务 resolve() 兑现 future → request() 返回

设计约束（见《群信息保存与复用_计划_V3》M2）：
  · 复用既有 OutboundEventBus 的事件类型扩展点，**不改出站总线本身**；
  · 不新增监听端口、不改部署拓扑、不引入新依赖（薄插件是平台边界的唯一持有者）；
  · 一次请求 = 一次动作，事件驱动、无定时、无常驻（系统内不存在周期机制）；
  · 插件离线（无 SSE 订阅者）时**立即**失败，不空等超时；
  · 同一 (action, params) 在途时合流，避免并发重复调用平台接口（QQ 侧有频率限制）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

logger = logging.getLogger("emily.platform_action")

#: SSE 出站事件类型（插件侧同名 handler 注册）
EVENT_TYPE = "platform_action"

#: 未指定动作超时时的默认等待秒数。控制台"人在场"路径可接受数秒等待。
DEFAULT_TIMEOUT = 8.0


class PlatformActionService:
    """平台动作的请求-响应服务（内核侧唯一入口）。"""

    def __init__(self, outbound_bus=None):
        """
        Args:
            outbound_bus: OutboundEventBus 实例（发布事件 + 读取订阅者数）。
        """
        self._bus = outbound_bus
        self._pending: dict[str, asyncio.Future] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    # ── 对外接口 ──

    def subscriber_count(self) -> int:
        """当前 SSE 订阅者数（0 = 薄插件未连接）。"""
        if self._bus is None:
            return 0
        return int(self._bus.subscriber_count)

    def pending_count(self) -> int:
        """在途请求数（可观测性；正常应为 0）。"""
        return len(self._pending)

    async def request(
        self,
        action: str,
        params: dict | None = None,
        *,
        platform: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        """发起一次平台动作并等待插件回调结果。

        Args:
            action: OneBot 动作名（如 get_group_list / get_group_member_list）。
            params: 动作参数（如 {"group_id": "12345"}）。
            platform: 目标平台标识（留空 = 由插件挑选第一个可用平台实例）。
            timeout: 等待秒数。

        Returns:
            {"ok": bool, "data": Any, "error": str, "request_id": str}
            —— 失败一律如实回报原因，不抛异常（调用方据此决定是否降级）。
        """
        params = dict(params or {})
        if not action:
            return self._fail("", "未指定平台动作")

        if self._bus is None:
            return self._fail("", "出站总线未就绪，无法请求平台动作")

        if self.subscriber_count() <= 0:
            # 无订阅者时 publish 会静默丢弃事件：立即如实失败，不白等 timeout
            logger.warning("platform action '%s' skipped: no SSE subscriber (plugin offline)", action)
            return self._fail("", "平台插件未连接（无 SSE 订阅者），无法拉取平台数据")

        # 同一 (action, params, platform) 在途时合流：避免并发重复调用平台接口
        key = self._join_key(action, params, platform)
        async with self._lock:
            existing = self._inflight.get(key)
            joined = existing is not None

        if joined:
            logger.info("platform action '%s' joined an in-flight request", action)
            return await asyncio.shield(existing)

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        async with self._lock:
            self._inflight[key] = future
            self._pending[request_id] = future

        try:
            self._bus.publish(EVENT_TYPE, {
                "request_id": request_id,
                "action": action,
                "params": params,
                "platform": platform,
            })
            logger.info("platform action '%s' dispatched (request_id=%s)", action, request_id)
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("platform action '%s' timed out after %.1fs", action, timeout)
                return self._fail(request_id, f"平台动作超时（{timeout:.1f}s）：{action}")
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)
                # future 未完成时（如外部取消）也应释放合流位，避免后续请求永久合流到死 future
                if self._inflight.get(key) is future:
                    self._inflight.pop(key, None)

    def resolve(self, request_id: str, ok: bool, data=None, error: str = "") -> bool:
        """由回调路由调用：兑现一次在途请求。

        Returns:
            True = 命中并已兑现；False = 未知 request_id（已超时或伪造），调用方应据此拒绝。
        """
        future = self._pending.get(request_id)
        if future is None:
            logger.warning("platform action result for unknown request_id=%s (timed out or forged)",
                           request_id)
            return False
        if future.done():
            return True
        future.set_result({
            "ok": bool(ok),
            "data": data,
            "error": str(error or ""),
            "request_id": request_id,
        })
        return True

    # ── 内部 ──

    @staticmethod
    def _join_key(action: str, params: dict, platform: str) -> str:
        try:
            payload = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 — 不可序列化参数退化为按动作合流
            payload = repr(params)
        return f"{platform}|{action}|{payload}"

    @staticmethod
    def _fail(request_id: str, error: str) -> dict:
        return {"ok": False, "data": None, "error": error, "request_id": request_id}
