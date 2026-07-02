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

from sqlalchemy import Column, String, Integer, BigInteger, ForeignKey, UniqueConstraint, Boolean, Text, Index, text
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
    人员类型不限于雇员——访客、临时工、供货商等均可记录，通过 org_category 区分。
    CHECK 约束（应用层强制）：phone/email/qq/wechat 不能同时为空。
    """
    __tablename__ = "users"
    # ── 原 users 字段（系统身份）──
    id = Column(String, primary_key=True, default=_new_uuid)
    username = Column(String(100), nullable=False)
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
    creator_id = Column(String, nullable=False)              # 创建人ID
    is_deleted = Column(Boolean, default=False)                  # 逻辑删除标记
    perm_list = Column(String, default="[]")                 # 权限集（JSON数组）
    org_category = Column(Integer, default=0)                # 组织类型标签（原 grouping，v2.0 改名只读不参与鉴权）：0=临时组 1=访客组 2=工程组 3=供货商 4=管理组
    permission_level = Column(Integer, default=1)            # 权限层级（v2.0 6级树形）：1=访客 2=参建执行 3=参建管理 4=建设主管 5=管理员 6=系统管理员
    supervisor_id = Column(String, nullable=True)            # 直接上级 ID（执行人升级/异常复核）
    company = Column(String, ForeignKey("company_info.id"), nullable=True)  # 隶属公司 FK→company_info.id（v2.0 改单 FK）
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)  # 所属项目 FK→projects.id
    position = Column(String, default="[]")                  # 本项目中负责岗位角色（JSON数组）
    long_term_memory = Column(String, default="")            # 用户长期记忆（Agent 自动维护的 Markdown 文本）
    conversation_summary = Column(String, default="")        # 历史对话摘要（LLM 压缩的对话要点）
    # ── 时间戳 ──
    created_at = Column(String, default=_utc_now, nullable=False)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now, nullable=False)
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

    # 全景节点图 V2 —— 文件溯源字段（需求文档 §6.1）
    source_module_id = Column(String(100), default="", comment="来源模块ID（节点ID/其他业务对象ID）")
    source_module_type = Column(String(50), default="", comment="来源模块类型：NODE_STARTUP_DOC/NODE_WORKLOAD_DOC/NODE_DELIVERABLE_DOC/NODE_ATTACHMENT")


# ══════════════════════════════════════════════════════════════════════════════
# 新增表（对齐需求文档 清单-数据库表.md）
# ══════════════════════════════════════════════════════════════════════════════


class CompanyInfo(Base):
    """公司基础信息表 —— 对应需求文档 company_info。

    v2.0 权限系统扩展（需求 §4）：新增 type/status/scope/partners/parent_id/department/function_scope，
    承载单位权限范围属性，支撑单位归属自动授权与越权检测。
    """
    __tablename__ = "company_info"
    id = Column(String, primary_key=True, default=_new_uuid)
    company_name = Column(String(256), nullable=False)       # 公司全称
    unified_code = Column(String(50), nullable=False)        # 统一社会信用代码（18位）
    business_desc = Column(String, default="")               # 业务描述
    project_leader_id = Column(String, nullable=False)       # 项目负责人ID → employee.id
    creator_id = Column(String, nullable=False)              # 创建者ID → employee.id
    # ── v2.0 权限系统扩展字段（需求 §4.1）──
    type = Column(String(50), default="")                    # 企业类型：建设单位/设计单位/总包/分包/监理/供应商
    status = Column(String(50), default="active")            # 履约状态：投标中/履约中/已退场
    scope = Column(String, default="[]")                     # 承包范围 JSON ["景观","1标段"]
    partners = Column(String, default="[]")                  # 对接公司ID JSON [company_id, ...]
    parent_id = Column(String, ForeignKey("company_info.id"), nullable=True)  # 上级公司（分包→总包）
    department = Column(String, default="[]")                # 部门 JSON ["设计部","工程部"]
    function_scope = Column(Text, default="{}")              # 职能-全景节点映射 JSON（需求 §4.1.1）
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
        # chat / chat_json / chat_with_tools
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


# ══════════════════════════════════════════════════════════════════════════════
# 权限架构 v1.2: SOP 鉴权两层结构
#   第一层：PermissionGroup - 权限组（企业+部门 两层归属）
#   第二层：SOPBusinessFlow - SOP 业务流特征信息（关联权限组）
# ══════════════════════════════════════════════════════════════════════════════


class PermissionGroup(Base):
    """权限组表（第一层：组织架构维度）。

    权限架构 v1.2: SOP 鉴权第一层 - 组织架构维度的权限分组。
    设计为两层归属分类：
      - 第一层：企业（建设单位、设计单位、总包、分包、监理、供应商）
      - 第二层：部门（设计部、工程部、成本部、采购部等，建设单位需细分）

    企业分组设计原则：
      - 一般企业只做简单的管理组与业务组划分
      - 建设单位需细分部门以便权限细分
    """
    __tablename__ = "permission_groups"

    id = Column(String, primary_key=True, default=_new_uuid)

    # ── 基本信息 ──
    name = Column(String(100), nullable=False)       # 权限组名称，如"建设单位-设计部"
    code = Column(String(50), unique=True, nullable=False)  # 权限组编码，如"OWNER-DESIGN"
    description = Column(String(500), default="")    # 一句话功能描述

    # ── 两层归属分类 ──
    company_type = Column(String(50), nullable=False)  # 企业类型：建设单位/设计单位/总包/分包/监理/供应商
    department = Column(String(100), default="")       # 部门归属（建设单位需细分）

    # ── 组织层级 ──
    org_level = Column(Integer, default=1)           # 组织层级 1=企业 2=部门 3=小组
    parent_group_id = Column(String, nullable=True)   # 父权限组ID（支持层级嵌套）

    # ── 权限配置 ──
    allowed_sop_types = Column(String, default="[]")  # 允许的 SOP 类型（JSON数组）
    min_permission_level = Column(Integer, default=1)  # 最低权限层级要求（6级树形继承，1-6）

    # ── 状态与审计 ──
    status = Column(String(50), default="active")
    is_system = Column(Boolean, default=False)           # 是否系统内置权限组
    creator_id = Column(String, nullable=True)
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class SOPBusinessFlow(Base):
    """SOP 业务流特征信息表（第二层：业务流维度）。

    权限架构 v1.2: SOP 鉴权第二层 - 业务流特征信息。
    每个 SOP 对应一条记录，定义其业务属性与权限要求。

    鉴权逻辑：
      1. 用户所属权限组（PermissionGroup）决定可见的 SOP 范围
      2. SOP 关联的权限组定义谁可以访问
      3. 部门权限过滤：只有归属部门匹配的用户才能看到对应的 SOP
    """
    __tablename__ = "sop_business_flows"

    id = Column(String, primary_key=True, default=_new_uuid)

    # ── SOP 基本信息 ──
    sop_id = Column(String(50), unique=True, nullable=False)    # SOP 编号，如"SOP-001-REC"
    sop_file_name = Column(String(200), nullable=False)          # SOP 文件名
    display_name = Column(String(200), nullable=False)           # 业务名称，如"会议纪要录入"
    description = Column(String(500), default="")                # 一句话功能描述

    # ── 业务分类 ──
    sop_type = Column(String(50), nullable=False)      # SOP 类型：REC/FILE/QRY/FLOW/SYS
    category = Column(String(100), default="")         # 业务分类，如"工程记录"/"项目管理"

    # ── 权限组归属（多对多通过中间表，此处为默认权限组）──
    default_permission_group_id = Column(String, ForeignKey("permission_groups.id"), nullable=True)

    # ── 权限要求 ──
    min_permission_level = Column(Integer, default=1)  # 最低权限层级（6级树形继承，1-6）
    require_company_match = Column(Boolean, default=True)   # 是否需要企业类型匹配
    require_department_match = Column(Boolean, default=False)  # 是否需要部门匹配

    # ── 可见性控制 ──
    is_public = Column(Boolean, default=False)            # 是否公开（所有用户可见）
    allowed_company_types = Column(String, default="[]")  # 允许的企业类型（JSON数组）
    allowed_departments = Column(String, default="[]")    # 允许的部门（JSON数组）
    security_level = Column(String(20), default="PUBLIC")  # v2.0 密级 PUBLIC/INTERNAL/PRIVATE/CONFIDENTIAL（需求 §3.1）
    required_node_ids = Column(String, default="[]")       # v2.0 关联全景节点ID JSON（节点范围鉴权，需求 §4）

    # ── 版本与状态 ──
    version = Column(String(50), default="v1.0")
    is_active = Column(Boolean, default=True)
    is_deprecated = Column(Boolean, default=False)
    deprecate_reason = Column(String(500), default="")

    # ── 审计 ──
    creator_id = Column(String, nullable=True)
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    # ── 关联 ──
    permission_group = relationship("PermissionGroup", foreign_keys=[default_permission_group_id])


class SOPPermissionBinding(Base):
    """SOP 权限组绑定表（多对多关联）。

    一个 SOP 可以绑定到多个权限组，一个权限组可以包含多个 SOP。
    """
    __tablename__ = "sop_permission_bindings"

    id = Column(String, primary_key=True, default=_new_uuid)
    sop_business_flow_id = Column(String, ForeignKey("sop_business_flows.id"), nullable=False)
    permission_group_id = Column(String, ForeignKey("permission_groups.id"), nullable=False)

    # 绑定类型
    binding_type = Column(String(50), default="allow")  # allow/deny

    # 审计
    creator_id = Column(String, nullable=True)
    created_at = Column(String, default=_utc_now)
    is_deleted = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("sop_business_flow_id", "permission_group_id", name="uq_sop_permission_binding"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# 计划任务系统 (Scheduled Task Module)
#   四张新表：plan_task_templates / plan_task_instances / plan_task_logs / plan_task_deliverables
#   v2.1: 含异常复核状态、执行人升级、LLM 推算失败标记
# ══════════════════════════════════════════════════════════════════════════════


class PlanTaskTemplate(Base):
    """计划任务模板表 —— 定义循环或一次性任务的模板。

    模板由发起人创建，激活后由调度机根据 deadline_rule 自动生成实例。
    """
    __tablename__ = "plan_task_templates"

    __table_args__ = (
        # 支持调度机 get_active_cycle_templates 查询（status + task_type 过滤）
        Index("idx_ptt_status_type", "status", "task_type"),
    )

    id = Column(String, primary_key=True, default=_new_uuid)
    template_no = Column(String(50), unique=True, nullable=False)   # 模板编号 TPL-YYYYMMDD-NNNN
    name = Column(String(200), nullable=False)                      # 模板名称
    description = Column(String, default="")                        # 模板描述

    # ── 任务定义 ──
    initiator_id = Column(String, ForeignKey("users.id"), nullable=False)  # 发起人
    executor_id = Column(String, ForeignKey("users.id"), nullable=True)    # 执行人（可为空=待指派）
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)  # 关联项目

    # ── 调度规则 ──
    task_type = Column(String(20), nullable=False, default="ONCE")  # ONCE / WEEKLY / MONTHLY
    deadline_rule = Column(String(500), default="")                 # 自然语言截止描述，如"每周五17:00"、"每月20日"

    # ── 验收标准 ──
    verification_standard = Column(String, default="{}")            # JSON 核验规则

    # ── 工作流关联 ──
    workflow_definition_key = Column(String(100), default="")       # 关联工作流定义键

    # ── 状态与审计 ──
    status = Column(String(20), default="DRAFT")                    # DRAFT / ACTIVE / INACTIVE
    creator_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    # ── LLM 推算失败标记（v1.5-fix: 模板级别）──
    llm_calculation_failed = Column(Boolean, default=False)  # LLM 推算截止时间是否失败
    llm_failure_notified = Column(Boolean, default=False)    # 是否已通知发起人人工处理


class PlanTaskInstance(Base):
    """计划任务实例表 —— 每个具体的任务实例。

    五态状态机（+ 异常复核）：WAITING / SUBMITTED / RETURNED /
    CONFIRMED / ARCHIVED / CANCELLED / ANOMALY_PENDING_REVIEW
    """
    __tablename__ = "plan_task_instances"

    __table_args__ = (
        # 幂等唯一约束：同一模板同一周期只能有一个非取消实例（计划 §3.6）
        Index(
            "uq_pti_period", "template_id", "period_key",
            unique=True, postgresql_where=text("status != 'CANCELLED'"),
        ),
        # 调度机核心查询：按状态+截止时间
        Index("idx_pti_status_deadline", "status", "deadline_at"),
        # 按执行人/项目查询
        Index("idx_pti_executor_status", "executor_id", "status"),
        Index("idx_pti_project", "project_id", "status"),
        # 计划外事件查询
        Index("idx_pti_unscheduled", "is_unscheduled", "created_at"),
        # LLM 失败待处理查询
        Index("idx_pti_llm_failed", "llm_calculation_failed", "llm_failure_notified"),
    )

    id = Column(String, primary_key=True, default=_new_uuid)
    instance_no = Column(String(50), unique=True, nullable=False)   # 实例编号 PTI-YYYYMMDD-NNNN
    template_id = Column(String, ForeignKey("plan_task_templates.id"), nullable=True)

    # ── 幂等键 ──
    period_key = Column(String(100), default="")                    # "2024-W25", "2024-M06", "2024-07-15"

    # ── 任务核心字段 ──
    title = Column(String(500), nullable=False)
    description = Column(String, default="")
    initiator_id = Column(String, ForeignKey("users.id"), nullable=False)   # 发起人
    executor_id = Column(String, ForeignKey("users.id"), nullable=True)     # 执行人
    project_id = Column(String, ForeignKey("projects.id"), nullable=True)
    phase_code = Column(String(100), nullable=True)                         # 关联项目阶段编码

    # ── 时间字段 ──
    deadline_at = Column(String, nullable=True)           # 截止时间（ISO8601）
    reminded_at = Column(String, nullable=True)           # 最后提醒时间
    escalated_at = Column(String, nullable=True)          # 升级给上级的时间（P2）

    # ── 执行人升级（P2）──
    original_executor_id = Column(String, ForeignKey("users.id"), nullable=True)  # 原执行人
    escalation_reason = Column(String, default="")       # 升级原因（离职/失能等）

    # ── 验收标准 ──
    verification_standard = Column(String, default="{}")  # JSON 核验规则

    # ── 状态机 ──
    # WAITING / SUBMITTED / RETURNED / CONFIRMED / ARCHIVED / CANCELLED / ANOMALY_PENDING_REVIEW
    status = Column(String(30), default="WAITING")

    # ── 时间戳 ──
    submitted_at = Column(String, nullable=True)          # 提交时间
    confirmed_at = Column(String, nullable=True)          # 确认时间
    archived_at = Column(String, nullable=True)           # 归档时间

    # ── 工作流关联 ──
    workflow_instance_id = Column(String, nullable=True)  # 关联工作流实例ID

    # ── 异常标记 ──
    is_unscheduled = Column(Boolean, default=False)       # 是否计划外事件
    anomaly_reason = Column(String(500), default="")           # 异常原因

    # ── LLM 推算失败标记 ──
    llm_calculation_failed = Column(Boolean, default=False)  # LLM 推算截止时间是否失败
    llm_failure_notified = Column(Boolean, default=False)    # 是否已通知发起人人工处理

    # ── 审计 ──
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    # ── 关联 ──
    template = relationship("PlanTaskTemplate", foreign_keys=[template_id])


class PlanTaskLog(Base):
    """计划任务状态变更日志表 —— 记录全生命周期每次状态变更的快照。"""
    __tablename__ = "plan_task_logs"

    __table_args__ = (
        # 按实例查日志（instance_id 单列已有 index=True，此处补复合索引支持时间范围查）
        Index("idx_ptl_instance", "instance_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=_new_uuid)
    instance_id = Column(String, ForeignKey("plan_task_instances.id"), nullable=False, index=True)

    # ── 状态变更 ──
    from_status = Column(String(20), nullable=True)       # 变更前状态
    to_status = Column(String(20), nullable=False)        # 变更后状态
    operator_id = Column(String, ForeignKey("users.id"), nullable=True)  # 操作人
    reason = Column(String, default="")                   # 变更原因

    # ── 快照 ──
    snapshot = Column(Text, default="{}")                 # JSON 全量快照（实例关键字段）

    # ── 审计 ──
    created_at = Column(String, default=_utc_now)


class PlanTaskDeliverable(Base):
    """计划任务成果表 —— 执行者提交的任务成果。"""
    __tablename__ = "plan_task_deliverables"

    __table_args__ = (
        # 按提交者查询成果
        Index("idx_ptd_submitted", "submitted_by", "submitted_at"),
    )

    id = Column(String, primary_key=True, default=_new_uuid)
    instance_id = Column(String, ForeignKey("plan_task_instances.id"), nullable=False, index=True)

    # ── 成果内容 ──
    type = Column(String(20), nullable=False, default="TEXT")  # FILE / TEXT / JSON
    content = Column(Text, default="")                         # 文本内容
    file_url = Column(String(1000), default="")                # 文件URL
    file_name = Column(String(500), default="")                # 文件名

    # ── 提交信息 ──
    submitted_by = Column(String, ForeignKey("users.id"), nullable=False)
    submitted_at = Column(String, default=_utc_now)

    # ── 审计 ──
    created_at = Column(String, default=_utc_now)


# ══════════════════════════════════════════════════════════════════════════════
# 权限管理系统 (Permission Module) — v2.0（需求-完整版）
#   8 张新表：
#     permission_def / permission_grants / permission_requests / permission_audit_log
#     / public_field_registry / pending_data / data_masking_rules / permission_review_tasks
# ══════════════════════════════════════════════════════════════════════════════


class PermissionDef(Base):
    """权限码定义表（需求 §6）—— 权限分层编码注册。

    编码格式: [资源类型]-[密级]-[项目ID]-[节点ID]-[资源ID]
    """
    __tablename__ = "permission_def"
    id = Column(String, primary_key=True, default=_new_uuid)
    perm_code = Column(String(256), unique=True, nullable=False)   # 权限编码
    resource_type = Column(String(3), nullable=False)              # DOC/DB/SOP/MSG/SYS
    security_level = Column(String(12), nullable=False)            # PUBLIC/INTERNAL/PRIVATE/CONFIDENTIAL
    project_id = Column(String, default="*")                       # 项目标识，* 表示全部
    node_id = Column(String, default="*")                          # 全景节点标识，* 表示全部
    resource_id = Column(String, default="*")                      # 具体资源标识，* 表示全部
    description = Column(String(500), default="")
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class PermissionGrant(Base):
    """授权记录表（需求 §5、§6.2）—— 3 种授权形式 AUTO/TEMP/PERMANENT。"""
    __tablename__ = "permission_grants"
    id = Column(String, primary_key=True, default=_new_uuid)
    grant_no = Column(String(50), unique=True, nullable=False)     # PGR-YYYYMMDD-NNNN
    grantee_id = Column(String, ForeignKey("users.id"), nullable=False)  # 被授权人
    grantor_id = Column(String, ForeignKey("users.id"), nullable=True)   # 授权人（AUTO 时可空）
    perm_code = Column(String(256), nullable=False)                # 权限编码
    grant_type = Column(String(12), nullable=False)                # AUTO/TEMP/PERMANENT
    operations = Column(String, default='["read"]')                # JSON 操作列表
    grant_time = Column(String, default=_utc_now)
    expire_time = Column(String, nullable=True)                    # 过期时间（TEMP 必填）
    status = Column(String(20), default="ACTIVE")                  # ACTIVE/REVOKED/EXPIRED
    revoke_time = Column(String, nullable=True)
    revoke_reason = Column(String(500), default="")
    remark = Column(String(500), default="")                       # 授权原因（PERMANENT 必填）
    client_ip = Column(String(64), default="")
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    __table_args__ = (
        Index("idx_pg_grantee_status", "grantee_id", "status"),
    )


class PermissionRequest(Base):
    """权限申请审批表（需求 §9）—— 轻量审批流载体，协同待办预留对接。"""
    __tablename__ = "permission_requests"
    id = Column(String, primary_key=True, default=_new_uuid)
    request_no = Column(String(50), unique=True, nullable=False)   # PRQ-YYYYMMDD-NNNN
    requester_id = Column(String, ForeignKey("users.id"), nullable=False)  # 申请人
    perm_code = Column(String(256), nullable=False)
    request_type = Column(String(20), nullable=False)              # TEMP_GRANT/UNIT_BIND/LEVEL_UP/ANOMALY_DATA
    reason = Column(String(1000), default="")
    status = Column(String(20), default="PENDING")                 # PENDING/APPROVED/REJECTED/EXPIRED/ESCALATED
    current_approver_id = Column(String, ForeignKey("users.id"), nullable=True)  # 当前审批人
    approval_level = Column(Integer, default=1)                    # 当前审批层级 1/2
    priority = Column(String(20), default="NORMAL")                # NORMAL/HIGH/URGENT
    expire_at = Column(String, nullable=True)                      # 申请过期时间
    approved_at = Column(String, nullable=True)
    approver_id = Column(String, ForeignKey("users.id"), nullable=True)  # 最终审批人
    approval_remark = Column(String(500), default="")
    source_data = Column(Text, default="{}")                       # JSON 额外上下文
    agent_issue_id = Column(String, nullable=True)                 # 协同待办对接 ID（预留）
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    __table_args__ = (
        Index("idx_prq_approver_status", "current_approver_id", "status"),
    )


class PermissionAuditLog(Base):
    """授权审计日志表（需求 §8.1）—— ⚠ 仅 INSERT，禁止 UPDATE/DELETE。

    通过 PostgreSQL 触发器强制不可篡改（迁移脚本创建触发器）。
    """
    __tablename__ = "permission_audit_log"
    log_id = Column(BigInteger, primary_key=True, autoincrement=True)  # BIGSERIAL
    event_time = Column(String, default=_utc_now)
    grantor_id = Column(String, nullable=True)                     # 授权人/操作人
    grantee_id = Column(String, nullable=True)                     # 被授权人/被审计人
    perm_code = Column(String(256), default="")
    grant_type = Column(String(32), default="")                    # AUTO/TEMP/PERMANENT
    duration = Column(Integer, nullable=True)                      # 时长秒（PERMANENT 为 NULL）
    session_id = Column(String(128), nullable=True)
    operation_type = Column(String(32), nullable=False)            # GRANT/REVOKE/EXPIRE/ACCESS_DENIED/ACCESS_CHECK
    client_ip = Column(String(64), default="")
    user_agent = Column(Text, default="")
    remark = Column(String(512), default="")

    __table_args__ = (
        Index("idx_pal_grantee_time", "grantee_id", "event_time"),
        Index("idx_pal_op_time", "operation_type", "event_time"),
    )


class PublicFieldRegistry(Base):
    """公开字段白名单（设计文档 §3.4）—— 模型-字段级公开信息登记。"""
    __tablename__ = "public_field_registry"
    id = Column(String, primary_key=True, default=_new_uuid)
    project_id = Column(String, nullable=True)                     # 关联项目（NULL=全局公开）
    model_name = Column(String(100), nullable=False)               # "Project"/"Event"
    field_name = Column(String(100), nullable=False)               # "name"/"area"
    description = Column(String(500), default="")
    created_at = Column(String, default=_utc_now)
    is_deleted = Column(Boolean, default=False)


class PendingData(Base):
    """越权写入暂存表（需求 §7.2.2）—— 越权数据暂存待主管审批。"""
    __tablename__ = "pending_data"
    id = Column(String, primary_key=True, default=_new_uuid)
    pending_no = Column(String(50), unique=True, nullable=False)   # PND-YYYYMMDD-NNNN
    user_id = Column(String, ForeignKey("users.id"), nullable=False)  # 提交人
    data_type = Column(String(50), nullable=False)                 # event/task/file
    data_content = Column(Text, default="{}")                      # 数据内容快照 JSON
    exception_reason = Column(String(1000), default="")
    target_node_id = Column(String, default="")
    approver_id = Column(String, ForeignKey("users.id"), nullable=True)  # 待审批主管
    status = Column(String(20), default="PENDING")                 # PENDING/APPROVED/REJECTED/CLEANED
    expire_time = Column(String, nullable=True)                    # 默认创建后 7 天
    request_id = Column(String, nullable=True)                     # 关联 permission_requests.id
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)

    __table_args__ = (
        Index("idx_pnd_approver_status", "approver_id", "status"),
    )


class DataMaskingRule(Base):
    """脱敏规则表（需求 §10.1）—— 敏感字段按 permission_level 脱敏。"""
    __tablename__ = "data_masking_rules"
    id = Column(String, primary_key=True, default=_new_uuid)
    rule_code = Column(String(50), unique=True, nullable=False)    # PHONE/ID_CARD/AMOUNT/CONTACT
    field_pattern = Column(String(200), nullable=False)            # 匹配字段名正则
    mask_type = Column(String(50), nullable=False)                 # MIDDLE_4/MIDDLE_10/RANGE/NAME
    min_level_to_view = Column(Integer, default=5)                 # 可见明文的最低 permission_level
    params = Column(Text, default="{}")                            # JSON 参数（如金额范围）
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


class PermissionReviewTask(Base):
    """定期评审任务表（需求 §12.2）—— 季度权限评审。"""
    __tablename__ = "permission_review_tasks"
    id = Column(String, primary_key=True, default=_new_uuid)
    review_no = Column(String(50), unique=True, nullable=False)    # REV-YYYYQQ-NN
    review_period = Column(String(20), nullable=False)             # "2026Q2"
    scope_type = Column(String(20), default="ALL")                 # ALL/UNIT/LEVEL
    scope_value = Column(String, default="")
    assignee_id = Column(String, ForeignKey("users.id"), nullable=True)  # 评审负责人
    status = Column(String(20), default="PENDING")                 # PENDING/IN_PROGRESS/COMPLETED
    deadline = Column(String, nullable=True)
    result_summary = Column(Text, default="{}")                    # JSON 评审结果
    created_at = Column(String, default=_utc_now)
    updated_at = Column(String, default=_utc_now, onupdate=_utc_now)
    is_deleted = Column(Boolean, default=False)


# ============================================================================
# 全景节点图 V2 — 5 张表（Phase 1-1）
# 基于需求文档 §3.2–§3.6
# ============================================================================


class ProjectNode(Base):
    """节点主表 —— 需求文档 §3.2 project_nodes。

    三态状态机：CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED。
    支持父子层级（parent_node_id 自引用），嵌套深度上限 3 层。
    多项目隔离：project_id 为必填。
    """
    __tablename__ = "project_nodes"

    # ── 必填业务字段（6个）──
    project_id = Column(String(100), nullable=False, comment="项目归属ID（FK→projects.id）")
    node_id = Column(String(100), nullable=False, comment="节点编号（业务主键），例：SG-JG-01-2026")
    node_name = Column(String(500), nullable=False, comment="节点名称")
    owner_dept_id = Column(String(100), nullable=False, default="项目总", comment="主责条线（FK→company_info.id）")
    related_company_id = Column(String(100), nullable=False, default="建设单位", comment="关联单位（FK→company_info.id）")
    deadline = Column(String(50), nullable=False, comment="截止时间（ISO8601）")

    # ── 选填业务字段（6个）──
    land_parcel_id = Column(String(100), default="", comment="关联地块ID")
    remark = Column(Text, default="", comment="备注")
    parent_node_id = Column(String(100), default="", comment="父节点ID（FK→project_nodes.node_id）")
    stage_id = Column(Integer, default=0, comment="所属阶段ID（对齐 projects.lifecycle_stage）")
    child_weight = Column(String, default="1.0000", comment="作为子节点时在父节点中的权重（DECIMAL(5,4)，存为字符串避免精度问题）")
    startup_doc_id = Column(String(100), default="", comment="启动文档记录ID（FK→files.id）")

    # ── 系统字段（10个）──
    creator_id = Column(String(100), nullable=False, comment="录入人ID")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="录入时间（ISO8601）")
    approver_id = Column(String(100), default="", comment="批准人ID")
    approved_at = Column(String(50), default="", comment="批准时间（ISO8601）")
    completed_at = Column(String(50), default="", comment="完成时间（ISO8601）")
    is_discarded = Column(Boolean, default=False, comment="是否被废弃")
    progress = Column(String, default="0.00", comment="整体进度（百分比 0.00-100.00，存为字符串避免精度问题）")
    status = Column(String(20), default="NOT_ACTIVATED", comment="当前状态：NOT_ACTIVATED / CONDITIONS_NOT_MET / IN_PROGRESS / COMPLETED")
    sort_order = Column(Integer, default=0, comment="排序序号")
    updated_at = Column(String(50), nullable=False, default=_utc_now, onupdate=_utc_now, comment="最后更新时间（ISO8601）")

    # ── 主键 ──
    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_nodes_project", "project_id"),
        Index("idx_nodes_status", "status"),
        Index("idx_nodes_stage", "stage_id"),
        Index("idx_nodes_owner", "owner_dept_id"),
        Index("idx_nodes_parent", "parent_node_id"),
    )


class NodeDependency(Base):
    """前置依赖表 —— 需求文档 §3.3 node_dependencies。

    核心机制：依赖不锁定节点，锁定具体成果文件。
    下游节点只需依赖文件就绪即可启动，无需等待上游节点整体完成。
    权重支持阻塞场景（权重 999 的人工依赖）。
    """
    __tablename__ = "node_dependencies"

    node_id = Column(String(100), nullable=False, comment="本节点（下游节点，FK→project_nodes.node_id）")
    depends_on_deliverable_id = Column(String(100), nullable=False, comment="依赖的成果ID（FK→node_deliverables.deliverable_id）")
    depends_on_node_id = Column(String(100), nullable=False, comment="成果所属上游节点ID（冗余字段，FK→project_nodes.node_id）")
    dependency_type = Column(String(20), nullable=False, default="DELIVERABLE", comment="依赖类型：DELIVERABLE / TIME")
    weight = Column(String, nullable=False, default="1.0000", comment="权重（0.0000-1.0000，存为字符串）")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="创建时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        UniqueConstraint("node_id", "depends_on_deliverable_id", name="uq_dep_node_deliverable"),
        Index("idx_ndep_node", "node_id"),
        Index("idx_ndep_deliverable", "depends_on_deliverable_id"),
    )


class NodeDeliverable(Base):
    """产出成果表 —— 需求文档 §3.4 node_deliverables。

    每个节点可定义多个产出成果，每个成果有目标量和当前量。
    成果完成度 = current_amount / target_amount。
    必需成果全部 100% 完成 → 节点状态自动流转至「已完成」。
    """
    __tablename__ = "node_deliverables"

    deliverable_id = Column(String(100), nullable=False, comment="成果编号（业务主键）")
    node_id = Column(String(100), nullable=False, comment="所属节点ID（FK→project_nodes.node_id）")
    deliverable_name = Column(String(500), nullable=False, comment="成果名称")
    target_amount = Column(String, nullable=False, comment="目标量（DECIMAL(12,2)，存为字符串）")
    current_amount = Column(String, nullable=False, default="0.00", comment="当前量（DECIMAL(12,2)，存为字符串）")
    unit = Column(String(50), nullable=False, comment="量纲（份/吨/平方米...）")
    is_required = Column(Boolean, nullable=False, default=True, comment="是否必需成果")
    file_id = Column(String(100), default="", comment="关联文件ID（FK→files.id）")
    completed_at = Column(String(50), default="", comment="完成时间（ISO8601）")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="创建时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_ndel_node", "node_id"),
    )


class NodeAccessibleFile(Base):
    """节点可见文件中间表 —— 需求文档 §3.5 node_accessible_files。

    M:N 关系，替代 JSON 数组方案，支持索引查询和权限审计。
    文件只对与之关联的节点的企业用户可见。
    """
    __tablename__ = "node_accessible_files"

    node_id = Column(String(100), nullable=False, comment="节点ID（FK→project_nodes.node_id）")
    file_id = Column(String(100), nullable=False, comment="文件ID（FK→files.id）")
    added_by = Column(String(100), nullable=False, comment="授权人ID")
    added_at = Column(String(50), nullable=False, default=_utc_now, comment="授权时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        UniqueConstraint("node_id", "file_id", name="uq_naf_node_file"),
        Index("idx_naf_node", "node_id"),
        Index("idx_naf_file", "file_id"),
    )


class NodeEvent(Base):
    """事件总线持久化表 —— 需求文档 §3.6 node_events。

    所有节点操作与变更记录，只增不改（immutable）。
    软删除也记录为 DELETE 事件，原始记录永久保留。
    """
    __tablename__ = "node_events"

    event_id = Column(String(100), nullable=False, comment="事件唯一ID")
    node_id = Column(String(100), nullable=False, comment="关联节点ID（FK→project_nodes.node_id）")
    event_type = Column(String(50), nullable=False, comment="事件类型枚举")
    old_value = Column(Text, default="", comment="变更前值（JSON快照）")
    new_value = Column(Text, default="", comment="变更后值（JSON快照）")
    operator_id = Column(String(100), default="", comment="操作人ID（系统自动触发则为空）")
    remark = Column(Text, default="", comment="操作说明/备注")
    created_at = Column(String(50), nullable=False, default=_utc_now, comment="事件发生时间（ISO8601）")

    id = Column(String, primary_key=True, default=_new_uuid)

    __table_args__ = (
        Index("idx_nev_node", "node_id"),
        Index("idx_nev_type", "event_type"),
        Index("idx_nev_created", "created_at"),
    )
