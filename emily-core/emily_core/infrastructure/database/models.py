"""SQLAlchemy ORM 模型 —— 关系型数据库 PostgreSQL 表结构。

十六张表（原 15 + M12b SOPCheckpoint）：
  原有表（扩展）：users / user_im_bindings / conversations / messages / projects
                  / events / tasks / meetings / files
  新增表：company_info / project_indicator_details
          / business_flow_orders / instruction_orders / project_plans / plan_items
          / hook_execution_logs (M12a) / sop_checkpoints (M12b)

users 表已合并原 employee 的人事档案字段（gender/id_card/qq/wechat/grouping/position 等），
不再需要独立的 employees 表。
"""

from datetime import datetime, timezone, timedelta
import uuid

from sqlalchemy import Column, String, Integer, ForeignKey, UniqueConstraint, Boolean, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


BEIJING_TZ = timezone(timedelta(hours=8))


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _new_id(prefix: str = "") -> str:
    """生成带前缀的短 ID，格式: {prefix}-YYYYMMDD-{uuid8}"""
    now = datetime.now(BEIJING_TZ)
    date_part = now.strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:8]
    if prefix:
        return f"{prefix.upper()}-{date_part}-{short_uuid}"
    return f"{date_part}-{short_uuid}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _beijing_now() -> str:
    """北京时间 ISO8601 字符串（用于展示/日期字段）。"""
    return datetime.now(BEIJING_TZ).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# 原有表（M2-M4，扩展自需求文档的清单-数据库表.md）
# ══════════════════════════════════════════════════════════════════════════════


class User(Base):
    """人员信息表 —— 对应需求文档 im_message + employee。

    合并了系统用户（IM 绑定、登录认证）与人事档案（性别、身份证、岗位等）。
    人员类型不限于雇员——访客、临时工、供货商等均可记录，通过 grouping 区分。
    CHECK 约束（应用层强制）：phone/email/qq/wechat 不能同时为空。
    """
    __tablename__ = "users"
    # ── 原 users 字段（系统身份）──
    id = Column(String, primary_key=True, default=_new_uuid)
    username = Column(String(100))
    real_name = Column(String(100))
    phone = Column(String(50))
    email = Column(String(200))
    status = Column(String(50), default="active")
    is_admin = Column(Boolean, default=False)
    # ── 原 employee 字段（人事档案）──
    gender = Column(Integer, default=0)                      # 性别 0=未知 1=男 2=女
    id_card = Column(String(50), default="")                 # 身份证号
    qq = Column(String(50), default="")                      # QQ号
    wechat = Column(String(100), default="")                 # 微信号
    remark = Column(String, default="")                      # 人员备注（自然语言描述）
    creator_id = Column(String, nullable=True)               # 创建人ID
    is_deleted = Column(Boolean, default=False)                  # 逻辑删除标记
    perm_list = Column(String, default="[]")                 # 权限集（JSON数组）
    grouping = Column(Integer, default=0)                    # 分组 0=临时组 1=访客组 2=工程组 3=供货商 4=管理组
    company = Column(String, default="[]")                   # 隶属公司（JSON数组）
    position = Column(String, default="[]")                  # 本项目中负责岗位角色（JSON数组）
    # ── 时间戳 ──
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    im_bindings = relationship("UserImBinding", back_populates="user", lazy="selectin")


class UserImBinding(Base):
    __tablename__ = "user_im_bindings"
    id = Column(String, primary_key=True, default=_new_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    im_platform = Column(String(50), nullable=False)
    im_user_id = Column(String(200), nullable=False)
    im_display_name = Column(String(200))
    status = Column(String(50), default="active")
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    __table_args__ = (UniqueConstraint("im_platform", "im_user_id", name="uq_im_platform_user"),)
    user = relationship("User", back_populates="im_bindings")


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=_new_uuid)
    im_platform = Column(String(50), nullable=False)
    conversation_type = Column(String(50), nullable=False)
    conversation_id = Column(String(200), nullable=False)
    group_id = Column(String(200))
    title = Column(String(500))
    project_id = Column(String)
    takeover_mode = Column(String(50), default="collaborate")
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    __table_args__ = (UniqueConstraint("im_platform", "conversation_id", name="uq_im_platform_conv"),)
    messages = relationship("Message", back_populates="conversation", lazy="selectin")


