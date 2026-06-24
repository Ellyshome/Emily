"""Emily Core —— 业务内核入口（Session 主线架构）。

蓝图 §1.2 逻辑分层：
    通信层(薄插件) → Adapter(Session 池) → Session(SessionAgent) → WorkItem(Pipeline BUS) → 基础设施

EmilyCore 是独立容器内的业务内核，不依赖任何 AstrBot 对象。它：
  · 延迟初始化基础设施（DB / LLM / RAG / 仓库 / 服务 / 工具）
  · 构建系统级公共 Pipeline BUS（全局单例 WorkItem-Agent + 声明式 Hook）
  · 构建 SessionPoolManager（消息路由 + Session 生命周期）
  · 暴露 handle_message 作为统一入站入口

与旧 TeamBrainCore 的区别：旧核心走 MessageApplication + 8 阶段 PipelineScheduler；
新核心走 SessionPool → SessionAgent → WorkItem → 4 节点 Pipeline BUS。
旧 agent/ 真实大脑随包迁移，作为 Phase B/C 接线储备，本期主路径用 Mock。
"""

import logging
from pathlib import Path
from typing import Optional

from .adapters.standard.message import StandardMessage
from .adapters.standard.reply import ReplyMessage
from .adapters.standard.route_decision import RouteDecision
from .config import Config
from .outbound_bus import OutboundEventBus
from .services.domain_takeover_service import DomainTakeoverService
from .services.user_binding_service import UserBindingService

logger = logging.getLogger("emily.core")


