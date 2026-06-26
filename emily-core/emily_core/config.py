"""配置模块 —— M7 MasterAgent + Mermaid 决策树架构。"""

from dataclasses import dataclass, field


@dataclass
class Config:
    """Emily Core 配置。

    配置来源优先级: AstrBot 插件配置 > 文件 > 默认值。
    """

    bot_name: str = "Emy"
    """机器人名称，用于 At 判断"""

    takeover_mode: str = "collaborate"
    """接管模式: observe / collaborate / managed"""

    log_level: str = "INFO"
    """日志级别"""

    log_dir: str = "logs/"
    """日志文件目录"""

    log_to_file: bool = True
    """是否写入日志文件"""

    # ---- LLM 配置 (M3 启用) ----
    llm_api_key: str = ""
    """LLM API 密钥（为空时所有消息按 chat 处理）"""

    llm_base_url: str = "https://api.deepseek.com"
    """LLM API 基础 URL（兼容 DeepSeek / OpenAI 等）"""

    llm_model: str = "deepseek-chat"
    """LLM 模型名称"""

    llm_temperature: float = 0.1
    """LLM 采样温度（路由场景建议 0.1）"""

    llm_max_tokens: int = 1024
    """LLM 最大输出 token 数"""

    # ---- 数据库 (PostgreSQL) ----
    database_url: str = ""
    """PostgreSQL 连接 URL（为空时使用默认参数连接 maxkb:5432/team_brain）。
    格式: postgresql://user:password@host:port/database"""

    # ---- 文件存储 ----
    storage_root: str = ""
    """文件存储根目录（为空时使用插件目录下的 files/ 文件夹）"""

    master_agent_prompt_path: str = ""
    """MasterAgent prompt 文件路径（为空时默认 prompts/master_agent.txt）"""

    domain_knowledge_path: str = ""
    """领域知识文件路径（为空时默认 prompts/domain_knowledge.md）"""

    # ---- Agent 配置 (M7) ----
    enable_master_agent: bool = True
    """启用 MasterAgent（ReAct 模式 + Mermaid 决策树导航），为唯一消息处理路径。"""

    agent_max_iterations: int = 10
    """MasterAgent ReAct 循环最大迭代次数。"""

    agent_temperature: float = 0.3
    """MasterAgent LLM 采样温度（比路由分类高一些，支持自然对话）。"""

    agent_context_max_turns: int = 10
    """MasterAgent 对话上下文保留的最大轮数。"""

    agent_context_ttl_seconds: int = 600
    """对话上下文过期时间（秒），默认 10 分钟。"""

    # ---- M7.1: Mermaid 决策树 ----
    flow_maps_dir: str = ""
    """Mermaid 决策树文件目录（为空时默认 prompts/flows/）。"""

    pending_issues_enabled: bool = True
    """待解决问题清单开关"""

    pending_issues_path: str = ""
    """待解决问题清单文件路径（为空时默认 tem_log/待解决问题.md）"""

    # ---- M8c: 项目日记与长期记忆 ----
    journal_enabled: bool = True
    """项目事件日志开关"""

    journal_path: str = ""
    """事件日志文件路径（为空时默认 tem_log/项目日志.md）"""

    user_memory_enabled: bool = True
    """用户长期记忆开关"""

    user_memory_dir: str = ""
    """用户记忆存储目录（为空时默认 memory/）"""

    user_memory_max_entries: int = 50
    """每个用户长期记忆最大条目数"""

    # ---- M9: SOP 发现式路由 ----
    sop_repository_dir: str = ""
    """SOP 仓库目录路径（为空时默认 SOPrepository/）"""

    # ---- Ex3/Ex4: RAG / MaxKB 知识库 ----
    kb_enabled: bool = False
    """是否启用知识库 RAG 功能"""

    maxkb_url: str = "http://maxkb:8080"
    """MaxKB 服务地址（Docker 内部用服务名）"""

    maxkb_api_key: str = ""
    """MaxKB API 密钥（在 MaxKB Web UI 中创建）"""

    maxkb_app_id: str = ""
    """MaxKB 应用 ID（关联了知识库的应用）"""

    kb_top_k: int = 5
    """RAG 检索返回的最大结果数"""

    kb_local_fallback_dir: str = ""
    """本地知识库目录（为空时默认 项目资料/）"""

    # ---- Ex4: MaxKB Admin hit_test 检索 ----
    maxkb_admin_password: str = ""
    """MaxKB 管理员密码（用于 hit_test API 登录，默认账号 admin）"""

    maxkb_knowledge_id: str = ""
    """MaxKB 知识库 ID（hit_test 目标知识库 UUID）"""

    maxkb_search_mode: str = "embedding"
    """hit_test 搜索模式: embedding / keywords / blend"""

    maxkb_similarity_threshold: float = 0.3
    """hit_test 相似度阈值（0.0-1.0）"""

    # ---- M8b: 前导信息机制 ----
    enable_progress_message: bool = True
    """前导信息开关（深度操作时先发"处理中..."再发结果）"""

    progress_message_template: str = "收到，正在为你{action}，请稍候..."
    """前导信息模板，{action} 由系统根据操作类型自动填充"""

    progress_threshold_iterations: int = 3
    """对话上下文超过此轮数时自动发送前导信息"""

    # ── M11: 聊天归档 ──
    chat_archive_enabled: bool = True
    """全量聊天记录存档开关（入站+出站双向归档）"""

    chat_archive_include_progress: bool = False
    """前导消息是否纳入对话历史查询（默认否）"""

    # ── M11: Agent 追踪 ──
    agent_trace_enabled: bool = True
    """Agent 推理过程记录总开关"""

    agent_trace_detail_level: str = "summary"
    """追踪详细级别: summary(仅元数据) / full(含完整prompt)"""

    llm_interaction_log_enabled: bool = True
    """LLM 交互日志开关（token消耗/延迟/响应类型）"""

    tool_call_log_enabled: bool = True
    """工具调用日志开关（工具名/参数/结果摘要）"""

    # ── M13: 文件下载 ──
    file_download_enabled: bool = True
    """附件自动下载开关（M13: 默认开启，下载失败会自动跳过不阻断管道）"""

    # ── Session 主线编排：公共 Pipeline BUS（4 节点）──
    pipeline_mode: str = "session"
    """消息处理编排模式: session（SessionPool → SessionAgent → WorkItem → 4 节点 Pipeline BUS）"""

    hook_config_path: str = ""
    """Hook 声明式配置文件路径（为空时默认 /app/config/hook_config.json 或 emily-data/config/hook_config.json）"""

    # ── Session 池（蓝图 §3.4 / §10.4）──
    session_ttl_seconds: int = 600
    """Session 无新消息过期时间（秒），默认 10 分钟。"""

    session_max_concurrent: int = 100
    """最大并发 Session 数。"""

    workitem_max_per_session: int = 5
    """每 Session 最大 WorkItem 数。"""

    # ── Phase B: Pipeline 节点大脑模式开关 ──
    planner_mode: str = "real"
    """规划大脑模式: mock | real（需 EMILY_LLM_API_KEY）"""

    # ── Phase C: Pipeline 节点大脑模式开关 ──
    executor_mode: str = "real"
    """执行大脑模式: mock | real（需 EMILY_LLM_API_KEY + BusinessFlowToolRegistry）"""

    guardian_mode: str = "mock"
    """守护大脑模式: mock（MockGuardian 兜底）"""

    auth_mode: str = "mock"
    """鉴权引擎模式: mock | real（需 SOPIntentRegistry）"""

    risk_mode: str = "mock"
    """风险评估模式: mock | real"""

    # ── M12b: Checkpoint 持久化 ──
    checkpoint_enabled: bool = True
    """检查点持久化开关"""

    checkpoint_ttl_seconds: int = 300
    """检查点超时时间（秒），默认 5 分钟"""

    checkpoint_resume_window_seconds: int = 1800
    """超时后可恢复的时间窗口（秒），默认 30 分钟"""

    checkpoint_max_per_user: int = 5
    """每用户最大活跃检查点数"""

    # ---- 计划任务系统 (Scheduled Task Module) ----
    scheduler_enabled: bool = True
    """调度引擎总开关"""

    scheduler_tick_seconds: int = 60
    """调度循环间隔（秒），默认 60 秒"""

    # ---- 全局状态机 (State Machine) ----
    state_machine_enabled: bool = True
    """全局状态机模块总开关"""

    state_machine_cascade_max_depth: int = 5
    """级联更新最大传播深度，默认 5"""

    state_machine_auto_start_nodes: bool = False
    """级联中是否自动启动满足度为 100 的节点（默认关闭，需人工确认）"""

    # ---- 项目级 Agent (ProjectAgent) ----
    project_agent_enabled: bool = True
    """项目级 Agent 总开关（状态机主动维护 + 健康度检查 + AI 自动运维）"""

    project_agent_tick_seconds: int = 300
    """ProjectAgent 调度循环间隔（秒），默认 300 秒（5 分钟）"""

    project_agent_stale_threshold_days: int = 14
    """节点卡滞判定阈值（天）。IN_PROGRESS/BLOCKED 超过此天数未变化视为卡滞"""

    project_agent_deadline_warn_days: int = 7
    """milestone 节点到期前 N 天开始预警"""

    project_agent_alert_cooldown_hours: int = 24
    """同一节点同一问题的告警冷却时间（小时），避免重复推送"""

    scheduler_reminder_before_minutes: int = 60
    """临近超时提醒提前量（分钟），默认 60 分钟"""

    scheduler_overdue_check_interval: int = 300
    """超时检测间隔（秒），默认 300 秒"""

    scheduler_escalate_after_overdue_days: int = 7
    """超期 N 天后自动升级给上级（P2），默认 7 天"""

    # ---- 权限管理 (Permission) ----
    permission_enabled: bool = True
    """权限管理模块总开关"""

    permission_cache_ttl_seconds: int = 300
    """权限矩阵缓存 TTL（秒），默认 5 分钟"""

    permission_super_admin_level: int = 6
    """系统管理员 permission_level 阈值（L6）"""

    permission_session_max_ttl_hours: int = 24
    """Session 权限快照最大存活时间（小时），超时自动刷新"""

    permission_fail_open: bool = True
    """权限查询失败时降级为访客（True）或拒绝（False）"""

    permission_agent_issue_integration_enabled: bool = False
    """协同待办模块集成开关 —— 关闭时 permission_requests 自身承载审批流；
    待 agent_issues 模块落地后置 True 切换为真实 HTTP 调用"""

    extra: dict = field(default_factory=dict)
    """预留扩展字段"""

    @classmethod
    def from_dict(cls, data: dict | None) -> "Config":
        """从字典加载配置，覆盖默认值。"""
        if not data:
            return cls()
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)