class Message(Base):
    """通讯记录表 —— 对应需求文档 im_message。

    原有字段保留；新增 msg_type / file_url / receiver_id / group_id 对齐需求文档。
    """
    __tablename__ = "messages"
    # ── 原有字段 ──
    id = Column(String, primary_key=True, default=_new_uuid)
    event_id = Column(String(100), unique=True, nullable=False)
    message_uid = Column(String(200))
    conversation_id = Column(String, ForeignKey("conversations.id"))
    project_id = Column(String, nullable=True)
    sender_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    sender_im_id = Column(String(200), nullable=False)
    sender_name = Column(String(200))
    message_type = Column(String(50), default="text")
    direction = Column(String(50), default="user_to_agent")
    content = Column(String)
    attachments = Column(String, default="[]")
    is_at_bot = Column(Boolean, default=False)
    takeover = Column(Boolean, default=False)
    takeover_reason = Column(String(200))
    intent = Column(String(100))
    status = Column(String(50), default="received")
    created_at = Column(String, default=_utc_now)
    processed_at = Column(String)
    conversation = relationship("Conversation", back_populates="messages")

    # ── 新增字段（对齐 im_message 需求）──
    msg_type = Column(Integer, default=1)        # 消息类型 1=文本 2=图片 3=文件 4=语音 5=视频 6=卡片
    file_url = Column(String, default="")         # 文件/语音/视频 URL
    receiver_id = Column(String(200), nullable=True)  # 接收者ID（单聊）
    group_id = Column(String(200), nullable=True)     # 群ID（群聊，冗余以便查询）

    # M11: 附件关联
    attachments_rel = relationship("MessageAttachment", back_populates="message", lazy="selectin")


class Project(Base):
    """项目基本条件表 —— 对应需求文档 project_basic。

    原有字段保留；新增 address / city / lifecycle_stage / stage_updated_at /
    creator_id / is_deleted 对齐需求文档。
    """
    __tablename__ = "projects"
    # ── 原有字段 ──
    id = Column(String, primary_key=True, default=_new_uuid)
    code = Column(String(50), unique=True)
    name = Column(String(200), nullable=False)
    description = Column(String)
    status = Column(String(50), default="active")
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    events = relationship("Event", back_populates="project", lazy="selectin")

    # ── 新增字段（对齐 project_basic 需求）──
    address = Column(String(500), default="")          # 项目详细地址
    city = Column(String(100), default="")              # 所属城市
    lifecycle_stage = Column(Integer, default=0)        # 生命周期阶段 0=立项 1=规划设计 2=工程施工 3=交付结算
    stage_updated_at = Column(String, nullable=True)    # 阶段更新时间
    creator_id = Column(String, nullable=True)          # 创建人ID
    is_deleted = Column(Boolean, default=False)             # 逻辑删除标记


class Event(Base):
    __tablename__ = "events"
    id = Column(String, primary_key=True, default=_new_uuid)
    event_no = Column(String(50), unique=True, nullable=False)
    event_type = Column(String(100), nullable=False)
    category = Column(String(100), default="待分类")
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(String)
    event_date = Column(String)
    attachments = Column(String, default="[]")
    remarks = Column(String)
    payload = Column(String, default="{}")
    status = Column(String(50), default="pending")
    created_at = Column(String, default=_utc_now)
    confirmed_at = Column(String)
    related_event_ids = Column(String, default="[]")   # 关联事件ID（JSON数组），如 ["EVT-20260612-0001"]
    project = relationship("Project", back_populates="events")


