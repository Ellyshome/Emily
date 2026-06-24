"""Emily Core —— 业务内核入口（Session 主线架构）。

蓝图 §1.2 逻辑分层：
    通信层(薄插件) → Adapter(Session 池) → Session(SessionAgent) → WorkItem(Pipeline BUS) → 基础设施

EmilyCore 是独立容器内的业务内核，不依赖任何 AstrBot 对象。它：
  · 延迟初始化基础设施（DB / LLM / RAG / 仓库 / 服务 / 工具）
  · 构建系统级公共 Pipeline BUS（全局单例 WorkItem-Agent + 声明式 Hook）
  · 构建 SessionPoolManager（消息路由 + Session 生命周期）
  · 暴露 handle_message 作为统一入站入口

Phase C 升级（蓝图 §12.2）：
  · MockWorkAgent → 真实执行引擎（M14 BusinessFlowToolRegistry 直调）
  · MockGuardian → GuardianReview + GuardianAgent（轻量/深度审计）
  · MockAuthEngine → 角色鉴权（SOP allow_roles）
  · MockRiskGrader → 基于规则的风险评估
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
    """Emily 业务内核 —— Session 主线编排。

    Phase C 升级：
      · _business_flow_tools → 执行引擎（M14 工具直调）
      · _guardian_review → 守护审核（轻量 + 深度）
      · _guardian_agent_factory → DeepAuditHook
    """

    def __init__(self, config: Config, rag_provider=None):
        self.config = config
        self.takeover_service = DomainTakeoverService(config)
        self.user_binding_service = UserBindingService()

        # 出站事件总线
        self.outbound_bus = OutboundEventBus()

        # RAG 提供者
        self._rag_provider = rag_provider

        # 延迟初始化的子系统
        self._llm_client = None
        self._initialized = False

        # 公共 Pipeline BUS + WorkItem-Agent
        self._bus = None
        self._workitem_agent = None

        # Session 池
        self._session_pool = None

        # Phase B/C: 共享基础设施
        self._sop_intent_registry = None
        self._tool_registry = None

        # Phase C: 执行和守护依赖
        self._business_flow_tools = None
        self._guardian_review = None
        self._guardian_agent_factory = None

    # ────────────────────────────────────────────────────────────────────
    # 延迟初始化
    # ────────────────────────────────────────────────────────────────────

    def _ensure_initialized(self) -> None:
        """延迟初始化基础设施 + 编排层（首次消息时触发）。"""
        if self._initialized:
            return

        # ── 基础设施：LLM ──
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

        # ── Phase B: SOP 意图注册表 + 工具注册表 ──
        self._init_phase_b_deps()

        # ── Phase C: 执行 + 守护依赖 ──
        self._init_phase_c_deps()

        # ── 公共 Pipeline BUS ──
        self._build_pipeline_bus()

        # ── Session 池 ──
        self._build_session_pool()

        self._initialized = True
        logger.info(
            "EmilyCore initialized: bus_hooks=%d, session_pool ready",
            self._bus.hook_count() if self._bus else 0,
        )

    def _init_phase_b_deps(self) -> None:
        """Phase B: 初始化 SOP 意图注册表 + 工具注册表。"""
        # 1. SOP 意图注册表
        try:
            from .agent.intent_registry import SOPIntentRegistry
            # 多级 fallback: 容器内默认 > 环境变量 > 宿主机开发路径
            sop_dir = "/app/sops"                             # 容器内默认（最高优先级）
            if not Path(sop_dir).exists():
                sop_dir = self.config.sop_repository_dir or ""  # 环境变量
            if not sop_dir or not Path(sop_dir).exists():
                # 开发环境 fallback
                dev_dir = str(Path(__file__).resolve().parents[2] / "emily-data" / "sops")
                if Path(dev_dir).exists():
                    sop_dir = dev_dir
            self._sop_intent_registry = SOPIntentRegistry(sop_directory=sop_dir)
            status = self._sop_intent_registry.load()
            logger.info("Phase B: SOPIntentRegistry loaded from %s — %s", sop_dir, status)
        except Exception as e:
            logger.warning("Phase B: SOPIntentRegistry init failed: %s", e)
            self._sop_intent_registry = None

        # 2. 工具注册表（Phase B: 仅当 service 初始化成功后才注册 LLM 工具）
        if self._llm_client:
            try:
                from .agent.tool_registry import ToolRegistry
                from .tools import create_all_tools
                self._tool_registry = ToolRegistry()
                # Phase C: Skip create_all_tools in mock mode (needs DB services).
                # LLM tools are only needed for ReAct fallback; M14 execution uses BusinessFlowToolRegistry.
                logger.info("Phase B: ToolRegistry created (LLM tools deferred to DB init)")
            except Exception as e:
                logger.warning("Phase B: ToolRegistry init failed: %s", e)
                self._tool_registry = None

    def _init_phase_c_deps(self) -> None:
        """Phase C: 初始化执行引擎 + 守护审核依赖。"""
        # 1. BusinessFlowToolRegistry（执行引擎）
        try:
            from .tools.business_flow_tools import BusinessFlowToolRegistry, BusinessFlowTool
            from .tools.event_tool import handle_record_event
            from .tools.task_tool import handle_record_task
            from .tools.meeting_tool import handle_record_meeting
            from .tools.file_tool import handle_record_file
            from .tools.query_tool import handle_query_data

            self._business_flow_tools = BusinessFlowToolRegistry()
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_event", description="记录项目事件",
                parameters={"type": "object", "properties": {}}, handler=handle_record_event,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_task", description="创建任务",
                parameters={"type": "object", "properties": {}}, handler=handle_record_task,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_meeting", description="归档会议纪要",
                parameters={"type": "object", "properties": {}}, handler=handle_record_meeting,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_file", description="记录文件元数据",
                parameters={"type": "object", "properties": {}}, handler=handle_record_file,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="query_data", description="查询项目数据",
                parameters={"type": "object", "properties": {}}, handler=handle_query_data,
            ))
            logger.info("Phase C: BusinessFlowToolRegistry initialized with 5 tools")
        except Exception as e:
            logger.warning("Phase C: BusinessFlowToolRegistry init failed: %s", e)
            self._business_flow_tools = None

        # 2. GuardianReview（轻量验证器）
        if self._llm_client:
            try:
                from .agent.guardian_review import GuardianReview
                self._guardian_review = GuardianReview(self._llm_client, self.config)
                logger.info("Phase C: GuardianReview initialized")

                # 3. GuardianAgent factory（深度审计，按需创建）
                def _guardian_agent_factory():
                    from .agent.guardian_agent import GuardianAgent
                    return GuardianAgent(
                        llm_client=self._llm_client,
                        query_service=None,  # Phase C: query_service 后续 DB 初始化时注入
                        config=self.config,
                        notebook_dir=getattr(self.config, "notebook_dir", "") or "",
                    )
                self._guardian_agent_factory = _guardian_agent_factory
                logger.info("Phase C: GuardianAgent factory ready")
            except Exception as e:
                logger.warning("Phase C: GuardianReview init failed: %s", e)
                self._guardian_review = None

    def _build_pipeline_bus(self) -> None:
        """构建系统级公共 Pipeline BUS（蓝图 §5.4 + Phase C 升级）。"""
        from .workitem import WorkItemAgent, PipelineBUS, KnowledgeInjector

        injector = KnowledgeInjector(
            sop_intent_registry=self._sop_intent_registry,
            sop_loader=None,
            tool_registry=self._tool_registry,
        )
        self._workitem_agent = WorkItemAgent(
            injector=injector,
            llm_client=self._llm_client,
            config=self.config,
            # Phase C: 执行 + 守护依赖
            business_flow_tools=self._business_flow_tools,
            guardian_review=self._guardian_review,
            sop_intent_registry=self._sop_intent_registry,
            rag_provider=self._rag_provider,
        )
        self._bus = PipelineBUS.build_default(
            node_handlers=self._workitem_agent.node_handlers(),
            name="emily_bus",
        )

        # Hook 配置
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
        candidates.append(Path("/app/config/hook_config.json"))
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
        """Phase C: 收集可注入到 Hook 的全量服务。"""
        injected: dict = {}

        # 前导消息发送器
        def _progress_sender(text: str):
            self.outbound_bus.publish("progress", {"content": text})
        injected["progress_sender"] = _progress_sender
        injected["progress_template"] = getattr(
            self.config, "progress_message_template", "收到，正在为你{action}，请稍候..."
        )

        # Phase B: 鉴权依赖
        if self._sop_intent_registry is not None:
            injected["sop_intent_registry"] = self._sop_intent_registry

        # Phase C: Guardian 服务
        if self._guardian_review is not None:
            injected["guardian_review"] = self._guardian_review
        if self._guardian_agent_factory is not None:
            injected["guardian_agent_factory"] = self._guardian_agent_factory

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
        """处理一条标准化入站消息（Session 主线）。"""
        decision: RouteDecision = self.takeover_service.decide(message)
        if not decision.takeover:
            return None

        logger.info(
            "handle_message takeover=true mode=%s sender=%s",
            decision.mode, message.sender_name,
        )

        self._ensure_initialized()

        # 用户自动绑定
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

        # SessionPool 路由
        reply = await self._session_pool.route(message, user_id=user_id)

        # 出站
        if reply is not None:
            self.outbound_bus.publish("reply", {
                "conversation_id": reply.conversation_id,
                "content": reply.content,
                "reply_to_message_id": reply.reply_to_message_id,
            })
        return reply

    async def terminate_session(self, conversation_id: str) -> bool:
        """强制终止指定 Session。"""
        self._ensure_initialized()
        ok = await self._session_pool.terminate(conversation_id)
        if ok:
            self.outbound_bus.publish("session_closed", {"conversation_id": conversation_id})
        return ok

    def health(self) -> dict:
        """健康状态。"""
        pool = self._session_pool
        return {
            "status": "ok",
            "initialized": self._initialized,
            "sessions": pool.size if pool else 0,
            "uptime": pool.uptime_seconds if pool else 0,
            "bus_hooks": self._bus.hook_count() if self._bus else 0,
        }
