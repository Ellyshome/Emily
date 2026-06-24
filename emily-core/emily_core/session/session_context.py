"""SessionContext —— Session 知识灌注数据类（蓝图 §4.3.1）。

Session 创建时只加载"必须立即获取"的上下文，其余懒加载。原则（最小化灌注）：
  ✅ 直接拿：最近对话（滑动窗口）、用户摘要（偏好+历史摘要）
  ✅ 一级压缩摘要：SOP 目录、工具目录（仅大类名 + 一句话功能描述）
  ❌ 不拿：SOP 全文、完整 schema、详细工具参数 → 懒加载

本数据类承载这些"已灌注"的最小上下文。真实的摘要生成器 / 懒加载触发
（蓝图 §4.3.1 懒加载触发表）属 Phase B/C，此处为骨架字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

    # ── 权限（创建时注入，执行时检查，蓝图 §8）──
    perm_list: list[dict] = field(default_factory=list)

    # ── 组装后的 system prompt（懒构建）──
    system_prompt: str = ""

    # ── 扩展 ──
    extra: dict[str, Any] = field(default_factory=dict)
