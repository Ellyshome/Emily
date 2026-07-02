"""统一注册入口 —— 一站式将所有工具注册到 BusinessFlowToolRegistry。

调用方式（EmilyCore._ensure_initialized 中）：
    from .tools.registry import register_all
    register_all(self)

所有注册逻辑集中在此文件，开发者查看此文件即可了解全部可用工具及其分类归属。

文件组织（tools/ 目录）：
  base/         __init__.py — 从原始平铺文件导入并分组导出（基座能力）
  business/     __init__.py — 从原始平铺文件导入并分组导出（业务工具）
  project/      __init__.py — 从原始平铺文件导入并分组导出（项目级工具）
  registry.py   本文件 — 统一注册入口
  *.py          原始平铺工具文件（保持向后兼容，不做物理迁移）
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ... import EmilyCore

logger = logging.getLogger("emily.tool.registry")


def register_all(core: "EmilyCore") -> None:
    """将所有工具注册到 core._business_flow_tools。此函数是唯一的注册入口。"""
    reg = getattr(core, "_business_flow_tools", None)
    if reg is None:
        logger.warning("register_all: _business_flow_tools is None — skip")
        return

    _register_base(core, reg)
    _register_business(core, reg)
    _register_project(core, reg)

    logger.info("registry: %d tools (base=%d, business=%d, project=%d)",
                len(reg), _bc, _buc, _pjc)

_bc, _buc, _pjc = 0, 0, 0


def _tool(name: str, desc: str, params: dict, handler):
    from .business_flow_tools import BusinessFlowTool
    return BusinessFlowTool(name=name, description=desc, parameters=params, handler=handler)


# ══════════════════════════════════════════════════════════════════════════════
# 基座能力 — 对所有 SOP 开放
# ══════════════════════════════════════════════════════════════════════════════

def _register_base(core, reg):
    global _bc
    _bc = 0

    # query_data
    from .query_tool import handle_query_data
    qs = getattr(core, "_query_service", None)
    if qs is None:
        from ..services.query_service import QueryService
        qs = QueryService()
    reg.register(_tool("query_data", "查询项目数据（事件/任务/会议/文件/消息/用户/项目/日志）",
                       {"type": "object", "properties": {}},
                       partial(handle_query_data, query_service=qs)))
    _bc += 1

    # knowledge_search (RAG)
    rp = getattr(core, "_rag_provider", None)
    if rp is not None:
        from .knowledge_search_tool import (
            handle_knowledge_search, _KNOWLEDGE_SEARCH_SCHEMA, _KNOWLEDGE_SEARCH_DESCRIPTION,
        )
        async def _rag(params, **kw):
            return await handle_knowledge_search(params, rag_provider=rp)
        reg.register(_tool("knowledge_search", _KNOWLEDGE_SEARCH_DESCRIPTION,
                           _KNOWLEDGE_SEARCH_SCHEMA, _rag))
        _bc += 1


# ══════════════════════════════════════════════════════════════════════════════
# 业务工具 — SOP §3.2 白名单约束
# ══════════════════════════════════════════════════════════════════════════════

def _register_business(core, reg):
    global _buc
    _buc = 0
    cfg = core.config

    # 5 个核心 CRUD
    _buc += _reg_biz(reg, "record_event", "记录项目事件",
                     partial(_h("event_tool", "handle_record_event"),
                             event_app=core._event_app))
    _buc += _reg_biz(reg, "record_task", "创建任务",
                     partial(_h("task_tool", "handle_record_task"),
                             task_app=core._task_app))
    _buc += _reg_biz(reg, "record_meeting", "归档会议纪要",
                     partial(_h("meeting_tool", "handle_record_meeting"),
                             meeting_app=core._meeting_app))
    _buc += _reg_biz(reg, "record_file", "记录文件元数据",
                     partial(_h("file_tool", "handle_record_file"),
                             file_app=core._file_app))

    # 计划任务 (4 tools)
    app = getattr(core, "_plan_task_app", None)
    if app is not None:
        try:
            from .plan_task_tool import (
                handle_record_plan_task, handle_submit_plan_task,
                handle_review_plan_task, handle_query_plan_tasks,
                _RECORD_PLAN_TASK_SCHEMA, _SUBMIT_PLAN_TASK_SCHEMA,
                _REVIEW_PLAN_TASK_SCHEMA, _QUERY_PLAN_TASKS_SCHEMA,
            )
            def _mh(fn, **x):
                async def h(params, **kw):
                    return await fn(params, **x, **kw)
                return h
            reg.register(_tool("record_plan_task", "创建计划任务",
                               _RECORD_PLAN_TASK_SCHEMA,
                               _mh(handle_record_plan_task, plan_task_app=app, pending_issues=None, config=cfg)))
            reg.register(_tool("submit_plan_task", "提交计划任务成果",
                               _SUBMIT_PLAN_TASK_SCHEMA,
                               _mh(handle_submit_plan_task, plan_task_app=app)))
            reg.register(_tool("review_plan_task", "审核计划任务成果",
                               _REVIEW_PLAN_TASK_SCHEMA,
                               _mh(handle_review_plan_task, plan_task_app=app)))
            reg.register(_tool("query_plan_tasks", "查询计划任务列表",
                               _QUERY_PLAN_TASKS_SCHEMA,
                               _mh(handle_query_plan_tasks, plan_task_app=app)))
            _buc += 4
        except Exception as e:
            logger.warning("plan_task tools registration failed: %s", e)

    # write_user_memory
    mem = getattr(core, "_user_memory_service", None)
    if mem is not None and not reg.has("write_user_memory"):
        from .memory_tool import create_memory_tool
        # TC-M01: 不再传入固定 user_name，handler 运行时通过 _user_id 查 DB 解析
        bt = create_memory_tool(mem)
        reg.register(_tool(bt.name, bt.description, bt.parameters, bt.execute))
        _buc += 1


def _reg_biz(reg, name, desc, handler):
    """注册一个业务工具（fail-safe）。"""
    try:
        reg.register(_tool(name, desc, {"type": "object", "properties": {}}, handler))
        return 1
    except Exception as e:
        logger.warning("tool '%s' registration failed: %s", name, e)
        return 0


def _h(mod, fn):
    """运行时从原始平铺 .py 文件导入 handler。"""
    import importlib
    m = importlib.import_module(f".{mod}", package="emily_core.tools")
    return getattr(m, fn)


# ══════════════════════════════════════════════════════════════════════════════
# 项目级工具 — 仅管理员 / ProjectAgent
# ══════════════════════════════════════════════════════════════════════════════

def _register_project(core, reg):
    global _pjc
    _pjc = 0

    # 全景节点 (5 tools)
    na = getattr(core, "_node_app", None)
    if na is not None:
        try:
            from .node_tool import (
                handle_create_node, handle_query_node, handle_update_node_progress,
                handle_add_node_dependency, handle_mount_child_node,
                _CREATE_NODE_SCHEMA, _CREATE_NODE_DESCRIPTION,
                _QUERY_NODE_SCHEMA, _QUERY_NODE_DESCRIPTION,
                _UPDATE_PROGRESS_SCHEMA, _UPDATE_PROGRESS_DESCRIPTION,
                _ADD_DEPENDENCY_SCHEMA, _ADD_DEPENDENCY_DESCRIPTION,
                _MOUNT_CHILD_SCHEMA, _MOUNT_CHILD_DESCRIPTION,
            )
            for name, desc, schema, handler in [
                ("create_node", _CREATE_NODE_DESCRIPTION, _CREATE_NODE_SCHEMA, handle_create_node),
                ("query_node", _QUERY_NODE_DESCRIPTION, _QUERY_NODE_SCHEMA, handle_query_node),
                ("update_node_progress", _UPDATE_PROGRESS_DESCRIPTION, _UPDATE_PROGRESS_SCHEMA, handle_update_node_progress),
                ("add_node_dependency", _ADD_DEPENDENCY_DESCRIPTION, _ADD_DEPENDENCY_SCHEMA, handle_add_node_dependency),
                ("mount_child_node", _MOUNT_CHILD_DESCRIPTION, _MOUNT_CHILD_SCHEMA, handle_mount_child_node),
            ]:
                if not reg.has(name):
                    reg.register(_tool(name, desc, schema, handler))
                    _pjc += 1
        except Exception as e:
            logger.warning("node tools registration failed: %s", e)

    # 邮箱 (2 tools)
    es = getattr(core, "_email_service", None)
    if es is not None:
        _pjc += _reg_email(reg, es, core.config)

    # chat_archive
    ca = getattr(core, "_chat_archive_service", None)
    if ca is not None:
        _pjc += _reg_chat_archive(reg, ca)

    # pending_issues
    pi = getattr(core, "_pending_issues_service", None)
    if pi is not None:
        _pjc += _reg_pending(reg, pi)

    # voice_entry (总是注册，stub 返回提示)
    from .project import _VOICE_ENTRY_SCHEMA, _VOICE_ENTRY_DESCRIPTION, handle_voice_entry
    reg.register(_tool("voice_entry", _VOICE_ENTRY_DESCRIPTION, _VOICE_ENTRY_SCHEMA, handle_voice_entry))
    _pjc += 1


def _reg_email(reg, es, config):
    try:
        from .email_tool import create_send_email_tool, create_fetch_inbox_tool
        from .project import (_SEND_EMAIL_SCHEMA, _SEND_EMAIL_DESCRIPTION,
                              _FETCH_INBOX_SCHEMA, _FETCH_INBOX_DESCRIPTION,
                              handle_send_email, handle_fetch_inbox)
        reg.register(_tool("send_email", _SEND_EMAIL_DESCRIPTION, _SEND_EMAIL_SCHEMA,
                           partial(handle_send_email, email_service=es, config=config)))
        reg.register(_tool("fetch_inbox", _FETCH_INBOX_DESCRIPTION, _FETCH_INBOX_SCHEMA,
                           partial(handle_fetch_inbox, email_service=es, config=config)))
        return 2
    except Exception as e:
        logger.warning("email tools registration failed: %s", e)
        return 0


def _reg_chat_archive(reg, ca):
    try:
        from .chat_archive_tool import create_chat_archive_tool
        from .project import _CHAT_ARCHIVE_SCHEMA, _CHAT_ARCHIVE_DESCRIPTION, handle_chat_archive
        reg.register(_tool("chat_archive", _CHAT_ARCHIVE_DESCRIPTION, _CHAT_ARCHIVE_SCHEMA,
                           partial(handle_chat_archive, chat_archive_service=ca)))
        return 1
    except Exception as e:
        logger.warning("chat_archive tool registration failed: %s", e)
        return 0


def _reg_pending(reg, pi):
    try:
        from .pending_issue_tool import create_pending_issue_tool
        from .project import _PENDING_ISSUE_SCHEMA, _PENDING_ISSUE_DESCRIPTION, handle_manage_pending_issues
        reg.register(_tool("manage_pending_issues", _PENDING_ISSUE_DESCRIPTION, _PENDING_ISSUE_SCHEMA,
                           partial(handle_manage_pending_issues, pending_issues_service=pi, is_admin=False)))
        return 1
    except Exception as e:
        logger.warning("pending_issue tool registration failed: %s", e)
        return 0