class Task(Base):
    """任务表 —— 创建、分配、跟踪工作任务。"""
    __tablename__ = "tasks"
    id = Column(String, primary_key=True, default=_new_uuid)
    task_no = Column(String(50), unique=True, nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    source_message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    title = Column(String(500), nullable=False)
    description = Column(String)
    owner_id = Column(String, ForeignKey("users.id"), nullable=True)
    owner_text = Column(String(200))
    status = Column(String(50), default="todo")
    due_date = Column(String)
    due_text = Column(String(200))
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)


class Meeting(Base):
    """会议记录表 —— 对应需求文档 meeting_records。

    原有字段保留；新增 meeting_type / meeting_date / location / host_id /
    attendee_names / conclusion / action_items / related_file_ids / status /
    updated_at / is_deleted 对齐需求文档。
    """
    __tablename__ = "meetings"
    # ── 原有字段 ──
    id = Column(String, primary_key=True, default=_new_uuid)
    meeting_no = Column(String(50), unique=True, nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    title = Column(String(500))
    summary = Column(String)
    attendees = Column(String, default="[]")
    source_message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, default=_utc_now)

    # ── 新增字段（对齐 meeting_records 需求）──
    meeting_type = Column(Integer, default=0)            # 会议类型 0=周例会 1=专题会 2=协调会 3=技术交底 4=验收会 5=其他
    meeting_date = Column(String, nullable=True)         # 会议时间（ISO8601）
    location = Column(String(500), default="")           # 会议地点
    host_id = Column(String, nullable=True)              # 主持人ID
    attendee_names = Column(String, default="[]")        # 参会人姓名快照（JSON数组）
    conclusion = Column(String, default="")              # 会议结论与决议
    action_items = Column(String, default="[]")          # 待办事项列表（JSON数组）
    related_file_ids = Column(String, default="[]")      # 关联文件ID（JSON数组）
    status = Column(Integer, default=1)                  # 状态 0=草稿 1=已确认 2=已归档
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class File(Base):
    """文件存储表 —— 对应需求文档 file_storage。

    原有字段保留；新增 file_ext / file_url / file_hash / related_company_ids /
    responsible_id / version / is_latest / parent_file_id / change_log /
    confidentiality / creator_id / updated_at / is_deleted 对齐需求文档。
    """
    __tablename__ = "files"
    # ── 原有字段 ──
    id = Column(String, primary_key=True, default=_new_uuid)
    file_no = Column(String(50), unique=True, nullable=False)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    filename = Column(String(500), nullable=False)
    file_type = Column(String(100))
    bucket = Column(String(100))
    object_key = Column(String)
    storage_path = Column(String)
    file_size = Column(Integer)
    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    parse_status = Column(String(50), default="pending")
    created_at = Column(String, default=_utc_now)

    # ── 新增字段（对齐 file_storage 需求）──
    file_ext = Column(String(50), default="")            # 文件扩展名
    file_url = Column(String(1000), default="")           # 文件存储路径（NAS/OSS/本地）
    file_hash = Column(String(256), default="")           # 文件指纹 SHA256
    related_company_ids = Column(String, default="[]")    # 相关单位ID（JSON数组）
    responsible_id = Column(String, nullable=True)        # 负责人ID
    version = Column(String(50), default="V1.0")          # 文件版本号
    is_latest = Column(Boolean, default=True)                # 是否最新版本
    parent_file_id = Column(String, nullable=True)        # 父文件ID（版本关联）
    change_log = Column(String, default="[]")             # 变更记录（JSON数组）
    confidentiality = Column(Integer, default=0)          # 密级 0=公开 1=内部 2=机密 3=绝密
    creator_id = Column(String, nullable=True)            # 创建人ID
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    # M11: 附件来源追溯
    source_attachment_id = Column(String, ForeignKey("message_attachments.id"), nullable=True)
    attachment_refs = relationship("MessageAttachment", back_populates="file",
                                   foreign_keys="MessageAttachment.file_id")


# ══════════════════════════════════════════════════════════════════════════════
# 新增表（对齐需求文档 清单-数据库表.md）
# ══════════════════════════════════════════════════════════════════════════════


