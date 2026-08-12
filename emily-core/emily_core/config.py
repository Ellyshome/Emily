"""配置模块 —— Emily Core 运行时配置。"""

from dataclasses import dataclass, field


@dataclass
class Config:
    """Emily Core 配置。

    配置来源优先级: AstrBot 插件配置 > 文件 > 默认值。
    """

    bot_name: str = "Emy"
    """机器人名称，用于 At 判断"""

    takeover_mode: str = "monitor"
    """接管模式: observe / collaborate / managed / monitor
    monitor: 群聊静默收集所有消息与文件，仅 @机器人 时回复；私聊正常响应"""

    log_level: str = "INFO"
    """日志级别"""

    log_dir: str = "logs/"
    """日志文件目录"""

    log_to_file: bool = True
    """是否写入日志文件"""

    # ---- LLM 配置 ----
    llm_api_key: str = ""
    """LLM API 密钥（为空时所有消息按 chat 处理）"""

    llm_base_url: str = "https://api.deepseek.com"
    """LLM API 基础 URL（兼容 DeepSeek / OpenAI 等）"""

    llm_model: str = "deepseek-v4-flash"
    """LLM 模型名称"""

    llm_temperature: float = 0.1
    """LLM 采样温度（路由场景建议 0.1）"""

    llm_max_tokens: int = 1024
    """LLM 最大输出 token 数"""

    llm_agent_loop_max_tokens: int = 8192
    """Agent loop 专用 max_tokens（v4-pro reasoner 需更大余量，按实际用量计费，设大不等于花得多）"""

    llm_router_model: str = "deepseek-v4-flash"
    """路由/意图识别用模型（轻量结构化任务，用 flash 而非 pro，快且省）"""

    llm_guardian_model: str = "deepseek-v4-flash"
    """Guardian 审核用模型（轻量结构化任务，用 flash 而非 pro）"""

    llm_agent_loop_model: str = "deepseek-v4-pro"
    """Agent loop 用模型（空则回退 router_model → model）。
    默认 v4-pro：标准 function calling 稳定，避免 v4-flash 的 DSML tool_call 泄漏问题。"""

    # ---- 数据库 (PostgreSQL) ----
    database_url: str = ""
    """PostgreSQL 连接 URL（为空时使用默认参数连接 emily-postgres:5432/emily）。
    格式: postgresql://user:password@host:port/database"""

    # ---- 文件存储 ----
    storage_root: str = ""
    """文件存储根目录（为空时使用插件目录下的 files/ 文件夹）"""

    # ── Prompt 文件目录 ──
    prompts_dir: str = ""
    """Prompt 模板文件目录路径（为空时走多级回退：环境变量 EMILY_PROMPTS_DIR →
    /app/prompts → emily-data/prompts）。目录下按名称存放 .md 文件：
    routing.md / planner.md / guardian_step.md / guardian_reply.md"""

    # ---- Agent 配置 ----
    agent_max_iterations: int = 10
    """MasterAgent ReAct 循环最大迭代次数。"""

    agent_context_max_turns: int = 10
    """MasterAgent 对话上下文保留的最大轮数。"""

    agent_context_ttl_seconds: int = 600
    """对话上下文过期时间（秒），默认 10 分钟。"""

    # ---- Mermaid 决策树 ----
    pending_issues_enabled: bool = True
    """待解决问题清单开关"""

    pending_issues_path: str = ""
    """待解决问题清单文件路径（为空时默认 tem_log/待解决问题.md）"""

    # ---- 项目日记与长期记忆 ----
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

    # ---- SOP 发现式路由 ----
    sop_repository_dir: str = ""
    """SOP 仓库目录路径（为空时默认 SOPrepository/）"""

    # ---- RAG / pgvector 知识库 ----
    kb_enabled: bool = False
    """是否启用知识库 RAG 功能"""

    tei_url: str = "http://tei:80"
    """TEI embedding 服务地址"""

    # ---- 远程 Embedding API（替代本地 TEI）----
    embedding_api_url: str = ""
    """远程 Embedding API 地址（OpenAI 兼容 /v1/embeddings）。
    设置后优先使用远程 API，不使用本地 TEI 容器。"""
    embedding_api_key: str = ""
    """远程 Embedding API 密钥"""
    embedding_model: str = ""
    """远程 Embedding 模型名，如 BAAI/bge-m3"""

    rag_similarity_threshold: float = 0.3
    """RAG 检索相似度阈值（0.0-1.0）"""

    kb_top_k: int = 5
    """RAG 检索返回的最大结果数"""

    kb_local_fallback_dir: str = ""
    """本地知识库目录（为空时默认 项目资料/）"""

    # ---- VLM 视觉大模型（OCR）----
    vlm_api_url: str = "https://api.siliconflow.cn/v1/chat/completions"
    """VLM API 地址"""

    vlm_api_key: str = ""
    """VLM API 密钥"""

    vlm_model: str = "Qwen/Qwen3-VL-8B-Instruct"
    """VLM 模型名称"""

    # ---- 前导信息机制 ----
    enable_progress_message: bool = True
    """前导信息开关（深度操作时先发"处理中..."再发结果）"""

    progress_message_template: str = "收到，正在为你{action}，请稍候..."
    """前导信息模板，{action} 由系统根据操作类型自动填充"""

    progress_threshold_iterations: int = 3
    """对话上下文超过此轮数时自动发送前导信息"""

    # ── 聊天归档 ──
    chat_archive_enabled: bool = True
    """全量聊天记录存档开关（入站+出站双向归档）"""

    chat_archive_include_progress: bool = False
    """前导消息是否纳入对话历史查询（默认否）"""

    # ── Agent 追踪 ──
    agent_trace_enabled: bool = True
    """Agent 推理过程记录总开关"""

    agent_trace_detail_level: str = "summary"
    """追踪详细级别: summary(仅元数据) / full(含完整prompt)"""

    llm_interaction_log_enabled: bool = True
    """LLM 交互日志开关（token消耗/延迟/响应类型）"""

    tool_call_log_enabled: bool = True
    """工具调用日志开关（工具名/参数/结果摘要）"""

    # ── 文件下载 ──
    file_download_enabled: bool = True
    """附件自动下载开关（默认开启，下载失败会自动跳过不阻断管道）"""

    # ── Session 主线编排：公共 Pipeline BUS（4 节点）──
    hook_config_path: str = ""
    """Hook 声明式配置文件路径（为空时默认 /app/config/hook_config.json 或 emily-data/config/hook_config.json）"""

    # ── Session 池（蓝图 §3.4 / §10.4）──
    session_ttl_seconds: int = 600
    """Session 无新消息过期时间（秒），默认 10 分钟。"""

    session_max_concurrent: int = 100
    """最大并发 Session 数。"""

    workitem_max_per_session: int = 5
    """每 Session 最大 WorkItem 数。"""

    # ── WorkItem 执行引擎（LangGraph StateGraph）──
    langgraph_max_replan: int = 1
    """LangGraph 引擎最大重规划次数（node3 失败→error_analysis→node2 循环上限，防死循环）。
    0 = 禁用重规划（node3 失败直接走 error_analysis 分类，但不重规划）。
    1 = 允许 1 次重规划（默认，平衡纠错能力与成本）。"""

    langgraph_max_retry: int = 2
    """LangGraph 引擎最大直接重试次数（node3→error_analysis→node3 循环上限，防死循环）。
    超过后升级为 REPLAN（回 node2 重规划），避免 transient_failure 分类导致的无限重试。"""

    # ── 专家Agent 配置 ──
    expert_review_enabled: bool = True
    """专家评审功能开关"""

    expert_model: str = "deepseek-chat"
    """专家评审用模型（chat 类，支持 temperature + json_mode）"""

    llm_expert_max_tokens: int = 16384
    """专家评审 LLM 最大输出 token 数（复杂评审需足够 token 输出完整 JSON）"""

    # ── Agent loop（L3）──
    agent_loop_max_iterations: int = 12
    """Agent loop 最大迭代次数（agent_node↔tool_node 循环上限，防 runaway）。
    超限升级外层 error_analysis 兜底。"""

    # ── Checkpoint 持久化 ──
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

    # ---- 权限管理 (Permission) ----
    """临近超时提醒提前量（分钟），默认 60 分钟"""

    scheduler_overdue_check_interval: int = 300
    """超时检测间隔（秒），默认 300 秒"""

    scheduler_escalate_after_overdue_days: int = 7
    """超期 N 天后自动升级给上级（P2），默认 7 天"""

    # ── Session 归档 md 文件 ----
    session_archive_enabled: bool = True
    """会话归档 md 文件实时追加开关"""

    session_archive_dir: str = ""
    """会话归档 md 文件存储目录（为空时三级回退：config → /app/session_archives → emily-data/session_archives）"""

    # ── 用户准入 (User Binding) ----
    auto_create_user: bool = False
    """未知 IM 用户是否自动创建系统用户。True=自动创建（开发/测试），
    False=拒绝未知用户（生产环境推荐）。"""

    auto_create_whitelist: list = field(default_factory=list)
    """IM 用户 ID 白名单——仅当 auto_create_user=False 时生效。
    白名单内的 im_user_id 仍可自动创建用户。"""

    # ---- 权限管理 (Permission) ----
    permission_enabled: bool = True
    """权限管理模块总开关"""

    permission_cache_ttl_seconds: int = 300
    """权限矩阵缓存 TTL（秒），默认 5 分钟"""

    permission_super_admin_level: int = 6
    """系统管理员 level 阈值（L6）"""

    permission_session_max_ttl_hours: int = 24
    """Session 权限快照最大存活时间（小时），超时自动刷新"""

    permission_fail_open: bool = True
    """权限查询失败时降级为访客（True）或拒绝（False）"""

    permission_agent_issue_integration_enabled: bool = False
    """协同待办模块集成开关 —— 关闭时 permission_requests 自身承载审批流；
    待 agent_issues 模块落地后置 True 切换为真实 HTTP 调用"""

    # ---- 邮箱模块 (Email) ----
    email_smtp_host: str = "smtp.qq.com"
    """SMTP 服务器地址"""

    email_smtp_port: int = 465
    """SMTP 端口（465 SSL / 587 STARTTLS）"""

    email_imap_host: str = "imap.qq.com"
    """IMAP 服务器地址"""

    email_imap_port: int = 993
    """IMAP 端口（993 SSL）"""

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
