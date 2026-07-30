"""Emily Core —— 业务内核入口（Session 主线架构）。

蓝图 §1.2 逻辑分层：
    通信层(薄插件) → Adapter(Session 池) → Session(SessionAgent) → WorkItem(Pipeline BUS) → 基础设施

EmilyCore 是独立容器内的业务内核，不依赖任何 AstrBot 对象。它：
  · 延迟初始化基础设施（DB / LLM / RAG / 仓库 / 服务 / 工具）
  · 构建系统级公共 Pipeline BUS（全局单例 WorkItem-Agent + 声明式 Hook）
  · 构建 SessionPoolManager（消息路由 + Session 生命周期）
  · 暴露 handle_message 作为统一入站入口
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

from .adapters.standard.message import StandardMessage
from .adapters.standard.reply import ReplyMessage
from .adapters.standard.route_decision import RouteDecision
from .config import Config
from .infrastructure.paths import resolve_data_path
from .outbound_bus import OutboundEventBus
from .services.domain_takeover_service import DomainTakeoverService
from .services.user_binding_service import UserBindingService

logger = logging.getLogger("emily.core")


class EmilyCore:
    """Emily 业务内核 —— Session 主线编排。"""

    def __init__(self, config: Config, rag_provider=None):
        self.config = config
        self.takeover_service = DomainTakeoverService(config)
        # BUG-002: 传入 auto_create 配置给 UserBindingService
        self.user_binding_service = UserBindingService(
            auto_create=getattr(config, "auto_create_user", True),
            whitelist=getattr(config, "auto_create_whitelist", []) or [],
        )

        # 出站事件总线
        self.outbound_bus = OutboundEventBus()

        # RAG 提供者
        self._rag_provider = rag_provider

        # 延迟初始化的子系统
        self._llm_client = None
        self._initialized = False

        # 邮箱服务
        self._email_service = None

        # WorkItem 执行引擎（LangGraph StateGraph，统一生命周期图 + L3 agent loop）
        self._workitem_agent = None
        self._workitem_graph = None
        self._hook_adapter = None
        self._resolvers = None

        # Session 池
        self._session_pool = None

        # 共享基础设施

        # 执行依赖
        self._business_flow_tools = None

        # ToolManager 聚合层
        self._tool_manager = None

        # ScriptManager 聚合层（开发者/维护脚本）
        self._script_registry = None
        self._script_manager = None

        # 系统调度器
        self._scheduler_service = None
        self._scheduler_engine = None
        self._scheduler_app = None
        self._scheduler_handler_registry = None
        self._scheduler_hook_registry = None

        # 权限管理模块（Permission Module，v2.0）
        self._permission_repo = None
        self._permission_grant_repo = None
        self._permission_service = None
        self._permission_cache = None
        self._permission_auth_engine = None
        self._permission_audit_repo = None
        self._permission_app = None

        # 全景节点图 V2
        self._node_service = None
        self._node_event_bus = None
        self._node_app = None

        # 项目日记 + 长期记忆 + 待解决问题（文件级持久化）
        self._event_journal = None
        self._user_memory_service = None
        self._pending_issues_service = None

        # Session 归档 md 文件
        self._session_archive_writer = None

        # Application 层实例（供工具 handler 注入）
        self._event_app = None
        self._task_app = None
        self._meeting_app = None
        self._file_app = None
        self._file_manager = None  # M1: FileManager 统一入口
        self._attachment_downloader = None  # M3: 附件异步下载
        self._query_service = None

        # Skill 模块
        self._skill_registry = None
        self._skill_executor = None

        # 监控模块（Monitor Dashboard）
        self._monitor_service = None

        # Agent 追踪查询服务（trace API）
        self._agent_trace_service = None

        # 聊天归档服务（chat_archive 工具）
        self._chat_archive_service = None

        # 群列表注册服务
        self._group_registry_service = None

        # 元认知模块
        self._rule_book_loader = None
        self._world_book_service = None
        self._system_description_service = None

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
                    router_model=getattr(self.config, "llm_router_model", "") or self.config.llm_model,
                    guardian_model=getattr(self.config, "llm_guardian_model", "") or self.config.llm_model,
                    agent_loop_model=getattr(self.config, "llm_agent_loop_model", "") or "",
                )
                # ── 进化日志：接入 LLM trace callback ──
                try:
                    from .infrastructure.logging.llm_logger import LLMInteractionLogger
                    self._llm_client.set_trace_callback(LLMInteractionLogger.make_callback())
                    logger.info("LLM trace callback connected to LLMInteractionLogger")
                except Exception as cb_err:
                    logger.warning("Failed to connect LLM trace callback: %s", cb_err)
                logger.info("LLM client initialized: model=%s", self.config.llm_model)
            except Exception as e:
                logger.error("LLM client init failed: %s", e)
                self._llm_client = None
        else:
            logger.info("No LLM API key — LLM-dependent features disabled (fallback steps will be used)")

        # ── Email 模块（SMTP + IMAP Providers + EmailService）──
        self._init_email_module()

        #  ── 执行 + 守护依赖 ──
        self._init_phase_c_deps()

        #  ── 全景节点图 V2（须先于调度器：PeriodicNodeHandler 注册时注入 NodeService）──
        self._init_node_module()

        #  ── 系统调度器 ──
        self._init_scheduler_module()

        #  ── 权限管理模块（v2.0：快照灌注）──
        self._init_permission_module()

        #  ── 项目日记 + 长期记忆 ──
        self._init_m8c_services()

        # ── Skill 模块 ──
        self._init_skill_module()

        #  ── 监控模块（Monitor Dashboard）──
        self._init_monitor_module()

        # ── Agent 追踪查询服务（D1：接线，供 trace API 查询）──
        try:
            from .services.agent_trace_service import AgentTraceService
            self._agent_trace_service = AgentTraceService()
            logger.info("AgentTraceService initialized (trace query ready)")
        except Exception as e:
            logger.warning("AgentTraceService init failed: %s", e)
            self._agent_trace_service = None

        # ── 聊天归档服务（D2：接线，激活 chat_archive 工具）──
        try:
            from .services.chat_archive_service import ChatArchiveService
            self._chat_archive_service = ChatArchiveService()
            logger.info("ChatArchiveService initialized (chat_archive tool ready)")
        except Exception as e:
            logger.warning("ChatArchiveService init failed: %s", e)
            self._chat_archive_service = None

        # ── 群列表注册服务 ──
        try:
            from .services.group_registry_service import GroupRegistryService
            self._group_registry_service = GroupRegistryService()
            logger.info("GroupRegistryService initialized")
        except Exception as e:
            logger.warning("GroupRegistryService init failed: %s", e)
            self._group_registry_service = None

        # ── 注入 trace 服务到 API 路由（lazy fallback 也能工作，直接注入更可靠）──
        try:
            from api.routes.trace import set_trace_service
            set_trace_service(self._agent_trace_service)
        except ImportError:
            pass  # 非 API 运行场景

        # ── 元认知模块 ──
        self._init_meta_cognition()

        # ── 统一工具注册（在全部子系统和 Application 就绪后，一次性注册）──
        if self._business_flow_tools is not None:
            from .tools.registry import register_all
            register_all(self)

            # ── ToolManager 聚合层 ──
            from .tools.manager import ToolManager
            self._tool_manager = ToolManager(self._business_flow_tools)
            logger.info("tool_manager: ready (%d tools)", len(self._business_flow_tools))

            # ── ScriptManager 聚合层 ──
            from .scripts.registry import load_registry
            from .scripts.manager import ScriptManager
            self._script_registry = load_registry()
            self._script_manager = ScriptManager(self._script_registry)
            logger.info("script_manager: ready (%d scripts)", len(self._script_registry))

        # ── 公共 Pipeline BUS ──
        self._build_pipeline_bus()

        # ── Session 池 ──
        self._build_session_pool()

        self._initialized = True
        hook_count = self._hook_adapter._registry.hook_count() if self._hook_adapter else 0
        logger.info(
            "EmilyCore initialized: hooks=%d, engine=langgraph, session_pool ready",
            hook_count,
        )

    def _init_email_module(self) -> None:
        """初始化邮箱模块：Provider + Service。fail-open，不阻塞 Core。"""
        try:
            from .providers.email import SMTPEmailProvider, IMAPEmailProvider
            from .services.email_service import EmailService

            smtp = SMTPEmailProvider()
            imap = IMAPEmailProvider()
            self._email_service = EmailService(smtp=smtp, imap=imap)

            logger.info("Email module initialized: SMTP + IMAP ready")
        except Exception as e:
            logger.warning("Email module init failed: %s", e)
            self._email_service = None

    def _init_skill_module(self) -> None:
        """初始化 Skill 模块：SOP .md 索引器（降级后）。fail-open。"""
        try:
            from .skill.registry import SkillRegistry

            # 使用统一路径解析：config → 容器 → 开发回退
            skill_dir = resolve_data_path(
                getattr(self.config, "skill_directory", "") or "",
                "/app/skills",
                "emily-data/skills",
            )

            self._skill_registry = SkillRegistry(skill_directory=skill_dir)
            status = self._skill_registry.load()
            self._skill_executor = None  # SkillExecutor 已删除（L3 agent loop 迁移）
            logger.info("Skill module initialized (SOP indexer): %s — dir=%s", status, skill_dir)

            # 将 SkillRegistry 注入 PermissionService（使 sop_allow fallback 生效）
            if self._permission_service is not None:
                self._permission_service._skill_registry = self._skill_registry
                logger.info("SkillRegistry injected into PermissionService")
        except Exception as e:
            logger.warning("Skill module init failed: %s", e)
            self._skill_registry = None
            self._skill_executor = None

    def reload_skills(self) -> dict:
        """热重载 Skill 注册表（无需重启容器）。

        适用场景：sop_to_skill.py 转换新 Skill 后，调用此方法使运行中的
        EmilyCore 感知新的 .skill.yaml 文件。也可通过 API 触发：
          POST /api/v1/skills/reload

        Returns:
            {"ok": bool, "total": int, "skill_ids": list[str]}
        """
        if self._skill_registry is None:
            return {"ok": False, "total": 0, "skill_ids": [], "error": "SkillRegistry not initialized"}

        try:
            status = self._skill_registry.reload()
            skill_ids = self._skill_registry.list_sop_ids()

            # 同步注入 PermissionService
            if self._permission_service is not None:
                self._permission_service._skill_registry = self._skill_registry

            logger.info(
                "SkillRegistry reloaded: %d skills (%d ok, %d failed)",
                len(skill_ids), status.successfully_parsed, status.failed_parsed,
            )
            return {
                "ok": True,
                "total": len(skill_ids),
                "skill_ids": skill_ids,
                "successfully_parsed": status.successfully_parsed,
                "failed_parsed": status.failed_parsed,
                "failed_files": status.failed_files,
            }
        except Exception as e:
            logger.error("SkillRegistry reload failed: %s", e)
            return {"ok": False, "total": 0, "skill_ids": [], "error": str(e)}

    def _init_monitor_module(self) -> None:
        """初始化监控模块：MonitorService。fail-open。"""
        try:
            from .services.monitor_service import MonitorService
            self._monitor_service = MonitorService(core=self)

            # 注入到 API 路由
            try:
                from api.routes.monitor import set_monitor_service
                set_monitor_service(self._monitor_service)
            except ImportError:
                pass  # 非 API 场景

            logger.info("Monitor module initialized")
        except Exception as e:
            logger.warning("Monitor module init failed: %s", e)
            self._monitor_service = None

    def _init_meta_cognition(self) -> None:
        """初始化元认知模块：规则书 + 世界书 + 系统描述。fail-open。"""
        try:
            from .services.rule_book_loader import RuleBookLoader
            from .services.world_book_service import ProjectWorldBookService
            from .services.system_description_service import SystemDescriptionService

            # 规则书加载
            self._rule_book_loader = RuleBookLoader()
            self._rule_book_loader.load()

            # 世界书服务
            self._world_book_service = ProjectWorldBookService(llm_client=self._llm_client)

            # 系统描述服务（启动时自动检测偏差并重建）
            self._system_description_service = SystemDescriptionService(llm_client=self._llm_client)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 在已有事件循环中，调度异步任务
                    asyncio.ensure_future(self._system_description_service.check_and_update())
                else:
                    loop.run_until_complete(self._system_description_service.check_and_update())
            except RuntimeError:
                # 无事件循环，同步调用
                asyncio.run(self._system_description_service.check_and_update())

            logger.info("Meta-cognition module initialized: rule_book=%s, world_book=ready, system_description=ready",
                         "loaded" if self._rule_book_loader.is_loaded else "empty")
        except Exception as e:
            logger.warning("Meta-cognition module init failed: %s", e)
            self._rule_book_loader = None
            self._world_book_service = None
            self._system_description_service = None

    def reload_rule_book(self) -> dict:
        """热重载规则书（无需重启容器）。

        Returns:
            {"ok": bool, "content_length": int, "changed": bool}
        """
        if self._rule_book_loader is None:
            return {"ok": False, "error": "RuleBookLoader not initialized"}
        return self._rule_book_loader.reload()

    def _init_phase_c_deps(self) -> None:
        """初始化执行引擎 + 守护审核依赖 + Application 层。"""
        try:
            from .tools.business_flow_tools import BusinessFlowToolRegistry

            self._business_flow_tools = BusinessFlowToolRegistry()

            # ── 创建 Application 实例（供工具 registry 注入）──
            from .application import EventApplication
            from .application import TaskApplication
            from .application import MeetingApplication
            from .application import FileApplication
            from .services import EventService, TaskService, MeetingService, FileService, QueryService
            from .services.file_storage_service import FileStorageService

            self._event_app = EventApplication(EventService())
            self._task_app = TaskApplication(TaskService())
            self._meeting_app = MeetingApplication(MeetingService())

            # 创建 FileStorageService 并注入 FileApplication
            storage_root = resolve_data_path(
                self.config.storage_root or "",
                "/app/attachments",
                "emily-data/attachments",
            )
            file_storage = FileStorageService(storage_root=storage_root)
            self._file_app = FileApplication(FileService(), storage_service=file_storage)
            self._query_service = QueryService()

            # M1: 创建 FileManager 统一入口并注入
            from .repositories.session_accessible_file_repo import SessionAccessibleFileRepo
            from .services.file_manager import FileManager
            file_manager = FileManager(
                file_service=self._file_app.file_service,
                storage_service=file_storage,
                accessible_repo=SessionAccessibleFileRepo(),
            )
            self._file_app.set_file_manager(file_manager)
            self._file_manager = file_manager
            logger.info("FileManager initialized and injected into FileApplication")

            # M3: 创建 AttachmentDownloader
            from .services.attachment_downloader import AttachmentDownloader
            self._attachment_downloader = AttachmentDownloader(file_manager)
            logger.info("AttachmentDownloader initialized")

            # 注入项目日记到 Application 层（如果 journal 先于此处初始化则注入）
            if self._event_journal is not None:
                self._event_app.set_journal(self._event_journal)
                self._task_app.set_journal(self._event_journal)
                self._meeting_app.set_journal(self._event_journal)
                self._file_app.set_journal(self._event_journal)
                self._query_service.set_journal(self._event_journal)
                logger.info("EventJournal injected into 4 apps + query_service")

        except Exception as e:
            logger.warning("Execution engine init failed: %s", e)
            self._business_flow_tools = None

    def _build_pipeline_bus(self) -> None:
        """构建统一生命周期 LangGraph 引擎（L3 agent loop）。

        旧 5 节点图 + WorkItemAgent 4 节点 handler 已移除（大爆炸切换）。
        新图：created→routing→executing(agent loop)→summarizing→done/failed。
        """
        from .workitem.langgraph_engine.graph import build_workitem_graph
        from .workitem.langgraph_engine.hook_adapter import build_hook_adapter_from_config
        from .workitem.langgraph_engine.agent.resolver import build_default_resolvers

        hook_cfg = self._load_hook_config() or {"hooks": {}}
        injected = self._collect_injected_services()
        self._hook_adapter = build_hook_adapter_from_config(hook_cfg, injected)

        self._resolvers = build_default_resolvers()

        self._workitem_graph = build_workitem_graph(
            hook_adapter=self._hook_adapter,
            llm_client=self._llm_client,
            business_tools=self._business_flow_tools,
            resolvers=self._resolvers,
            config=self.config,
            max_iterations=getattr(self.config, "agent_loop_max_iterations", 12),
        )
        logger.info(
            "Unified lifecycle graph built: agent loop, max_iterations=%d, checkpointer=MemorySaver",
            getattr(self.config, "agent_loop_max_iterations", 12),
        )

    def _build_session_pool(self) -> None:
        """构建 Session 池（蓝图 §3.4）。"""
        from .adapters.session import SessionPoolManager, SessionConfig, SessionFactory

        session_config = SessionConfig.from_config(self.config)
        factory = SessionFactory(core=self)
        self._session_pool = SessionPoolManager(
            config=session_config,
            factory=factory,
            core=self,
        )

    def _init_scheduler_module(self) -> None:
        """初始化系统调度器模块：Service + Engine + Handler + Application。"""
        try:
            import asyncio
            from .scheduler.service import SchedulerService
            from .scheduler.engine import SchedulerEngine
            from .scheduler.handler_registry import JobHandlerRegistry
            from .scheduler.hook_registry import SchedulerHookRegistry
            from .scheduler.application import SchedulerApplication

            # 注册表
            self._scheduler_handler_registry = JobHandlerRegistry()
            self._scheduler_hook_registry = SchedulerHookRegistry()

            # Service
            self._scheduler_service = SchedulerService()

            # 注册内置 Handler
            from .scheduler.jobs.morning_report import MorningReportHandler
            from .scheduler.jobs.node_deadlines import NodeDeadlineHandler
            from .scheduler.jobs.periodic_node import PeriodicNodeHandler
            from .scheduler.jobs.session_cleanup import SessionCleanupHandler
            from .scheduler.jobs.health_check import HealthCheckHandler
            from .scheduler.jobs.data_sync import DataSyncHandler
            from .scheduler.jobs.webhook import WebhookHandler

            self._scheduler_handler_registry.register(
                MorningReportHandler(
                    outbound_bus=self.outbound_bus,
                    llm_client=self._llm_client,
                )
            )
            self._scheduler_handler_registry.register(
                NodeDeadlineHandler(
                    node_service=self._node_service,
                    outbound_bus=self.outbound_bus,
                )
            )
            self._scheduler_handler_registry.register(
                PeriodicNodeHandler(
                    node_service=self._node_service,
                )
            )
            self._scheduler_handler_registry.register(
                SessionCleanupHandler(
                    session_pool=self._session_pool,
                    outbound_bus=self.outbound_bus,
                )
            )
            self._scheduler_handler_registry.register(
                HealthCheckHandler(
                    outbound_bus=self.outbound_bus,
                )
            )
            self._scheduler_handler_registry.register(DataSyncHandler())
            self._scheduler_handler_registry.register(WebhookHandler())

            # 每日文件解析盘点 Handler
            from .scheduler.jobs.daily_file_parse import DailyFileParseHandler
            _file_svc = self._file_app.file_service if self._file_app else None
            self._scheduler_handler_registry.register(
                DailyFileParseHandler(
                    file_service=_file_svc,
                    outbound_bus=self.outbound_bus,
                )
            )

            # 元认知 Handler
            from .scheduler.jobs.world_book_update import WorldBookUpdateHandler
            self._scheduler_handler_registry.register(
                WorldBookUpdateHandler(world_book_service=self._world_book_service)
            )

            # 系统描述更新 Handler（周级）
            from .scheduler.jobs.system_description_update import SystemDescriptionUpdateHandler
            self._scheduler_handler_registry.register(
                SystemDescriptionUpdateHandler()
            )

            # 进化闭环 Handler
            from .scheduler.jobs.daily_insight import DailyInsightHandler
            from .scheduler.jobs.rule_induction import RuleInductionHandler
            from .scheduler.jobs.patch_validator import PatchValidationHandler

            self._scheduler_handler_registry.register(
                DailyInsightHandler(llm_client=self._llm_client)
            )
            self._scheduler_handler_registry.register(
                RuleInductionHandler(llm_client=self._llm_client)
            )
            self._scheduler_handler_registry.register(
                PatchValidationHandler(llm_client=self._llm_client)
            )

            # Engine
            self._scheduler_engine = SchedulerEngine(
                service=self._scheduler_service,
                handler_registry=self._scheduler_handler_registry,
                hook_registry=self._scheduler_hook_registry,
                config=self.config,
                outbound_bus=self.outbound_bus,
            )

            # Application
            self._scheduler_app = SchedulerApplication(
                service=self._scheduler_service,
                engine=self._scheduler_engine,
            )

            # 启动引擎 tick 循环
            asyncio.ensure_future(self._scheduler_engine.start())

            logger.info("Scheduler module initialized: %d handlers, %d hooks",
                         len(self._scheduler_handler_registry),
                         self._scheduler_hook_registry.hook_count())
        except Exception as e:
            logger.warning("Scheduler module init failed: %s", e)
            self._scheduler_engine = None
            self._scheduler_app = None

    def _init_permission_module(self) -> None:
        """初始化权限管理模块：三维鉴权 + 校验接口。

        PermissionService 在 SessionFactory._build_context() 中被调用，
        组装 PermissionSnapshot 注入 SessionContext。
        PermissionCache + PermissionAuthEngine + PermissionAuditLogRepository +
        PermissionApplication + API 路由注册。
        fail-open：初始化失败不阻塞 Core。
        """
        try:
            from .repositories.permission_repo import PermissionRepository
            from .repositories.permission_grant_repo import PermissionGrantRepository
            from .services.permission_service import PermissionService
            from .permission.cache import PermissionCache
            from .permission.auth_engine import PermissionAuthEngine
            from .permission.row_security import (
                PermissionAuditLogRepository,
                register_row_security_listener,
            )
            from .application.permission_app import PermissionApplication

            fail_open = getattr(self.config, "permission_fail_open", True)
            cache_ttl = getattr(self.config, "permission_cache_ttl_seconds", 300)

            # Repository
            self._permission_repo = PermissionRepository()
            self._permission_grant_repo = PermissionGrantRepository()
            self._permission_audit_repo = PermissionAuditLogRepository()

            # L1/L2 缓存
            self._permission_cache = PermissionCache(ttl_seconds=cache_ttl)

            # 三维鉴权引擎
            self._permission_auth_engine = PermissionAuthEngine(
                cache=self._permission_cache,
                audit_repo=self._permission_audit_repo,
            )

            # Service（注入缓存 + 引擎 + 审计）
            self._permission_service = PermissionService(
                repo=self._permission_repo,
                grant_repo=self._permission_grant_repo,
                fail_open=fail_open,
                cache=self._permission_cache,
                auth_engine=self._permission_auth_engine,
                audit_repo=self._permission_audit_repo,
            )

            # Application 编排层
            self._permission_app = PermissionApplication(
                service=self._permission_service,
            )

            # API 路由注册
            try:
                from emily_core.api.routes.permission import set_permission_app
                set_permission_app(self._permission_app)
            except Exception as e:
                logger.debug("permission route registration skipped: %s", e, exc_info=True)

            # 注册行级安全拦截器
            try:
                register_row_security_listener()
            except Exception as e:
                logger.warning("Row security listener registration failed: %s", e)

            logger.info(
                "Permission module initialized: service + cache + auth_engine + app + row_security"
            )
        except Exception as e:
            logger.warning("Permission module init failed: %s", e)
            self._permission_repo = None
            self._permission_grant_repo = None
            self._permission_service = None
            self._permission_cache = None
            self._permission_auth_engine = None
            self._permission_audit_repo = None
            self._permission_app = None

    def _init_node_module(self) -> None:
        """初始化全景节点图 V2 模块（Service + EventBus + Application + 工具注册）。"""
        try:
            from .services.node_service import NodeService
            from .node_event_bus import NodeEventBus
            from .application.node_app import NodeApplication

            from .repositories.permission_repo import PermissionRepository

            self._node_service = NodeService(user_repo=PermissionRepository())
            self._node_event_bus = NodeEventBus()
            self._node_event_bus.set_outbound_bus(self.outbound_bus)
            self._node_app = NodeApplication(self._node_service)

            # 注入到 API 路由
            try:
                from api.routes.node import set_node_service
                set_node_service(self._node_service)
            except ImportError:
                pass  # 非 API 场景（如脚本直接调用 EmilyCore）
            try:
                from api.sse.node_events import set_node_event_bus
                set_node_event_bus(self._node_event_bus)
            except ImportError:
                pass

            # 注册节点工具到 BusinessFlowToolRegistry
            if self._business_flow_tools is not None:
                self._register_node_tools()

            logger.info("Node graph V2 module initialized")
        except Exception:
            logger.warning("Node graph V2 module initialization failed", exc_info=True)

    # ── 项目日记 + 长期记忆 ──

    def _init_m8c_services(self) -> None:
        """初始化文件级持久化服务：EventJournal + UserMemoryService。

        EventJournal：项目事件流水日志，记录所有系统事件的摘要行。
        通过 set_journal() 注入到 event_app / task_app / meeting_app / file_app /
        query_service，在业务操作完成后自动追加。

        UserMemoryService：用户长期工作记忆，Agent 通过 write_user_memory 工具
        写入用户的长期需求。Session 创建时加载为上下文字段。
        """
        # ── 1. EventJournal（项目日记）──
        try:
            from .services.event_journal import EventJournal
            journal_path = resolve_data_path(
                self.config.journal_path or "",
                "/app/journal/项目日志.md",
                "emily-data/journal/项目日志.md",
            )
            journal = EventJournal(
                path=journal_path,
                enabled=self.config.journal_enabled,
            )
            self._event_journal = journal
            logger.info("EventJournal initialized — path=%s enabled=%s",
                         journal_path, journal.enabled)

            # 注入到已创建的 Application 实例
            if self._event_app is not None:
                self._event_app.set_journal(journal)
                self._task_app.set_journal(journal)
                self._meeting_app.set_journal(journal)
                self._file_app.set_journal(journal)
                self._query_service.set_journal(journal)
                logger.info("EventJournal injected into 4 apps + query_service")
        except Exception as e:
            logger.warning("EventJournal init failed: %s", e)
            self._event_journal = None

        # ── 2. UserMemoryService（长期记忆）──
        try:
            from .services.user_memory_service import UserMemoryService
            memory_dir = resolve_data_path(
                self.config.user_memory_dir or "",
                "/app/user_memory",
                "emily-data/user_memory",
            )
            self._user_memory_service = UserMemoryService(
                memory_dir=memory_dir,
                enabled=self.config.user_memory_enabled,
                max_entries=self.config.user_memory_max_entries,
            )
            logger.info("UserMemoryService initialized — dir=%s enabled=%s",
                         memory_dir, self._user_memory_service.enabled)

            # write_user_memory 工具注册已移至 register_all() → _register_business()
            # 此处不再重复注册，避免 user_name 参数在初始化时固定为空
        except Exception as e:
            logger.warning("UserMemoryService init failed: %s", e)
            self._user_memory_service = None

        # ── 3. PendingIssuesService（待解决问题清单 / notebooks 目录）──
        try:
            from .services.pending_issues import PendingIssuesService
            issues_path = resolve_data_path(
                self.config.pending_issues_path or "",
                "/app/notebooks/待解决问题.md",
                "emily-data/notebooks/待解决问题.md",
            )
            self._pending_issues_service = PendingIssuesService(issues_path=issues_path)
            self._pending_issues_service._ensure_file()  # 确保文件存在
            logger.info("PendingIssuesService initialized — path=%s", issues_path)
        except Exception as e:
            logger.warning("PendingIssuesService init failed: %s", e)
            self._pending_issues_service = None

        # ── 4. SessionArchiveWriter（会话归档 md 文件实时追加）──
        try:
            from .services.session_archive_writer import SessionArchiveWriter
            archive_dir = resolve_data_path(
                self.config.session_archive_dir or "",
                "/app/session_archives",
                "emily-data/session_archives",
            )
            self._session_archive_writer = SessionArchiveWriter(
                archive_dir=archive_dir,
                enabled=self.config.session_archive_enabled,
            )
            logger.info("SessionArchiveWriter initialized — dir=%s enabled=%s",
                         archive_dir, self._session_archive_writer.enabled)
        except Exception as e:
            logger.warning("SessionArchiveWriter init failed: %s", e)
            self._session_archive_writer = None

    def _register_node_tools(self) -> None:
        """将全景节点工具注册到 BusinessFlowToolRegistry。"""
        try:
            from .tools.node_tool import (
                handle_create_node,
                handle_query_node,
                handle_update_node_progress,
                handle_add_node_dependency,
                handle_mount_child_node,
                handle_update_nodes,
                handle_activate_nodes,
                handle_discard_nodes,
                _CREATE_NODE_SCHEMA,
                _CREATE_NODE_DESCRIPTION,
                _QUERY_NODE_SCHEMA,
                _QUERY_NODE_DESCRIPTION,
                _UPDATE_PROGRESS_SCHEMA,
                _UPDATE_PROGRESS_DESCRIPTION,
                _ADD_DEPENDENCY_SCHEMA,
                _ADD_DEPENDENCY_DESCRIPTION,
                _MOUNT_CHILD_SCHEMA,
                _MOUNT_CHILD_DESCRIPTION,
                _UPDATE_NODES_SCHEMA,
                _UPDATE_NODES_DESCRIPTION,
                _ACTIVATE_NODES_SCHEMA,
                _ACTIVATE_NODES_DESCRIPTION,
                _DISCARD_NODES_SCHEMA,
                _DISCARD_NODES_DESCRIPTION,
            )
            from .tools.business_flow_tools import BusinessFlowTool

            for name, desc, schema, handler in [
                ("create_node", _CREATE_NODE_DESCRIPTION, _CREATE_NODE_SCHEMA, handle_create_node),
                ("query_node", _QUERY_NODE_DESCRIPTION, _QUERY_NODE_SCHEMA, handle_query_node),
                ("update_node_progress", _UPDATE_PROGRESS_DESCRIPTION, _UPDATE_PROGRESS_SCHEMA, handle_update_node_progress),
                ("add_node_dependency", _ADD_DEPENDENCY_DESCRIPTION, _ADD_DEPENDENCY_SCHEMA, handle_add_node_dependency),
                ("mount_child_node", _MOUNT_CHILD_DESCRIPTION, _MOUNT_CHILD_SCHEMA, handle_mount_child_node),
                ("update_nodes", _UPDATE_NODES_DESCRIPTION, _UPDATE_NODES_SCHEMA, handle_update_nodes),
                ("activate_nodes", _ACTIVATE_NODES_DESCRIPTION, _ACTIVATE_NODES_SCHEMA, handle_activate_nodes),
                ("discard_nodes", _DISCARD_NODES_DESCRIPTION, _DISCARD_NODES_SCHEMA, handle_discard_nodes),
            ]:
                self._business_flow_tools.register(BusinessFlowTool(
                    name=name,
                    description=desc,
                    parameters=schema,
                    handler=handler,
                ))
            logger.info("Node tools registered to BusinessFlowToolRegistry: 8 tools")
        except Exception as e:
            logger.warning("Node tool registration failed: %s", e)

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
        """收集可注入到 Hook 的全量服务。"""
        injected: dict = {}

        # 前导消息发送器
        def _progress_sender(text: str):
            self.outbound_bus.publish("progress", {"content": text})
        injected["progress_sender"] = _progress_sender
        injected["progress_template"] = getattr(
            self.config, "progress_message_template", "收到，正在为你{action}，请稍候..."
        )

        # 邮箱模块：供 LLM Tool 使用
        if self._email_service is not None:
            injected["email_service"] = self._email_service

        # Skill 模块
        if self._skill_registry is not None:
            injected["skill_registry"] = self._skill_registry

        # Session 归档 writer（供 ArchiveHook 逐段追加归档）
        if self._session_archive_writer is not None:
            injected["archive_writer"] = self._session_archive_writer

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

        # 用户解析：BUG-001 修复 — 增加 UUID 直查路径
        user_id = ""
        try:
            # ① 如果 sender_id 看起来像 UUID，先直接查 users 表
            if self._looks_like_uuid(message.sender_id):
                from .repositories.user_repo import UserRepository
                direct_user = UserRepository.get_by_id(message.sender_id)
                if direct_user:
                    user_id = direct_user.id
                    logger.debug(
                        "handle_message: sender_id resolved as UUID -> user %s (%s)",
                        user_id, direct_user.username,
                    )

            # ② 回退到 IM 绑定解析（含 BUG-002 门禁）
            if not user_id:
                user, _is_new = self.user_binding_service.get_or_create_user(
                    im_platform=message.platform,
                    im_user_id=message.sender_id,
                    im_display_name=message.sender_name,
                )
                user_id = user.id if user else ""
        except Exception as e:
            # UserNotAllowedError 不应吞掉——记录但继续（返回无用户回复）
            logger.warning("user binding failed (continuing): %s", e)

        # ── 入站消息持久化（M1 修复：恢复落库，供 trace 关联）──
        db_message_id = ""
        try:
            from .services.message_service import MessageService
            _msg_service = MessageService()
            # event_id 防御：空串时生成 fallback，避免 unique 约束冲突
            _event_id = event_id or f"fallback_{uuid.uuid4().hex[:12]}"
            db_msg = await asyncio.to_thread(
                _msg_service.record_message, _event_id, message, decision
            )
            db_message_id = db_msg.id
            # 回填 sender_user_id（用户绑定已在上面完成）
            if user_id:
                await asyncio.to_thread(_msg_service.bind_sender, db_message_id, user_id)
            logger.info(
                "Inbound message persisted: id=%s event_id=%s conv=%s",
                db_message_id, _event_id, message.conversation_id,
            )
        except Exception as e:
            # 非阻断：持久化失败不阻塞 Pipeline，仅 trace 会缺失
            logger.warning("Inbound message persist failed (non-blocking): %s", e)

        # ── M3: 附件自动下载（异步，不阻塞主线）──
        if db_message_id and self._attachment_downloader is not None:
            _attachments = getattr(message, "attachments", None) or []
            if _attachments:
                asyncio.create_task(
                    self._attachment_downloader.download_for_message(
                        message_id=db_message_id, attachments=_attachments,
                    )
                )
                logger.debug(
                    "Scheduled attachment download: msg=%s, %d item(s)",
                    db_message_id, len(_attachments),
                )

        # ── 静默收集：仅归档不响应，跳过流水线 ──
        if not decision.should_reply:
            logger.info(
                "Silent collect: msg persisted conv=%s sender=%s",
                message.conversation_id, message.sender_name,
            )
            return None

        # SessionPool 路由（携带 db_message_id —— 见 M2）
        reply = await self._session_pool.route(message, user_id=user_id, db_message_id=db_message_id)

        # 出站
        if reply is not None:
            self.outbound_bus.publish("reply", {
                "conversation_id": reply.conversation_id,
                "content": reply.content,
                "reply_to_message_id": reply.reply_to_message_id,
            })
            # 持久化 agent 回复到 messages 表（非阻断，fail-open）
            if self._chat_archive_service is not None:
                try:
                    await asyncio.to_thread(
                        self._chat_archive_service.record_outbound_reply,
                        conversation_id=message.conversation_id,
                        content=reply.content,
                        reply_to_message_id=reply.reply_to_message_id,
                    )
                    logger.info(
                        "Outbound reply persisted: conv=%s len=%d",
                        message.conversation_id, len(reply.content),
                    )
                except Exception as e:
                    logger.warning("Outbound reply persist failed (non-blocking): %s", e)
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
        result = {
            "status": "ok",
            "initialized": self._initialized,
            "sessions": pool.size if pool else 0,
            "uptime": pool.uptime_seconds if pool else 0,
            "langgraph_engine": self._graph is not None if hasattr(self, '_graph') else False,
        }
        return result

    # ── 辅助方法 ──

    @staticmethod
    def _looks_like_uuid(value: str) -> bool:
        """判断 sender_id 是否看起来像 UUID（含连字符的 8-4-4 格式）。

        用于 BUG-001 修复：emy-test 等工具传入 users 表 UUID 作为 sender_id，
        需优先走 UUID 直查路径而非 IM 绑定查找。
        """
        if not value:
            return False
        # 标准 UUID 格式：xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx（36 字符）
        parts = value.split("-")
        if len(parts) == 5 and all(p.isalnum() for p in parts):
            return True
        # 无连字符 UUID（32 字符十六进制）
        if len(value) == 32 and value.isalnum():
            return True
        return False