class CompanyInfo(Base):
    """公司基础信息表 —— 对应需求文档 company_info。"""
    __tablename__ = "company_info"
    id = Column(String, primary_key=True, default=_new_uuid)
    company_name = Column(String(256), nullable=False)       # 公司全称
    unified_code = Column(String(50), nullable=False)        # 统一社会信用代码（18位）
    business_desc = Column(String, default="")               # 业务描述
    project_leader_id = Column(String, nullable=False)       # 项目负责人ID → employee.id
    creator_id = Column(String, nullable=False)              # 创建者ID → employee.id
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class ProjectIndicatorDetail(Base):
    """项目指标表 —— 对应需求文档 project_indicator_details。"""
    __tablename__ = "project_indicator_details"
    id = Column(String, primary_key=True, default=_new_uuid)
    project_id = Column(String, nullable=False)              # 所属项目ID → projects.id
    indicator_name = Column(String(128), nullable=False)     # 指标名称
    indicator_value = Column(String(256), nullable=False)    # 指标值
    unit = Column(String(50), default="")                    # 指标单位
    source_file_id = Column(String, nullable=True)           # 来源文件ID → files.id
    description = Column(String, default="")                 # 指标说明
    is_constraint = Column(Boolean, default=False)               # 是否强制指标
    creator_id = Column(String, nullable=False)              # 创建人ID
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class BusinessFlowOrder(Base):
    """业务流转单 —— 对应需求文档 business_flow_orders。"""
    __tablename__ = "business_flow_orders"
    id = Column(String, primary_key=True, default=_new_uuid)
    flow_no = Column(String(100), unique=True, nullable=False)  # 流转单编号
    project_id = Column(String, nullable=True)                  # 关联项目ID → projects.id
    title = Column(String(500), nullable=False)                  # 业务概述
    flow_type = Column(Integer, default=0)                       # 流转类型 0=通用 1=设计变更 2=签证 3=材料进场 4=验收申请 5=付款申请
    metrics = Column(String, default="{}")                       # 量化任务指标（JSON对象）
    planned_finish_time = Column(String, nullable=True)          # 计划完成时间
    actual_finish_time = Column(String, nullable=True)           # 实际完成时间
    current_node = Column(String(100), default="")               # 当前节点名称
    current_handler_id = Column(String, nullable=True)           # 当前处理人ID → employee.id
    flow_records = Column(String, default="[]")                  # 流转记录（JSON数组）
    related_file_ids = Column(String, default="[]")              # 关联文件ID（JSON数组）
    related_meeting_ids = Column(String, default="[]")           # 关联会议ID（JSON数组）
    status = Column(Integer, default=0)                          # 状态 0=草稿 1=处理中 2=已完成 3=已驳回 4=已取消
    priority = Column(Integer, default=1)                        # 优先级 0=低 1=中 2=高 3=紧急
    creator_id = Column(String, nullable=False)                  # 创建人ID
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class InstructionOrder(Base):
    """指令单表 —— 对应需求文档 instruction_orders。"""
    __tablename__ = "instruction_orders"
    id = Column(String, primary_key=True, default=_new_uuid)
    instruction_no = Column(String(100), unique=True, nullable=False)  # 指令单编号
    project_id = Column(String, nullable=True)                  # 关联项目ID → projects.id
    title = Column(String(500), nullable=False)                  # 指令标题
    content = Column(String, nullable=False)                     # 指令内容
    instruction_type = Column(Integer, default=0)                # 指令类型 0=工作安排 1=整改要求 2=技术交底 3=会议通知 4=其他
    issuer_id = Column(String, nullable=False)                   # 指令发起者ID → employee.id
    executor_ids = Column(String, default="[]")                  # 执行者ID列表（JSON数组）
    deadline = Column(String, nullable=True)                     # 要求完成时间
    actual_finish_time = Column(String, nullable=True)           # 实际完成时间
    feedback = Column(String, default="")                        # 执行反馈
    related_file_ids = Column(String, default="[]")              # 关联附件ID（JSON数组）
    related_flow_id = Column(String, nullable=True)              # 关联流转单ID → business_flow_orders.id
    message_id = Column(String, nullable=True)                   # 来源消息ID → messages.id
    status = Column(Integer, default=0)                          # 状态 0=待执行 1=执行中 2=已完成 3=已驳回 4=已取消
    creator_id = Column(String, nullable=False)                  # 创建人ID
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class ProjectPlan(Base):
    """计划表（主表）—— 对应需求文档 project_plans。"""
    __tablename__ = "project_plans"
    id = Column(String, primary_key=True, default=_new_uuid)
    plan_no = Column(String(100), unique=True, nullable=False)   # 计划编号
    project_id = Column(String, nullable=True)                   # 关联项目ID → projects.id
    title = Column(String(500), nullable=False)                   # 计划标题
    plan_type = Column(Integer, default=0)                        # 计划类型 0=总进度计划 1=周计划 2=月计划 3=专项计划 4=个人计划
    start_date = Column(String, nullable=True)                    # 计划开始日期
    end_date = Column(String, nullable=True)                      # 计划结束日期
    creator_id = Column(String, nullable=False)                   # 计划录入者ID
    status = Column(Integer, default=0)                           # 状态 0=草稿 1=已发布 2=执行中 3=已完成 4=已作废
    parent_plan_id = Column(String, nullable=True)                # 父计划ID（计划分解）
    remark = Column(String, default="")                           # 计划备注
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class PlanItem(Base):
    """计划明细表 —— 对应需求文档 plan_items（project_plans 的子表）。"""
    __tablename__ = "plan_items"
    id = Column(String, primary_key=True, default=_new_uuid)
    plan_id = Column(String, nullable=False)                     # 所属计划ID → project_plans.id
    item_no = Column(Integer, nullable=False)                    # 明细项序号
    content = Column(String, nullable=False)                     # 计划内容描述
    responsible_id = Column(String, nullable=True)               # 责任人ID → employee.id
    reviewer_id = Column(String, nullable=True)                  # 验收人ID → employee.id
    planned_date = Column(String, nullable=True)                 # 计划完成日期
    actual_date = Column(String, nullable=True)                  # 实际完成日期
    is_completed = Column(Boolean, default=False)                    # 是否完成
    is_covered = Column(Boolean, default=False)                      # 是否被新计划覆盖
    related_instruction_ids = Column(String, default="[]")       # 关联指令单ID（JSON数组）
    related_flow_ids = Column(String, default="[]")              # 关联流转单ID（JSON数组）
    progress = Column(Integer, default=0)                        # 完成进度百分比（0-100）
    remark = Column(String, default="")                          # 备注说明
    creator_id = Column(String, nullable=False)                  # 创建人ID
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


