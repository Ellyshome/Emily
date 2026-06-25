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
    """

    # ── 权限层级（累进继承）──
    grouping: int = 0                   # 0:访客, 1:参建, 2:主管, 3:一般管理员, 4:系统管理员

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

    # ── 信息访问级别 ──
    info_level: str = "public"          # public / internal / confidential

    # ── 组织架构 ──
    supervisor_id: str = ""             # 直接上级（异常审核人）
    org_group: str = ""                 # 企业内分组：管理组 / 业务组

    # ── 扩展 ──
    extra_perms: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionContext:
    """Session 最小化灌注上下文。"""

    # ── 标识 ──
    conversation_id: str = ""
    user_id: str = ""
    user_name: str = ""

    # ── 1. 最近对话（滑动窗口，蓝图建议最近 20 轮）──
    recent_turns: list[dict] = field(default_factory=list)

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

    def meets_grouping_requirement(self, required_grouping: int) -> bool:
        """检查是否满足权限层级要求（累进继承）。"""
        return self.permissions.grouping >= required_grouping
