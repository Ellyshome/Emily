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

    # ---- 上下文预算 / 压缩（参照 Pi Agent compaction）----
    llm_context_window_override: int = 0
    """模型上下文窗口覆盖值（0 = 用 model_registry 表内值；换私有部署模型时填）"""

    llm_compact_reserve_tokens: int = 16384
    """压缩触发保留额：已用 > 窗口 - 该值 时触发压缩（同时充当摘要输出预算来源）"""

    llm_compact_keep_recent_tokens: int = 20000
    """压缩时保留的近期 token 预算（按 token 累计，而非固定条数）"""

    llm_dynamic_output: bool = True
    """是否按剩余窗口动态压低输出上限（避免长上下文下输入挤爆输出）"""

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

    # ---- Mermaid 决策树 ----
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

    # ---- RAG / pgvector 知识库 ----
    kb_enabled: bool = False
    """是否启用知识库 RAG 功能"""

    tei_url: str = "http://emily-embed:80"
    """本地 TEI embedding 服务地址（本地优先路径）。
    容器内为 compose 服务名 emily-embed；宿主机脚本可经 EMILY_TEI_URL 覆盖。"""

    embedding_mode: str = "auto"
    """Embedding 后端选择：auto（默认，本地优先 + 远程 API 兜底）/ local（仅本地，不兜底）
    / remote（仅远程 API，不兜底）。选型实现见 infrastructure/embedding/factory.py。"""

    # ---- 远程 Embedding API（本地不可用时的兜底）----
    embedding_api_url: str = ""
    """远程 Embedding API 地址（OpenAI 兼容 /v1/embeddings）。
    auto 模式下仅当本地 TEI 不可用时使用；embedding_mode=remote 时才强制使用。"""
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
    progress_message_template: str = "收到，正在为你{action}，请稍候..."
    """前导信息模板，{action} 由系统根据操作类型自动填充"""

    # ── 分级兜底（Fallback Tiering）──
    fallback_basic_tools: str = "knowledge_search,chat_archive"
    """基础兜底工具白名单（逗号分隔），普通用户可见的最小只读集。"""

    fallback_advanced_write_tools: str = "record_event,record_task,record_meeting,record_file"
    """高级兜底追加写白名单（逗号分隔），仅 L4+ 或管理单位可用的追加型工具。"""

    fallback_admin_min_level: int = 4
    """高级兜底所需的最低权限等级（>= 此值或 is_management_unit 视为高级档）。"""

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

    langgraph_checkpointer: str = "postgres"
    """图检查点后端：postgres（默认，AsyncPostgresSaver 持久化，进程重启断点不丢）或
    memory（MemorySaver，仅用于本地调试/无库环境）。Postgres 不可达时自动回退 memory
    并打 WARNING，不阻断启动。可通过环境变量 EMILY_LANGGRAPH_CHECKPOINTER 覆盖。"""

    # ── 专家Agent 配置 ──
    expert_review_enabled: bool = True
    """专家评审功能开关。False 时全局跳过专家评审——即使 SOP 已绑定 ACTIVE 专家，
    routing 也直接进 executing（agent loop），并在日志中记录跳过说明。
    可通过环境变量 EMILY_EXPERT_REVIEW_ENABLED 覆盖（false/0/no/off 视为关闭）。"""

    expert_model: str = "deepseek-chat"
    """专家评审用模型（chat 类，支持 temperature + json_mode）"""

    llm_expert_max_tokens: int = 16384
    """专家评审 LLM 最大输出 token 数（复杂评审需足够 token 输出完整 JSON）"""

    # ── Agent loop（L3）──
    agent_loop_max_iterations: int = 12
    """Agent loop 最大迭代次数（agent_node↔tool_node 循环上限，防 runaway）。
    超限升级外层 error_analysis 兜底。"""

    # ── 会话编排（M7/M8）──
    orchestrator_enabled: bool = True
    """会话编排总开关。关闭时 SessionAgent 走原 flat 拆分 + run_all_with_message"""

    orchestrator_max_depth: int = 3
    """WI DAG 最大深度（防无限编排；动态追加 WI 的 depth 超过此值即丢弃）"""

    orchestrator_max_dynamic_wis: int = 2
    """单轮动态追加 WI 上限（跨域检索编排；达到上限后 on_wi_done 返回空）"""

    session_loop_sop_allowlist: str = ""
    """新循环的 SOP 能力准入清单（逗号分隔短形编号，如 "SOP-002-REC,SOP-005-QRY"）。
    空 = 全部放开。未列入的 SOP 能力不进入新循环的能力目录（灰度逐 SOP 放开）。"""

    # 会话主循环开关（session_loop_enabled）与会话编排图开关（session_graph_enabled）
    # 均已于退役中移除：会话池（session/ 编排内核）为唯一入站渠道，
    # 关闭即回退的旧链路与开关一并作废（见 需求/LangGraph编排内核化/..._退役记录_V1.md）

    capability_call_timeout_seconds: int = 120
    """单次能力调用超时（秒）；超时以结构化失败结果回灌对话，由循环收敛（AC-US-01.4）。"""

    # ── Checkpoint 持久化 ──
    checkpoint_resume_window_seconds: int = 1800
    """超时后可恢复的时间窗口（秒），默认 30 分钟"""

    # ---- 计划任务系统 (Scheduled Task Module) ----
    scheduler_enabled: bool = True
    """调度引擎总开关"""

    scheduler_tick_seconds: int = 60
    """调度循环间隔（秒），默认 60 秒"""

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
    permission_cache_ttl_seconds: int = 300
    """权限矩阵缓存 TTL（秒），默认 5 分钟"""

    permission_fail_open: bool = True
    """权限查询失败时降级为访客（True）或拒绝（False）"""

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