# ══════════════════════════════════════════════════════════════════════════════
# M9: SOP 路由决策日志表
# ══════════════════════════════════════════════════════════════════════════════


class SOPRoutingLog(Base):
    """SOP 路由决策日志 —— 每次 Orchestrator 匹配都写入一条记录。

    用途：
      - 统计各 SOP 的命中率、误判率
      - 发现覆盖盲区（哪些用户意图从未命中任何 SOP）
      - 追踪 LLM 匹配质量
      - 为 SOP 目录优化提供数据支撑
    """
    __tablename__ = "sop_routing_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    # ── 时间维度 ──
    log_date = Column(String(10), nullable=False)       # 日期 YYYY-MM-DD
    log_time = Column(String(8), nullable=False)        # 时间 HH:MM:SS

    # ── 关联维度 ──
    user_id = Column(String, nullable=True)              # FK → users.id
    conversation_id = Column(String, nullable=True)      # FK → conversations.id
    message_id = Column(String, nullable=True)           # FK → messages.id

    # ── 内容维度 ──
    message_content = Column(String, nullable=False)     # 用户消息原文（截断 500 字）

    # ── 匹配维度 ──
    matched_sop_id = Column(String, nullable=True)       # 命中的 SOP 编号；未命中则为 NULL
    is_hit = Column(Boolean, default=False)                  # 0=未命中 1=命中
    match_confidence = Column(String(10), default="none")  # high / medium / low / none

    # ── 处理维度 ──
    fallback_action = Column(String(30), default="")     # 兜底动作
    llm_reasoning = Column(String, default="")           # LLM 匹配推理简述（≤200 字）
    execution_result = Column(String(20), default="")    # success / failed / skipped

    # ── 元数据 ──
    created_at = Column(String, default=_utc_now)


