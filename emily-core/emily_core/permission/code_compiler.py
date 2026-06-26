"""权限编码系统 —— 分层编码解析与匹配（需求 §6）。

编码格式: [资源类型]-[密级]-[项目ID]-[节点ID]-[资源ID]
示例:
  DOC-PUBLIC-PRJ001-NODE001-FILE001
  DB-INTERNAL-PRJ002-*-*
  SOP-CONFIDENTIAL-*-NODE005-FORM003

通配符（需求 §6.1.2）:
  *        匹配任意单段值
  PREFIX*  前缀匹配（如 NODE001* 匹配 NODE001A、NODE001B 等子节点）

compile_code() 将编码字符串编译为 CompiledCode 5 元组结构；
code_matches_any() 用于鉴权：用户持有的通配编码匹配资源的具体编码。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# 资源类型枚举（需求 §6.1.1）
RESOURCE_TYPES = frozenset({"DOC", "DB", "SOP", "MSG", "SYS"})

# 密级枚举（需求 §3.1），按可见性从宽到窄排序，索引越大越严格
SECURITY_LEVELS = ["PUBLIC", "INTERNAL", "PRIVATE", "CONFIDENTIAL"]
SECURITY_LEVEL_INDEX = {lvl: i for i, lvl in enumerate(SECURITY_LEVELS)}

# 密级中文
SECURITY_LEVEL_NAME = {
    "PUBLIC": "公开",
    "INTERNAL": "内部",
    "PRIVATE": "私有",
    "CONFIDENTIAL": "机密",
}

# permission_level → 可见的最大密级索引（需求 §2.4 + §3.1）
# L1 访客: 仅 PUBLIC
# L2 参建执行: + INTERNAL（本单位节点内）
# L3 参建管理: + INTERNAL（本单位机密走节点范围/单独授权）
# L4 建设主管: + INTERNAL（责任范围内）
# L5 管理员: + PRIVATE + CONFIDENTIAL
# L6 系统管理员: 全部
LEVEL_MAX_SECURITY_INDEX: dict[int, int] = {
    1: 0,  # PUBLIC
    2: 1,  # INTERNAL
    3: 1,  # INTERNAL（机密走单独授权）
    4: 1,  # INTERNAL
    5: 3,  # CONFIDENTIAL
    6: 3,  # CONFIDENTIAL
}


@dataclass(frozen=True)
class CompiledCode:
    """编译后的权限编码结构。"""

    raw: str               # 原始编码字符串
    resource_type: str     # DOC/DB/SOP/MSG/SYS（资源类型必须显式，无通配）
    security_level: str    # PUBLIC/INTERNAL/PRIVATE/CONFIDENTIAL
    project_id: str        # 项目ID 或 "*"
    node_id: str           # 节点ID 或 "*" 或 "PREFIX*"
    resource_id: str       # 资源ID 或 "*" 或 "PREFIX*"

    def matches(self, target: "CompiledCode") -> bool:
        """判断本编码（含通配符）是否匹配 target（具体编码）。

        用于鉴权：用户持有的 granted_codes（含通配）匹配资源的具体编码。
        resource_type 与 security_level 必须精确相等（不允许通配）。
        """
        if self.resource_type != target.resource_type:
            return False
        if self.security_level != target.security_level:
            return False
        return (
            _segment_match(self.project_id, target.project_id)
            and _segment_match(self.node_id, target.node_id)
            and _segment_match(self.resource_id, target.resource_id)
        )


def _segment_match(pattern: str, value: str) -> bool:
    """单段匹配：* 匹配任意；PREFIX* 前缀匹配；否则精确匹配。"""
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return pattern == value


_CODE_RE = re.compile(r"^([A-Z]+)-([A-Z]+)-([^-]*)-([^-]*)-([^-]*)$")


def compile_code(code: str) -> CompiledCode | None:
    """编译权限编码字符串为 CompiledCode。非法格式返回 None。"""
    if not code:
        return None
    m = _CODE_RE.match(code.strip())
    if m is None:
        return None
    rtype, slevel, pid, nid, rid = m.groups()
    if rtype not in RESOURCE_TYPES:
        return None
    if slevel not in SECURITY_LEVEL_INDEX:
        return None
    return CompiledCode(
        raw=code.strip(),
        resource_type=rtype,
        security_level=slevel,
        project_id=pid or "*",
        node_id=nid or "*",
        resource_id=rid or "*",
    )


def code_matches_any(code: str, patterns: Iterable[str]) -> bool:
    """判断具体编码 code 是否匹配任一通配模式 patterns。

    Args:
        code: 资源的具体权限编码（无通配）
        patterns: 用户持有的权限编码列表（可含通配）
    """
    target = compile_code(code)
    if target is None:
        return False
    for p in patterns:
        pat = compile_code(p)
        if pat is not None and pat.matches(target):
            return True
    return False


def can_view_security_level(user_level: int, security_level: str) -> bool:
    """用户级别是否能查看指定密级（需求 §3.1）。

    L1: 仅 PUBLIC；L2/L3/L4: +INTERNAL；L5/L6: +PRIVATE+CONFIDENTIAL。
    机密/私有资源的细粒度访问另由 granted_codes / 单独授权控制。
    """
    idx = SECURITY_LEVEL_INDEX.get(security_level)
    if idx is None:
        return False
    max_idx = LEVEL_MAX_SECURITY_INDEX.get(user_level, 0)
    return idx <= max_idx
