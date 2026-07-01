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

        # 邮箱服务
        self._email_service = None

        # 公共 Pipeline BUS + WorkItem-Agent
        self._bus = None
        self._workitem_agent = None

        # Session 池
        self._session_pool = None

        # Phase B/C: 共享基础设施
        self._sop_intent_registry = None

        # Phase C: 执行依赖
        self._business_flow_tools = None

        # 计划任务系统（Scheduled Task Module）
        self._plan_task_service = None
        self._plan_task_scheduler = None
        self._plan_task_app = None
        self._workflow_integrator = None

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

        # M8c: 项目日记 + 长期记忆（文件级持久化）
        self._event_journal = None
        self._user_memory_service = None

        # Application 层实例（供工具 handler 注入）
        self._event_app = None
        self._task_app = None
        self._meeting_app = None
        self._file_app = None
        self._query_service = None

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

        # ── Email 模块（SMTP + IMAP Providers + EmailService）──
        self._init_email_module()

        # ── Phase B: SOP 意图注册表 + 工具注册表 ──
        self._init_phase_b_deps()

        #  ── Phase C: 执行 + 守护依赖 ──
        self._init_phase_c_deps()

        #  ── 计划任务系统 ──
        self._init_plan_task_module()

        #  ── 权限管理模块（v2.0：快照灌注）──
        self._init_permission_module()

        #  ── 全景节点图 V2 ──
        self._init_node_module()

        #  ── M8c: 项目日记 + 长期记忆（必须在 Phase C 之后，依赖 Application 实例）──
        self._init_m8c_services()

        # 将 plan_task 工具注册到 BusinessFlowToolRegistry
        if self._plan_task_app is not None and self._business_flow_tools is not None:
            self._register_plan_task_tools()

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
        """Phase B: 初始化 SOP 意图注册表。"""
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

        # 2. ToolRegistry 已移除（M14 重构后走 BusinessFlowToolRegistry 直调）

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

            # ── 创建 Application 实例（供工具 handler 注入）──
            from .application import EventApplication
            from .application import TaskApplication
            from .application import MeetingApplication
            from .application import FileApplication
            from .services import EventService, TaskService, MeetingService, FileService, QueryService

            self._event_app = EventApplication(EventService())
            self._task_app = TaskApplication(TaskService())
            self._meeting_app = MeetingApplication(MeetingService())
            self._file_app = FileApplication(FileService())
            self._query_service = QueryService()

            # M8c: 注入项目日记到 Application 层
            if self._event_journal is not None:
                self._event_app.set_journal(self._event_journal)
                self._task_app.set_journal(self._event_journal)
                self._meeting_app.set_journal(self._event_journal)
                self._file_app.set_journal(self._event_journal)
                self._query_service.set_journal(self._event_journal)
                logger.info("M8c: EventJournal injected into 4 apps + query_service")

            # 用 partial + self._*_app 包装 handler
            from functools import partial

            _event_handler = partial(handle_record_event, event_app=self._event_app)
            _task_handler = partial(handle_record_task, task_app=self._task_app)
            _meeting_handler = partial(handle_record_meeting, meeting_app=self._meeting_app)
            _file_handler = partial(handle_record_file, file_app=self._file_app)
            _query_handler = partial(handle_query_data, query_service=self._query_service)

            self._business_flow_tools.register(BusinessFlowTool(
                name="record_event", description="记录项目事件",
                parameters={"type": "object", "properties": {}}, handler=_event_handler,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_task", description="创建任务",
                parameters={"type": "object", "properties": {}}, handler=_task_handler,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_meeting", description="归档会议纪要",
                parameters={"type": "object", "properties": {}}, handler=_meeting_handler,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="record_file", description="记录文件元数据",
                parameters={"type": "object", "properties": {}}, handler=_file_handler,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="query_data", description="查询项目数据",
                parameters={"type": "object", "properties": {}}, handler=_query_handler,
            ))

            # 基座能力：knowledge_search — RAG 知识库检索（供所有 SOP 调用）
            if self._rag_provider is not None:
                try:
                    from .tools.knowledge_search_tool import (
                        handle_knowledge_search,
                        _KNOWLEDGE_SEARCH_SCHEMA,
                        _KNOWLEDGE_SEARCH_DESCRIPTION,
                    )
                    _rp = self._rag_provider
                    async def _rag_handler(params, **kw):
                        return await handle_knowledge_search(params, rag_provider=_rp)
                    self._business_flow_tools.register(BusinessFlowTool(
                        name="knowledge_search",
                        description=_KNOWLEDGE_SEARCH_DESCRIPTION,
                        parameters=_KNOWLEDGE_SEARCH_SCHEMA,
                        handler=_rag_handler,
                    ))
                    logger.info("Phase C: knowledge_search registered to BusinessFlowToolRegistry")
                except Exception as e:
                    logger.warning("Phase C: knowledge_search registration failed: %s", e)

            logger.info("Phase C: BusinessFlowToolRegistry initialized with %d tools",
                         len(self._business_flow_tools))
        except Exception as e:
            logger.warning("Phase C: BusinessFlowToolRegistry init failed: %s", e)
            self._business_flow_tools = None

    def _build_pipeline_bus(self) -> None:
        """构建系统级公共 Pipeline BUS（蓝图 §5.4 + Phase C 升级）。"""
        from .workitem import WorkItemAgent, PipelineBUS, KnowledgeInjector

        injector = KnowledgeInjector(
            sop_intent_registry=self._sop_intent_registry,
            sop_loader=None,
        )
        self._workitem_agent = WorkItemAgent(
            injector=injector,
            llm_client=self._llm_client,
            config=self.config,
            # Phase C: 执行依赖
            business_flow_tools=self._business_flow_tools,
            sop_intent_registry=self._sop_intent_registry,
            rag_provider=self._rag_provider,
            # 阶段二：三维鉴权引擎
            permission_engine=self._permission_auth_engine,
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

    # ────────────────────────────────────────────────────────────────────
    # 计划任务系统（Scheduled Task Module）
    # ────────────────────────────────────────────────────────────────────

    def _register_plan_task_tools(self) -> None:
        """将 plan_task 工具注册到 BusinessFlowToolRegistry。"""
        try:
            from .tools.plan_task_tool import (
                handle_record_plan_task,
                handle_submit_plan_task,
                handle_review_plan_task,
                handle_query_plan_tasks,
                _RECORD_PLAN_TASK_SCHEMA,
                _SUBMIT_PLAN_TASK_SCHEMA,
                _REVIEW_PLAN_TASK_SCHEMA,
                _QUERY_PLAN_TASKS_SCHEMA,
            )
            from .tools.business_flow_tools import BusinessFlowTool

            app = self._plan_task_app
            cfg = self.config

            def _make_handler(fn, **extra):
                async def _handler(params, user_id="", message_id="", **kw):
                    return await fn(params, **extra, user_id=user_id, message_id=message_id)
                return _handler

            self._business_flow_tools.register(BusinessFlowTool(
                name="record_plan_task",
                description="创建计划任务（一次性或循环）。用于下达工作任务、布置周期性任务（日报/周报/月报等）。",
                parameters=_RECORD_PLAN_TASK_SCHEMA,
                handler=_make_handler(handle_record_plan_task,
                    plan_task_app=app, pending_issues=None, config=cfg),
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="submit_plan_task",
                description="提交计划任务成果。执行者在完成任务后提交成果。",
                parameters=_SUBMIT_PLAN_TASK_SCHEMA,
                handler=_make_handler(handle_submit_plan_task, plan_task_app=app),
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="review_plan_task",
                description="审核计划任务成果（确认完成或退回修改）。",
                parameters=_REVIEW_PLAN_TASK_SCHEMA,
                handler=_make_handler(handle_review_plan_task, plan_task_app=app),
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="query_plan_tasks",
                description="查询计划任务列表（按执行人或发起人、按状态过滤）。",
                parameters=_QUERY_PLAN_TASKS_SCHEMA,
                handler=_make_handler(handle_query_plan_tasks, plan_task_app=app),
            ))
            logger.info("PlanTask tools registered to BusinessFlowToolRegistry: 4 tools")
        except Exception as e:
            logger.warning("PlanTask tool registration failed: %s", e)

    def _init_plan_task_module(self) -> None:
        """初始化计划任务系统：Service + Scheduler + Application + WorkflowIntegrator。"""
        try:
            from .repositories.plan_task_repo import (
                PlanTaskTemplateRepo,
                PlanTaskInstanceRepo,
                PlanTaskLogRepo,
                PlanTaskDeliverableRepo,
            )
            from .repositories.user_repo import UserRepository
            from .services.plan_task_service import PlanTaskService
            from .services.plan_task_scheduler import PlanTaskScheduler
            from .services.workflow_integrator import WorkflowIntegrator
            from .application.plan_task_app import PlanTaskApplication

            # 创建 Service
            self._plan_task_service = PlanTaskService(
                template_repo=PlanTaskTemplateRepo(),
                instance_repo=PlanTaskInstanceRepo(),
                log_repo=PlanTaskLogRepo(),
                deliverable_repo=PlanTaskDeliverableRepo(),
                user_repo=UserRepository(),
            )

            # 创建 WorkflowIntegrator（workflow_client 待工作流系统就绪后注入）
            self._workflow_integrator = WorkflowIntegrator(
                workflow_client=None,
                plan_task_service=self._plan_task_service,
            )

            # 创建 Application（注入 workflow_integrator，确认后触发工作流）
            self._plan_task_app = PlanTaskApplication(
                self._plan_task_service, workflow_integrator=self._workflow_integrator
            )

            # 创建并启动调度引擎（注入 workflow_integrator，tick 内重试启动工作流）
            self._plan_task_scheduler = PlanTaskScheduler(
                service=self._plan_task_service,
                config=self.config,
                outbound_bus=self.outbound_bus,
                llm_client=self._llm_client,
                workflow_integrator=self._workflow_integrator,
            )

            # 启动后台调度循环
            import asyncio
            asyncio.ensure_future(self._plan_task_scheduler.start())

            logger.info(
                "PlanTask module initialized: service + scheduler + app + workflow_integrator ready"
            )
        except Exception as e:
            logger.warning("PlanTask module init failed: %s", e)
            self._plan_task_service = None
            self._plan_task_scheduler = None
            self._plan_task_app = None
            self._workflow_integrator = None

    def _init_permission_module(self) -> None:
        """初始化权限管理模块（阶段二：三维鉴权 + 校验接口）。

        阶段一：PermissionService 在 SessionFactory._build_context() 中被调用，
        组装 PermissionSnapshot 注入 SessionContext。
        阶段二：PermissionCache + PermissionAuthEngine + PermissionAuditLogRepository +
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
            except Exception:
                pass  # router registration handled by api/server.py

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

            self._node_service = NodeService()
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

    # ── M8c: 项目日记 + 长期记忆 ──

    def _init_m8c_services(self) -> None:
        """初始化 M8c 文件级持久化服务：EventJournal + UserMemoryService。

        EventJournal：项目事件流水日志，记录所有系统事件的摘要行。
        通过 set_journal() 注入到 event_app / task_app / meeting_app / file_app /
        query_service，在业务操作完成后自动追加。

        UserMemoryService：用户长期工作记忆，Agent 通过 write_user_memory 工具
        写入用户的长期需求。Session 创建时加载为上下文字段。
        """
        # ── 1. EventJournal（项目日记）──
        try:
            from .services.event_journal import EventJournal
            journal_path = self.config.journal_path or ""
            if not journal_path:
                # Docker 容器内默认路径：/app/journal/项目日志.md
                container_path = Path("/app/journal/项目日志.md")
                if container_path.parent.exists():
                    journal_path = str(container_path)
                else:
                    # 开发环境回退
                    journal_path = str(
                        Path(__file__).resolve().parents[2]
                        / "emily-data" / "journal" / "项目日志.md"
                    )
            journal = EventJournal(
                path=journal_path,
                enabled=self.config.journal_enabled,
            )
            self._event_journal = journal
            logger.info("M8c: EventJournal initialized — path=%s enabled=%s",
                         journal_path, journal.enabled)

            # 注入到已创建的 Application 实例
            if self._event_app is not None:
                self._event_app.set_journal(journal)
                self._task_app.set_journal(journal)
                self._meeting_app.set_journal(journal)
                self._file_app.set_journal(journal)
                self._query_service.set_journal(journal)
                logger.info("M8c: EventJournal injected into 4 apps + query_service")
        except Exception as e:
            logger.warning("M8c: EventJournal init failed: %s", e)
            self._event_journal = None

        # ── 2. UserMemoryService（长期记忆）──
        try:
            from .services.user_memory_service import UserMemoryService
            memory_dir = self.config.user_memory_dir or ""
            if not memory_dir:
                container_path = Path("/app/user_memory")
                if container_path.exists():
                    memory_dir = str(container_path)
                else:
                    memory_dir = str(
                        Path(__file__).resolve().parents[2]
                        / "emily-data" / "user_memory"
                    )
            self._user_memory_service = UserMemoryService(
                memory_dir=memory_dir,
                enabled=self.config.user_memory_enabled,
                max_entries=self.config.user_memory_max_entries,
            )
            logger.info("M8c: UserMemoryService initialized — dir=%s enabled=%s",
                         memory_dir, self._user_memory_service.enabled)

            # 将 write_user_memory 工具注册到 BusinessFlowToolRegistry
            if self._business_flow_tools is not None:
                from .tools.memory_tool import create_memory_tool
                from .tools.business_flow_tools import BusinessFlowTool as _BFT
                mem_tool = create_memory_tool(self._user_memory_service)
                self._business_flow_tools.register(_BFT(
                    name=mem_tool.name,
                    description=mem_tool.description,
                    parameters=mem_tool.parameters,
                    handler=mem_tool.execute,
                ))
                logger.info("M8c: write_user_memory tool registered to BusinessFlowToolRegistry")
        except Exception as e:
            logger.warning("M8c: UserMemoryService init failed: %s", e)
            self._user_memory_service = None

    def _register_node_tools(self) -> None:
        """将全景节点工具注册到 BusinessFlowToolRegistry。"""
        try:
            from .tools.node_tool import (
                handle_create_node,
                handle_query_node,
                handle_update_node_progress,
                handle_add_node_dependency,
                handle_mount_child_node,
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
            )
            from .tools.business_flow_tools import BusinessFlowTool

            self._business_flow_tools.register(BusinessFlowTool(
                name="create_node",
                description=_CREATE_NODE_DESCRIPTION,
                parameters=_CREATE_NODE_SCHEMA,
                handler=handle_create_node,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="query_node",
                description=_QUERY_NODE_DESCRIPTION,
                parameters=_QUERY_NODE_SCHEMA,
                handler=handle_query_node,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="update_node_progress",
                description=_UPDATE_PROGRESS_DESCRIPTION,
                parameters=_UPDATE_PROGRESS_SCHEMA,
                handler=handle_update_node_progress,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="add_node_dependency",
                description=_ADD_DEPENDENCY_DESCRIPTION,
                parameters=_ADD_DEPENDENCY_SCHEMA,
                handler=handle_add_node_dependency,
            ))
            self._business_flow_tools.register(BusinessFlowTool(
                name="mount_child_node",
                description=_MOUNT_CHILD_DESCRIPTION,
                parameters=_MOUNT_CHILD_SCHEMA,
                handler=handle_mount_child_node,
            ))
            logger.info("Node tools registered to BusinessFlowToolRegistry: 5 tools")
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

        # 计划任务系统：注入到 PlanTaskMatchHook（§2.5 计划外事件匹配）
        if self._plan_task_service is not None:
            injected["plan_task_service"] = self._plan_task_service

        # 邮箱模块：供 LLM Tool 使用
        if self._email_service is not None:
            injected["email_service"] = self._email_service

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
        result = {
            "status": "ok",
            "initialized": self._initialized,
            "sessions": pool.size if pool else 0,
            "uptime": pool.uptime_seconds if pool else 0,
            "bus_hooks": self._bus.hook_count() if self._bus else 0,
        }
        return result
