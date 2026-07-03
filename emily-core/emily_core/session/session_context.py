"""SessionContext —— Session 知识灌注数据类（蓝图 §4.3.1）。

Session 创建时只加载"必须立即获取"的上下文，其余懒加载。原则（最小化灌注）：
  ✅ 直接拿：最近对话（滑动窗口）、用户摘要（偏好+历史摘要）
  ✅ 一级压缩摘要：SOP 目录、工具目录（仅大类名 + 一句话功能描述）
  ❌ 不拿：SOP 全文、完整 schema、详细工具参数 → 懒加载

**权限架构调整（v1.2）**：
  权限信息不再直接灌注到 Session-Agent，改为在 SessionContext 内用专门字段存放。
  WorkItemAgent 对此保持只读权限，用于获取状态信息与权限列表进行鉴权检查。

本数据类承载这些"已灌注"的最小上下文。真实的摘要生成器 / 懒加载触发
（蓝图 §4.3.1 懒加载触发表）属 Phase B/C，此处为骨架字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PermissionSnapshot:
    """权限快照 —— Session 创建时一次性加载，后续所有访问基于此快照。

    设计说明：
    - 不直接灌注到 Session-Agent，避免上下文污染和安全边界不清晰
    - WorkItemAgent 通过只读访问获取权限信息进行鉴权
    - 数据库访问权限限定通过权限可见范围白名单机制实现

    v2.0 权限系统改造（需求-完整版）：
    - grouping → permission_level（6 级树形继承，值域 1-6，需求 §2）
    - 新增 permissions_loaded_at / permission_version（轻量变更检测，设计文档 v1.5）
    - 新增 authorized_node_ids（单位权限范围关联的全景节点 ID）
    - 新增 granted_codes / denied_codes（权限编码，支持通配符匹配，需求 §6）
    """

    # ── 权限层级（6 级树形继承，需求 §2）──
    permission_level: int = 1           # 1:访客 2:参建执行 3:参建管理 4:建设主管 5:管理员 6:系统管理员

    # ── 企业归属 ──
    company_id: str = ""                # 所属公司 ID
    company_type: str = ""              # 公司类型：建设单位/设计单位/总包/分包/监理/供应商
    company_name: str = ""              # 公司名称
    department: str = ""                # 部门归属（建设单位细分：设计部/工程部/成本部等）

    # ── 项目与范围 ──
    project_ids: list[str] = field(default_factory=list)   # 参与的项目
    partner_ids: list[str] = field(default_factory=list)   # 对接公司（可见对方部分数据）
    scopes: list[str] = field(default_factory=list)        # 承包范围 ["景观", "绿化", ...]

    # ── SOP 访问白名单 ──
    sop_allow: list[str] = field(default_factory=list)     # 可用的 SOP 白名单

    # ── 数据库表级权限 ──
    db_perms: dict[str, str] = field(default_factory=dict)  # {"event": "read_write", "project": "read"}

    # ── 信息访问级别（4 级密级，需求 §3.1）──
    info_level: str = "public"          # public / internal / private / confidential

    # ── 组织架构 ──
    supervisor_id: str = ""             # 直接上级（异常审核人）
    org_group: str = ""                 # 企业内分组：管理组 / 业务组

    # ── 权限编码（需求 §6，支持通配符 * 与前缀匹配）──
    granted_codes: list[str] = field(default_factory=list)   # 临时/永久授权持有的权限编码
    denied_codes: list[str] = field(default_factory=list)    # 显式拒绝的权限编码（优先级最高）

    # ── 节点范围（单位权限范围关联的全景节点 ID，需求 §4）──
    authorized_node_ids: list[str] = field(default_factory=list)

    # ── 变更检测（设计文档 v1.5）──
    permissions_loaded_at: str = ""     # 快照加载时间戳（ISO8601）
    permission_version: int = 0         # 权限版本号（变更时递增，Hook 处理前对比）

    # ── 扩展 ──
    extra_perms: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionContext:
    """Session 最小化灌注上下文。"""

    # ── 标识 ──
    conversation_id: str = ""
    user_id: str = ""
    user_name: str = ""

    # ── 1. 多轮对话记忆（v2 — OpenAI 格式消息列表）──
    message_history: list[dict] = field(default_factory=list)
    # OpenAI 格式消息列表，不含 system prompt（调用时拼接）：
    # [{"role":"user","content":"...","name":"张工"},
    #  {"role":"assistant","content":"..."},
    #  {"role":"user","content":"...","name":"张工"}, ...]
    #
    # 压缩后会在开头插入一条摘要消息：
    # {"role":"user","content":"[对话历史摘要] ...", "name":"system"}

    # ── 2. 用户长期记忆（压缩摘要版）──
    user_preferences: str = ""           # 例："喜欢简洁回复, 负责消防"
    history_summary: str = ""            # 例："上周讨论过材料进场"

    # ── 3. SOP 目录摘要（一级压缩：大类名 + 一句话）──
    sop_catalog_summary: str = ""

    # ── 4. 工具目录摘要（一级压缩：大类名 + 一句话）──
    tool_catalog_summary: str = ""

    # ── 5. 数据库结构摘要（懒加载，仅一级表名列表）──
    schema_summary: str = ""

    # ── 6. 当前日期时间 ──
    current_datetime: str = ""

    # ── 权限快照（Session 创建时一次性注入，只读）──
    # 注意：不再直接灌注到 Session-Agent，WorkItemAgent 通过只读访问获取
    permissions: PermissionSnapshot = field(default_factory=PermissionSnapshot)

    # ── 兼容字段：保留 perm_list 作为过渡，后续逐步移除 ──
    perm_list: list[dict] = field(default_factory=list)

    # ── 组装后的 system prompt（懒构建）──
    system_prompt: str = ""

    # ── 扩展 ──
    extra: dict[str, Any] = field(default_factory=dict)

    # ── 只读权限访问方法（供 WorkItemAgent 调用）──
    def get_permission_snapshot(self) -> PermissionSnapshot:
        """获取权限快照（只读访问）。"""
        return self.permissions

    def has_sop_permission(self, sop_id: str) -> bool:
        """检查是否有权限使用指定 SOP。"""
        return sop_id in self.permissions.sop_allow or "all" in self.permissions.sop_allow

    def has_db_permission(self, table: str, operation: str = "read") -> bool:
        """检查是否有权限访问指定数据库表。"""
        perm = self.permissions.db_perms.get(table)
        if perm is None:
            return False
        if operation == "read":
            return perm in ["read", "read_write"]
        if operation == "write":
            return perm == "read_write"
        return False

    def meets_level_requirement(self, required_level: int) -> bool:
        """检查是否满足权限层级要求（6 级树形继承，需求 §2）。

        调用 permission.can_access() 判断 required_level 是否在用户继承链内。
        """
        from ..permission.level import can_access
        return can_access(self.permissions.permission_level, required_level)

    def meets_grouping_requirement(self, required_grouping: int) -> bool:
        """【已废弃 v2.0】旧线性继承检查，保留向后兼容。

        新代码应使用 meets_level_requirement()。内部转调树形判断。
        """
        return self.meets_level_requirement(required_grouping)


# ══════════════════════════════════════════════════════════════════════════════
# messages 多轮记忆工具函数（模块级，供 SessionAgent + WorkItemAgent 共用）
# ══════════════════════════════════════════════════════════════════════════════


def format_message_history(message_history: list[dict]) -> str:
    """将 message_history 格式化为可读文本（供日志/调试使用）。

    注意：这不是给 LLM 用的——LLM 调用直接传 message_history 列表。
    此函数仅用于日志输出和调试。
    """
    if not message_history:
        return "（无历史消息）"
    lines = []
    for msg in message_history:
        role = msg.get("role", "?")
        role_label = "用户" if role == "user" else ("Emy" if role == "assistant" else "系统")
        content = (msg.get("content", "") or "")[:100]
        name = msg.get("name", "")
        name_part = f"（{name}）" if name and name != "system" else ""
        lines.append(f"[{role_label}{name_part}] {content}")
    return "\n".join(lines)


def build_compress_messages(history: list[dict], existing_summary: str) -> list[dict]:
    """构建压缩用的 messages 列表。

    将需要压缩的消息列表组装成一条 LLM 调用，返回增量摘要文本。

    Args:
        history: 即将被压缩的消息子列表
        existing_summary: 已有摘要（用于增量合并）

    Returns:
        可直接传给 LLMClient.chat_messages() 的 messages 列表
    """
    if not history:
        return []
    history_text = format_message_history(history)
    return [
        {"role": "system", "content": (
            "你是一个对话摘要助手。请将以下对话压缩为简短的要点摘要（中文，不超过 300 字），"
            "只保留关键事实：人物、事件、决策、任务、时间。不要包含套话。"
        )},
        {"role": "user", "content": (
            f"## 已有摘要\n{existing_summary or '（无）'}\n\n"
            f"## 近期对话\n{history_text}\n\n"
            f"请输出合并后的完整摘要（不超过 300 字）："
        )},
    ]
