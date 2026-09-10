"""fallback_policy —— 分级兜底档位判定与白名单唯一事实源。

需求基线 R1-R6 落地：
  - 两级兜底：basic（全员，最小安全工具集） / advanced（level>=4 或管理单位）
  - basic 白名单 = {knowledge_search, chat_archive}（零写）
  - advanced 白名单 = 只读集 ∪ 追加写集（append/transition，不含覆盖/删除）

硬约束（SD §硬约束 9）：本模块是"档位判定/白名单"唯一事实源，
禁止在其他文件硬编码第二份名单。
"""

from __future__ import annotations

from enum import Enum

from ....permission.level import PermissionLevel
from ....tools.definitions import WriteMode


class FallbackTier(str, Enum):
    """兜底档位。"""

    BASIC = "basic"
    ADVANCED = "advanced"


class FallbackPolicy:
    """分级兜底策略 —— 档位判定 + 两级白名单唯一事实源。

    安全默认（fail-closed）：白名单默认最小集；未显式登记的工具对兜底不可见；
    权限判定失败一律降级为 basic（最低权限）。
    """

    # 基础兜底工具集（R3/R6）：普惠只读，零写。
    _BASIC_TOOLS: frozenset[str] = frozenset({"knowledge_search", "chat_archive"})

    # 高级兜底只读集：现 tool_adapter.FALLBACK_SAFE_TOOLS 迁移至此（单一事实源）。
    _ADVANCED_READ_TOOLS: frozenset[str] = frozenset({
        "query_data",
        "query_node",
        "query_my_nodes",
        "query_files",
        "query_experts",
        "knowledge_search",
        "list_attachments",
        "list_file_versions",
        "chat_archive",
        "fetch_inbox",
    })

    # 高级兜底追加写集（R5）：追加型（create）。覆盖/编辑/删除/批量一律不放开。
    # 默认最小集；如需放开更多追加工具，仅在此登记（并同步 M2 write_mode 元数据）。
    _ADVANCED_APPEND_TOOLS: frozenset[str] = frozenset({
        "record_event",
        "record_task",
        "record_meeting",
        "record_file",
    })

    @staticmethod
    def gate(actor: dict | None) -> FallbackTier:
        """判定档位。

        规则（R2）：`level >= 4` 或 `is_management_unit == True` → ADVANCED；
        否则 → BASIC。actor 缺失 / 字段缺失 / 解析失败 → BASIC（fail-safe）。
        """
        if not actor:
            return FallbackTier.BASIC
        try:
            level = int(actor.get("level", 1) or 1)
        except (TypeError, ValueError):
            level = 1
        is_management_unit = bool(actor.get("is_management_unit", False))
        if level >= PermissionLevel.OWNER_SUPERVISOR.value or is_management_unit:
            return FallbackTier.ADVANCED
        return FallbackTier.BASIC

    @staticmethod
    def basic_tools() -> frozenset[str]:
        return FallbackPolicy._BASIC_TOOLS

    @staticmethod
    def advanced_read_tools() -> frozenset[str]:
        return FallbackPolicy._ADVANCED_READ_TOOLS

    @staticmethod
    def advanced_append_tools() -> frozenset[str]:
        return FallbackPolicy._ADVANCED_APPEND_TOOLS

    @staticmethod
    def resolve(tier: FallbackTier | str, *, with_write: bool = False) -> frozenset[str]:
        """单一口径取白名单。

        basic → basic_tools（零写）。
        advanced 且 with_write → advanced_read_tools ∪ advanced_append_tools。
        advanced 且 not with_write → advanced_read_tools。
        """
        tier = FallbackTier(tier) if not isinstance(tier, FallbackTier) else tier
        if tier == FallbackTier.BASIC:
            return FallbackPolicy.basic_tools()
        tools = set(FallbackPolicy.advanced_read_tools())
        if with_write:
            tools |= set(FallbackPolicy.advanced_append_tools())
        return frozenset(tools)

    @staticmethod
    def assert_write_allowed(
        tool_name: str,
        tier: FallbackTier | str,
        write_mode: WriteMode | str,
    ) -> tuple[bool, str]:
        """兜底写门禁（R5/R7）。

        READ → 放行（读工具由白名单裁剪，不在此拦截）。
        APPEND/TRANSITION → 仅 advanced 且工具在追加白名单内放行。
        OVERWRITE/DELETE → 任意兜底档一律拒绝。
        """
        mode = WriteMode(write_mode) if not isinstance(write_mode, WriteMode) else write_mode
        tier = FallbackTier(tier) if not isinstance(tier, FallbackTier) else tier

        if mode == WriteMode.READ:
            return True, ""

        if mode in (WriteMode.OVERWRITE, WriteMode.DELETE):
            return False, "覆盖/删除类操作需走对应标准流程并人工确认，兜底不可执行。"

        if tier != FallbackTier.ADVANCED:
            return False, "当前为普通兜底，无写入能力，请走对应标准流程或联系管理员。"

        if mode in (WriteMode.APPEND, WriteMode.TRANSITION):
            if tool_name not in FallbackPolicy.advanced_append_tools():
                return False, f"工具 {tool_name} 未登记为可追加写，兜底不可执行。"
            return True, ""

        return False, "未知写语义。"
