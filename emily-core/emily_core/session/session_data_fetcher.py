"""SessionDataFetcher —— Session 聚合根全量数据采集器。

替代旧的 collect_session_data.py 脚本逻辑，作为生产路径统一入口。
一次调用 `fetch()` 返回 session_snapshot / session_runtime / errors。

设计要点：
  - 接收 core 对象获取依赖（getattr + fail-open）
  - 合并 5 次 DB 查询为 1 次 UserRepository.get_by_id()
  - 权限采集调 PermissionService.build_permission_dict() 返回 dict
  - 项目上下文直查 Project 模型（无 ProjectRepository）
  - 工具函数从 collect_session_data.py 迁入，不改逻辑
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("emily.session_data_fetcher")

_SENTINEL = "XXXXXXXXXX"


def _beijing_now_str() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


# ══════════════════════════════════════════════════════════════════════════════
# 工具函数（从 collect_session_data.py 迁入，不改逻辑）
# ══════════════════════════════════════════════════════════════════════════════

def _parse_position_json(position_raw: str) -> str:
    """解析 users.position JSON 数组字段，拼接全部岗位名（顿号分隔）。

    多职位用户（如"精装深化设计主管"+"BIM负责人"）需要完整身份上下文，
    以便 LLM 在意图识别时感知用户全部角色。只取首个会丢失关键身份信息。
    """
    if not position_raw or position_raw == "[]":
        return ""
    try:
        positions = json.loads(position_raw)
        if isinstance(positions, list) and positions:
            return "、".join(str(p) for p in positions if p)
    except (json.JSONDecodeError, IndexError, TypeError):
        return ""
    return ""


def _resolve_display_name(user) -> str:
    """优先取 IM 绑定的显示名（如"陈哲"），回退到 username（如"chen_zhe"）。

    user_name 会被注入 LLM prompt 的"姓名"字段，使用中文名更符合
    QQ IM 场景下的用户预期。username 是登录 ID，不宜直接展示。
    """
    # 优先从 im_bindings 取第一个有 display_name 的绑定
    try:
        bindings = getattr(user, "im_bindings", None)
        if bindings:
            for binding in bindings:
                display = getattr(binding, "im_display_name", None)
                if display:
                    return display
    except Exception:
        pass
    # 回退到 username
    return getattr(user, "username", "") or ""


def _format_recent_turns(recent_turns: list[dict]) -> str:
    """将最近对话列表格式化为可读纯文本（仅限 CLI 调试展示用）。

    注意：此函数产出不注入 prompt 模板。recent_turns 的生产路径是
    session_runtime.recent_turns → SessionContext.message_history →
    build_llm_messages() 拼入 messages 数组，不走模板变量替换。
    """
    if not recent_turns:
        return ""
    lines = []
    for turn in recent_turns:
        role_label = "用户" if turn.get("role") == "user" else "Emy"
        time_str = turn.get("time", "")[:16]
        content = turn.get("content", "")
        lines.append(f"[{time_str}] {role_label}: {content}")
    return "\n".join(lines)


def _translate_project_status(status: str) -> str:
    """将 projects.status 英文值翻译为中文。"""
    status_map = {
        "active": "进行中",
        "planning": "规划中",
        "paused": "已暂停",
        "completed": "已竣工",
        "cancelled": "已取消",
        "draft": "草稿",
    }
    return status_map.get(status, status)


# ══════════════════════════════════════════════════════════════════════════════
# SessionDataFetcher
# ══════════════════════════════════════════════════════════════════════════════

class SessionDataFetcher:
    """Session 聚合根数据采集器。

    静态方法 fetch() 一次性全量采集并返回结构化数据。
    """

    @staticmethod
    def fetch(user_id: str, conversation_id: str = "", core=None) -> dict:
        """一次性全量采集 Session 数据。

        Args:
            user_id: 用户 ID
            conversation_id: 会话 ID（可选）
            core: EmilyCore 实例（可选，用于获取 PermissionService / SOP 注册表等）

        Returns:
            {
                "session_snapshot": { ... },
                "session_runtime":  { ... },
                "errors": list[str],
            }
        """
        if not user_id:
            raise ValueError("user_id 不能为空")

        errors: list[str] = []

        def _record(value, label: str):
            if isinstance(value, str) and value == _SENTINEL:
                errors.append(label)
            elif isinstance(value, dict):
                for k, v in value.items():
                    if v == _SENTINEL:
                        errors.append(f"{label}.{k}")

        # ── 步骤 1: 从 UserRepository 一次性获取用户名/职位/long_term_memory/conversation_summary/project_id ──
        from ..repositories.user_repo import UserRepository
        user = UserRepository.get_by_id(user_id)
        if user is None:
            return _empty_result(conversation_id, user_id, ["用户不存在"])

        # 优先取 IM 绑定的显示名（如"陈哲"），回退到 username（如"chen_zhe"）
        user_name = _resolve_display_name(user)
        user_position = _parse_position_json(user.position or "")

        long_term_memory = user.long_term_memory or ""
        conversation_summary = user.conversation_summary or ""

        project_id = getattr(user, "project_id", None)

        # ── 步骤 2: 权限快照（调 PermissionService.build_permission_dict） ──
        perms = _sub_fetch_permissions(user_id, core)

        # ── 步骤 3: 项目上下文（直查 Project 模型） ──
        project = _sub_fetch_project(project_id)

        # ── 步骤 4: 最近对话（调 MessageRepository.get_recent_by_user_id） ──
        recent_turns = _sub_fetch_recent_turns(user_id)

        # ── 步骤 5: 原子化能力（API 工具列表、可见文件、RAG） ──
        available_tools = _sub_fetch_available_tools(perms)
        visible_schema = _sub_fetch_visible_schema(perms)
        visible_files = _sub_fetch_visible_files(user_id)
        rag_info = _sub_fetch_rag_info(core)

        # ── 组装输出 ──
        created_at = datetime.now(timezone.utc).isoformat()

        session_snapshot = {
            # 标识字段 🔒
            "conversation_id": conversation_id,
            "user_id": user_id,
            "user_name": user_name,
            "user_position": user_position,
            "created_at": created_at,
            # 项目上下文 🔄
            "project_name": project["project_name"],
            "project_type": project["project_type"],
            "project_status": project["project_status"],
            # 权限字段 🔥（扁平化，不嵌套 permissions dict）
            "level": perms.get("level", 1),
            "company_id": perms.get("company_id", ""),
            "company_type": perms.get("company_type", ""),
            "company_name": perms.get("company_name", ""),
            "department": perms.get("department", []),
            "project_ids": perms.get("project_ids", []),
            "partner_ids": perms.get("partner_ids", []),
            "scopes": perms.get("scopes", []),
            "sop_allow": perms.get("sop_allow", []),
            "db_perms": perms.get("db_perms", {}),
            "info_level": perms.get("info_level", "public"),
            "supervisor_id": perms.get("supervisor_id", ""),
            "granted_codes": perms.get("granted_codes", []),
            "denied_codes": perms.get("denied_codes", []),
            "authorized_node_ids": perms.get("authorized_node_ids", []),
            "permission_version": perms.get("permission_version", 0),
            "permissions_loaded_at": perms.get("permissions_loaded_at", ""),
            # 记忆字段 📝
            "long_term_memory": long_term_memory,
            "conversation_summary": conversation_summary,
            # 原子化能力字段 🔥
            "available_tools": available_tools,
            "visible_schema_summary": visible_schema,
            "visible_files_count": visible_files.get("count", 0),
            "visible_files_summary": _format_visible_files_summary(visible_files),
            "rag_available": rag_info.get("available", False),
            "rag_collections": rag_info.get("collections", []),
        }

        session_runtime = {
            "recent_turns": recent_turns,
        }

        # 记录错误
        for key in ["user_name", "user_position"]:
            _record(session_snapshot[key], key)
        for key in ["level", "company_id", "company_type", "company_name",
                     "supervisor_id", "info_level"]:
            _record(session_snapshot[key], key)
        for key in ["project_name", "project_type", "project_status"]:
            _record(project[key], f"project.{key}")
        _record(long_term_memory, "long_term_memory")
        _record(conversation_summary, "conversation_summary")

        logger.info("SessionDataFetcher.fetch done: user=%s errors=%d", user_id, len(errors))
        if errors:
            logger.warning("SessionDataFetcher.fetch errors: %s", " | ".join(errors))

        return {
            "session_snapshot": session_snapshot,
            "session_runtime": session_runtime,
            "errors": errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 子采集函数
# ══════════════════════════════════════════════════════════════════════════════

def _sub_fetch_permissions(user_id: str, core=None) -> dict:
    """从 PermissionService 获取权限 dict。"""
    try:
        perm_service = getattr(core, "_permission_service", None) if core else None
        if perm_service is None:
            from ..services.permission_service import PermissionService
            # 独立创建时传入 SkillRegistry（若有），使 sop_allow fallback 生效
            skill_registry = getattr(core, "_skill_registry", None) if core else None
            perm_service = PermissionService(skill_registry=skill_registry)
        perm_dict = perm_service.build_permission_dict(user_id)
        return perm_dict
    except Exception as e:
        logger.error("_sub_fetch_permissions failed user=%s: %s", user_id, e)
        return {"level": 1}


def _sub_fetch_project(project_id: Optional[str]) -> dict:
    """直查 Project 模型获取项目上下文。"""
    if not project_id:
        return {"project_name": "", "project_type": "", "project_status": ""}
    try:
        from ..infrastructure.database import get_session
        from ..infrastructure.database.models import Project

        with get_session() as session:
            project = session.query(Project).filter(
                Project.id == project_id,
                Project.is_deleted == False,
            ).first()

            if project is None:
                return {"project_name": "", "project_type": "", "project_status": ""}

            stage = getattr(project, "lifecycle_stage", 0) or 0
            type_map = {0: "工程项目", 1: "工程项目", 2: "房屋建筑", 3: "工程项目"}
            return {
                "project_name": project.name or "",
                "project_type": type_map.get(stage, ""),
                "project_status": _translate_project_status(project.status or ""),
            }
    except Exception as e:
        logger.error("_sub_fetch_project failed project_id=%s: %s", project_id, e)
        return {"project_name": _SENTINEL, "project_type": _SENTINEL, "project_status": _SENTINEL}


def _sub_fetch_recent_turns(user_id: str) -> list[dict]:
    """从 MessageRepository 获取最近消息（跨会话）。"""
    try:
        from ..repositories.message_repo import MessageRepository
        return MessageRepository.get_recent_by_user_id(user_id, limit=20)
    except Exception as e:
        logger.error("_sub_fetch_recent_turns failed user=%s: %s", user_id, e)
        return []


def _sub_fetch_available_tools(perms: dict) -> list[dict]:
    """从 ToolRegistryRepo 获取用户可用 API 列表。委托给 fetchers 子模块。"""
    from .fetchers.fetch_available_tools import fetch
    return fetch(perms=perms)


def _sub_fetch_visible_schema(perms: dict) -> str:
    """获取可见 DB Schema 摘要。委托给 fetchers 子模块。"""
    from .fetchers.fetch_visible_schema import fetch
    return fetch(perms=perms)


def _sub_fetch_visible_files(user_id: str) -> dict:
    """获取用户可见文件摘要。委托给 fetchers 子模块。"""
    from .fetchers.fetch_visible_files import fetch
    return fetch(user_id=user_id)


def _sub_fetch_rag_info(core=None) -> dict:
    """获取 RAG 知识库可用性信息。委托给 fetchers 子模块。"""
    from .fetchers.fetch_rag_info import fetch
    return fetch(core=core)


def _format_visible_files_summary(visible_files: dict) -> str:
    """格式化可见文件摘要为文本。委托给 fetchers 子模块。"""
    from .fetchers.fetch_visible_files import format_summary
    return format_summary(visible_files)


def _empty_result(conversation_id: str, user_id: str, errors: list[str]) -> dict:
    return {
        "session_snapshot": {
            # 标识字段 🔒
            "conversation_id": conversation_id,
            "user_id": user_id,
            "user_name": _SENTINEL,
            "user_position": _SENTINEL,
            "created_at": _beijing_now_str(),
            # 项目上下文 🔄
            "project_name": _SENTINEL,
            "project_type": _SENTINEL,
            "project_status": _SENTINEL,
            # 权限字段 🔥（扁平化）
            "level": 1,
            "company_id": "",
            "company_type": "",
            "company_name": "",
            "department": [],
            "project_ids": [],
            "partner_ids": [],
            "scopes": [],
            "sop_allow": [],
            "db_perms": {},
            "info_level": "public",
            "supervisor_id": "",
            "granted_codes": [],
            "denied_codes": [],
            "authorized_node_ids": [],
            "permission_version": 0,
            "permissions_loaded_at": "",
            # 记忆字段 📝
            "long_term_memory": _SENTINEL,
            "conversation_summary": _SENTINEL,
            # 原子化能力字段 🔥
            "available_tools": [],
            "visible_schema_summary": "",
            "visible_files_count": 0,
            "visible_files_summary": "",
            "rag_available": False,
            "rag_collections": [],
        },
        "session_runtime": {
            "recent_turns": [],
        },
        "errors": errors,
    }