# ══════════════════════════════════════════════════════════════════════════════
# M11: 消息附件关联 + Agent 追踪（4 张新表）
# ══════════════════════════════════════════════════════════════════════════════


class MessageAttachment(Base):
    """消息附件关联表 —— 一条消息可以有多个附件（图片、文件、语音等）。

    附件物理存储路径: {storage_root}/{platform}/{YYYY-MM}/FIL-YYYYMMDD-NNNN.ext
    """
    __tablename__ = "message_attachments"

    id = Column(String, primary_key=True, default=_new_uuid)
    message_id = Column(String, ForeignKey("messages.id"), nullable=False)
    file_id = Column(String, ForeignKey("files.id"), nullable=True)
    attachment_type = Column(Integer, default=0)        # 0=未知 1=图片 2=文件 3=语音 4=视频 5=头像/引用消息
    file_url = Column(String(1000), default="")         # IM平台原始URL
    local_path = Column(String(1000), default="")       # 本地相对路径
    file_size = Column(Integer, default=0)               # 字节
    mime_type = Column(String(100), default="")
    thumbnail_url = Column(String(1000), default="")    # QQ缩略图URL
    created_at = Column(String, default=_utc_now)

    message = relationship("Message", back_populates="attachments_rel")
    file = relationship("File", back_populates="attachment_refs",
                        foreign_keys=[file_id])