class EmilyCore:
    """Emily 业务内核 —— Session 主线编排。"""

    def __init__(self, config: Config, rag_provider=None):
        self.config = config
        self.takeover_service = DomainTakeoverService(config)
        self.user_binding_service = UserBindingService()

        # 出站事件总线（api/sse 订阅，向薄插件推送异步出站消息）
        self.outbound_bus = OutboundEventBus()

        # RAG 提供者（main/api 层提前创建后注入）
        self._rag_provider = rag_provider

        # 延迟初始化的子系统
        self._llm_client = None
        self._initialized = False

        # 公共 Pipeline BUS（全局单例）+ WorkItem-Agent（全局单例）
        self._bus = None
        self._workitem_agent = None

        # Session 池
        self._session_pool = None

    # ────────────────────────────────────────────────────────────────────
    # 延迟初始化
    # ────────────────────────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """延迟初始化基础设施 + 编排层（首次消息时触发）。"""
        if self._initialized:
            return

        # ── 基础设施：LLM（无 key 则跳过，Mock 路径不需要）──
        if self.config.llm_api_key:
            try:
                from .infrastructure.llm.client import LLMClient
                self._llm_client = LLMClient(
                    api_key=self.config.llm_api_key,
                    base_url=self.config.llm_base_url,
                    model=self.config.llm_model,
                    temperature=self.config.llm_temperature,
                    max_tokens=self.config.llm_max_tokens,
                )
                logger.info("LLM client initialized: model=%s", self.config.llm_model)
            except Exception as e:
                logger.error("LLM client init failed: %s", e)
                self._llm_client = None
        else:
            logger.info("No LLM API key — running with Mock WorkItem-Agent brain")

        # ── 公共 Pipeline BUS（4 节点 + 全局单例 WorkItem-Agent）──
        self._build_pipeline_bus()

        # ── Session 池 ──
        self._build_session_pool()

        self._initialized = True
        logger.info(
            "EmilyCore initialized: bus_hooks=%d, session_pool ready",
            self._bus.hook_count() if self._bus else 0,
        )

    def _build_pipeline_bus(self) -> None:
        """构建系统级公共 Pipeline BUS（蓝图 §5.4）。"""
        from .workitem import WorkItemAgent, PipelineBUS, KnowledgeInjector

        injector = KnowledgeInjector()
        self._workitem_agent = WorkItemAgent(injector=injector)
        self._bus = PipelineBUS.build_default(
            node_handlers=self._workitem_agent.node_handlers(),
            name="emily_bus",
        )

        # 声明式 Hook 配置（蓝图 §7.5 hook_config.json）
        hook_config = self._load_hook_config()
        if hook_config:
            injected = self._collect_injected_services()
            self._bus.register_hooks_from_config(hook_config, **injected)

    def _build_session_pool(self) -> None:
        """构建 Session 池（蓝图 §3.4）。"""
        from .adapters.session import SessionPoolManager, SessionConfig, SessionFactory

        session_config = SessionConfig.from_config(self.config)
        factory = SessionFactory(self._bus, core=self)
        self._session_pool = SessionPoolManager(
            bus=self._bus,
            config=session_config,
            factory=factory,
            core=self,
        )

    def _load_hook_config(self) -> dict | None:
        """加载 Hook 声明式配置（hook_config.json）。"""
        import json

        path = getattr(self.config, "hook_config_path", "") or ""
        candidates = []
        if path:
            candidates.append(Path(path))
        # 默认：emily-data/config/hook_config.json（容器内挂载到 /app/config）
        candidates.append(Path("/app/config/hook_config.json"))
        # 开发环境：Emily/emily-data/config/（__file__ = Emily/emily-core/emily_core/__init__.py）
        candidates.append(
            Path(__file__).resolve().parents[2] / "emily-data" / "config" / "hook_config.json"
        )
        for p in candidates:
            try:
                if p.exists():
                    with open(p, "r", encoding="utf-8") as f:
                        logger.info("Hook config loaded from: %s", p)
                        return json.load(f)
            except Exception as e:
                logger.warning("Failed to load hook config %s: %s", p, e)
        logger.info("No hook config found — BUS runs without declarative hooks")
        return None

    def _collect_injected_services(self) -> dict:
        """收集可注入到 Hook 的服务（本期最小：progress/trace 占位）。

        真实的 guardian_review / agent_trace_service / guardian_agent_factory
        接线属 Phase B/C（相关服务已随包迁移）。
        """
        injected: dict = {}
        # 前导消息发送器：把 progress 事件发到出站总线
        def _progress_sender(text: str):
            self.outbound_bus.publish("progress", {"content": text})
        injected["progress_sender"] = _progress_sender
        injected["progress_template"] = getattr(
            self.config, "progress_message_template", "收到，正在为你{action}，请稍候..."
        )
        return injected

    # ────────────────────────────────────────────────────────────────────
    # 入站入口
    # ────────────────────────────────────────────────────────────────────

    async def handle_message(
        self,
        message: StandardMessage,
        event_id: str = "",
        on_progress=None,
        on_send_file=None,
    ) -> ReplyMessage | None:
        """处理一条标准化入站消息（Session 主线）。

        流程：接管判定 → 用户绑定 → SessionPool 路由 → SessionAgent 处理 → 回复。

        Args:
            message: 标准化入站消息。
            event_id: 消息指纹（薄插件去重用，预留）。
            on_progress / on_send_file: 兼容旧回调签名（容器化后由 SSE 出站总线替代，
                如提供则桥接到 outbound_bus）。

        Returns:
            ReplyMessage: 需要同步回复时返回；None 表示不接管。
        """
        decision: RouteDecision = self.takeover_service.decide(message)
        if not decision.takeover:
            return None

        logger.info(
            "handle_message takeover=true mode=%s sender=%s",
            decision.mode, message.sender_name,
        )

        self._ensure_initialized()

        # 用户自动绑定（Adapter 层职责）
        user_id = ""
        try:
            user, _is_new = self.user_binding_service.get_or_create_user(
                im_platform=message.platform,
                im_user_id=message.sender_id,
                im_display_name=message.sender_name,
            )
            user_id = user.id if user else ""
        except Exception as e:
            logger.warning("user binding failed (continuing): %s", e)

        # SessionPool 路由（命中复用 / 未命中创建）
        reply = await self._session_pool.route(message, user_id=user_id)

        # 出站：同步回复直接返回；同时镜像到出站总线（供 SSE 订阅者）
        if reply is not None:
            self.outbound_bus.publish("reply", {
                "conversation_id": reply.conversation_id,
                "content": reply.content,
                "reply_to_message_id": reply.reply_to_message_id,
            })
        return reply

    async def terminate_session(self, conversation_id: str) -> bool:
        """强制终止指定 Session（蓝图 §2.6）。"""
        self._ensure_initialized()
        ok = await self._session_pool.terminate(conversation_id)
        if ok:
            self.outbound_bus.publish("session_closed", {"conversation_id": conversation_id})
        return ok

    # ────────────────────────────────────────────────────────────────────
    # 健康/状态
    # ────────────────────────────────────────────────────────────────────

    def health(self) -> dict:
        """健康状态（蓝图 §2.6 /health）。"""
        pool = self._session_pool
        return {
            "status": "ok",
            "initialized": self._initialized,
            "sessions": pool.size if pool else 0,
            "uptime": pool.uptime_seconds if pool else 0,
            "bus_hooks": self._bus.hook_count() if self._bus else 0,
        }