class AgentReasoningLog(Base):
    """Agent 推理记录 —— 每次 MasterAgent.run() 写入一条。

    记录 Agent 处置一个用户请求的完整执行摘要。
    """
    __tablename__ = "agent_reasoning_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    # 关联维度
    message_id = Column(String, ForeignKey("messages.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=True)
    # 执行维度
    iteration_count = Column(Integer, default=0)
    elapsed_ms = Column(Integer, default=0)
    max_iterations_reached = Column(Boolean, default=False)
    # 路由维度
    matched_sop_id = Column(String(50), nullable=True)
    match_confidence = Column(String(10), default="none")  # high/medium/low/none
    is_compound = Column(Boolean, default=False)
    fallback = Column(Boolean, default=False)
    # 结果维度
    execution_result = Column(String(20), default="")       # success/failed/timeout
    reply_preview = Column(String(500), default="")
    error_message = Column(String(500), default="")
    # 步骤明细（JSON）
    steps_json = Column(Text, default="[]")
    created_at = Column(String, default=_utc_now)


class LLMInteractionLog(Base):
    """LLM 交互记录 —— 每次 LLM API 调用写入一条。

    用于统计 token 消耗、延迟、模型行为分析。
    """
    __tablename__ = "llm_interaction_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    # 关联维度
    reasoning_log_id = Column(String, ForeignKey("agent_reasoning_logs.id"), nullable=True)
    message_id = Column(String, ForeignKey("messages.id"), nullable=True)
    # 调用维度
    call_sequence = Column(Integer, default=0)
    call_type = Column(String(30), default="chat_with_tools")
        # chat / chat_json / chat_with_tools / guardian_review
    model = Column(String(100), default="")
    # 输入维度
    prompt_summary = Column(String(500), default="")
    user_message_count = Column(Integer, default=0)
    tool_count = Column(Integer, default=0)
    # 输出维度
    response_type = Column(String(20), default="")           # text / tool_call
    response_summary = Column(String(500), default="")
    finish_reason = Column(String(50), default="")           # stop/length/tool_calls/content_filter
    # 成本维度
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    created_at = Column(String, default=_utc_now)


class ToolCallLog(Base):
    """工具调用记录 —— 每次 Agent 执行工具时写入一条。

    用于分析工具使用模式、常见失败路径。
    """
    __tablename__ = "tool_call_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    # 关联维度
    reasoning_log_id = Column(String, ForeignKey("agent_reasoning_logs.id"), nullable=True)
    llm_interaction_id = Column(String, ForeignKey("llm_interaction_logs.id"), nullable=True)
    # 调用维度
    step_index = Column(Integer, default=0)
    tool_name = Column(String(100), nullable=False)
    tool_arguments = Column(Text, default="{}")
    tool_result_summary = Column(String(500), default="")
    is_success = Column(Boolean, default=True)
    error_message = Column(String(500), default="")
    elapsed_ms = Column(Integer, default=0)
    created_at = Column(String, default=_utc_now)


# ══════════════════════════════════════════════════════════════════════════════
# M12a: Hook 执行日志表
# ══════════════════════════════════════════════════════════════════════════════


class HookExecutionLog(Base):
    """Hook 执行日志（M12a）。

    每次 hook 执行写入一条记录，用于审计 hook 行为、追踪阻断模式。
    """
    __tablename__ = "hook_execution_logs"

    id = Column(String, primary_key=True, default=_new_uuid)
    hook_name = Column(String, nullable=False, index=True)   # "auth.admin_check"
    mount_point = Column(String, nullable=False)              # "before:execute"
    pipeline_run_id = Column(String, nullable=False, index=True)
    phase = Column(String, nullable=False)                    # "before" | "after" | "on_error"
    decision = Column(String, nullable=False)                 # "allow" | "warn" | "block"
    message = Column(String, default="")
    duration_ms = Column(Integer, default=0)
    metadata_json = Column(Text, default="{}")
    created_at = Column(String, nullable=False)


# ══════════════════════════════════════════════════════════════════════════════
# M12b: SOP 执行状态检查点表
# ══════════════════════════════════════════════════════════════════════════════


class SOPCheckpoint(Base):
    """SOP 执行状态快照（M12b）。

    在 user_interaction 节点执行前自动创建，
    用户确认/取消/超时后更新状态。

    借鉴 LangGraph checkpoint-per-superstep 模式：
    - 容器重启后待确认项不丢失
    - 超时后保留快照，支持"刚才的还有吗"恢复
    """
    __tablename__ = "sop_checkpoints"

    id = Column(String, primary_key=True, default=lambda: _new_id("chk"))
    thread_id = Column(String, nullable=False, index=True)       # conversation_id
    message_id = Column(String, nullable=True)                   # 触发确认的消息 ID
    sop_id = Column(String, nullable=False, index=True)           # SOP-002-REC
    node_name = Column(String, nullable=False)                   # wait_confirm
    pipeline_run_id = Column(String, nullable=True, index=True)  # M12a pipeline run ID

    # 完整状态快照（JSON）：PipelineContext 中与此确认相关的所有字段
    state_json = Column(Text, default="{}")

    # 状态生命周期
    status = Column(String, default="pending")  # pending | confirmed | cancelled | expired | resumed
    created_at = Column(String, nullable=False)
    confirmed_at = Column(String, nullable=True)
    cancelled_at = Column(String, nullable=True)
    resumed_at = Column(String, nullable=True)
    expires_at = Column(String, nullable=False, index=True)

    # 展示给用户的信息
    prompt_text = Column(String, default="")        # "请确认以下事件录入：..."
    confirm_keywords = Column(String, default="")   # JSON array: ["确认","对","ok"]
    cancel_keywords = Column(String, default="")    # JSON array: ["取消","不对"]

    # 确认后继续执行所需的上下文
    resume_context = Column(Text, default="{}")     # 恢复执行所需的上下文数据

    # 审计
    created_by = Column(String, nullable=True)       # user_id
    metadata_json = Column(Text, default="{}")
