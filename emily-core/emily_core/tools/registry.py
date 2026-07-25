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
    """将所有工具注册到 core._business_flow_tools。此函数是唯一的注册入口。

    Args:
        core: EmilyCore 实例，其 _business_flow_tools 属性承载注册表，并向外暴露各 service/app 依赖。

    Returns:
        None — 注册结果通过副作用写入 core._business_flow_tools。
    """
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


def _tool(name: str, desc: str, params: dict, handler, category: str = "base", permission_flag: str = "all"):
    """快捷构造 BusinessFlowTool 实例。

    Args:
        name: 工具名称，与 Skill YAML 的 tools 声明一致。
        desc: 工具描述，注入 LLM prompt 辅助参数提取。
        params: JSON Schema 参数定义。
        handler: 异步处理函数，签名为 async fn(params: dict) -> dict。
        category: 工具分类，base/business/project。
        permission_flag: 权限标识，all/admin/write。

    Returns:
        BusinessFlowTool 实例，可直接注册到 BusinessFlowToolRegistry。
    """
    from .business_flow_tools import BusinessFlowTool
    return BusinessFlowTool(name=name, description=desc, parameters=params, handler=handler,
                            category=category, permission_flag=permission_flag)


# ══════════════════════════════════════════════════════════════════════════════
# 基座能力 — 对所有 SOP 开放
# ══════════════════════════════════════════════════════════════════════════════

def _register_base(core, reg):
    """注册基座能力工具（query_data、knowledge_search），对所有 SOP 开放。

    Args:
        core: EmilyCore 实例，取其 _query_service 和 _rag_provider 注入 handler。
        reg: BusinessFlowToolRegistry 注册表实例。

    Returns:
        None — 通过 reg.register() 副作用写入注册表，同时更新全局变量 _bc。
    """
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
    from .knowledge_search_tool import (
        _KNOWLEDGE_SEARCH_SCHEMA, _KNOWLEDGE_SEARCH_DESCRIPTION,
    )
    if rp is not None:
        from .knowledge_search_tool import handle_knowledge_search
        async def _rag(params, **kw):
            return await handle_knowledge_search(params, rag_provider=rp)
        reg.register(_tool("knowledge_search", _KNOWLEDGE_SEARCH_DESCRIPTION,
                           _KNOWLEDGE_SEARCH_SCHEMA, _rag))
    else:
        # 兜底：rag_provider 不可用时仍注册工具，返回友好提示（而非完全缺失）
        async def _rag_stub(params, **kw):
            query = (params.get("query", "") or "").strip()
            return {
                "success": False,
                "reply": f"知识库服务暂未就绪（查询：{query}），请稍后重试或联系管理员检查 RAG 配置。",
            }
        reg.register(_tool("knowledge_search", _KNOWLEDGE_SEARCH_DESCRIPTION,
                           _KNOWLEDGE_SEARCH_SCHEMA, _rag_stub))
        logger.warning("knowledge_search registered with stub handler (rag_provider is None)")
    _bc += 1

    # ocr_document (VLM OCR)
    vlc = getattr(core, "_vlm_client", None)
    if vlc is not None:
        from .ocr_tool import handle_ocr_document, _OCR_SCHEMA, _OCR_DESCRIPTION
        reg.register(_tool("ocr_document", _OCR_DESCRIPTION, _OCR_SCHEMA,
                           partial(handle_ocr_document, vlm_client=vlc)))
        _bc += 1


# ══════════════════════════════════════════════════════════════════════════════
# 业务工具 
# ══════════════════════════════════════════════════════════════════════════════

def _register_business(core, reg):
    """注册业务工具（CRUD 核心操作、文件管理、用户记忆等）。

    Args:
        core: EmilyCore 实例，取其 _event_app、_task_app、_meeting_app、_file_app
            和 _user_memory_service 注入各 handler。
        reg: BusinessFlowToolRegistry 注册表实例。

    Returns:
        None — 通过 _reg_biz() 副作用写入注册表，同时更新全局变量 _buc。
    """
    global _buc
    _buc = 0
    cfg = core.config

    # 5 个核心 CRUD
    _buc += _reg_biz(reg, "record_event", "记录项目事件",
                     partial(_h("event_tool", "handle_record_event"),
                             event_app=core._event_app), "business", "write")
    _buc += _reg_biz(reg, "record_task", "创建任务",
                     partial(_h("task_tool", "handle_record_task"),
                             task_app=core._task_app), "business", "write")
    _buc += _reg_biz(reg, "record_meeting", "归档会议纪要",
                     partial(_h("meeting_tool", "handle_record_meeting"),
                             meeting_app=core._meeting_app), "business", "write")
    _buc += _reg_biz(reg, "record_file", "记录文件元数据",
                     partial(_h("file_tool", "handle_record_file"),
                             file_app=core._file_app), "business", "write")

    # 文件查询 + 分类修改 (2 tools)
    _buc += _reg_biz(reg, "query_files", "按分类或关键词查询项目文件",
                     partial(_h("file_tool", "handle_query_files"),
                             file_app=core._file_app), "business", "all")
    _buc += _reg_biz(reg, "update_file_category", "修改文件分类归属",
                     partial(_h("file_tool", "handle_update_file_category"),
                             file_app=core._file_app), "business", "write")

    # 计划任务工具已废弃（由 node_task_tool 替代），不再注册

    # write_user_memory
    mem = getattr(core, "_user_memory_service", None)
    if mem is not None and not reg.has("write_user_memory"):
        from .memory_tool import create_memory_tool
        # TC-M01: 不再传入固定 user_name，handler 运行时通过 _user_id 查 DB 解析
        bt = create_memory_tool(mem)
        reg.register(_tool(bt.name, bt.description, bt.parameters, bt.execute))
        _buc += 1

    # ── 原子工具层（RAG 录入侧）──
    # parse_document
    from .parse_document_tool import handle_parse_document, _PARSE_SCHEMA as _PS, _PARSE_DESCRIPTION as _PD
    reg.register(_tool("parse_document", _PD, _PS, partial(handle_parse_document),
                      category="business", permission_flag="all"))
    _buc += 1

    # extract_table
    from .extract_table_tool import handle_extract_table, _TABLE_SCHEMA as _TS, _TABLE_DESCRIPTION as _TD
    reg.register(_tool("extract_table", _TD, _TS, partial(handle_extract_table),
                      category="business", permission_flag="all"))
    _buc += 1

    # chunk_text
    from .chunk_tool import handle_chunk_text, _CHUNK_SCHEMA as _CS, _CHUNK_DESCRIPTION as _CD
    reg.register(_tool("chunk_text", _CD, _CS, partial(handle_chunk_text),
                      category="business", permission_flag="all"))
    _buc += 1

    # embed_and_index
    tei = getattr(core, "_tei_client", None)
    kc_repo = getattr(core, "_knowledge_chunk_repo", None)
    if tei is not None and kc_repo is not None:
        from .embed_tool import handle_embed_and_index, _EMBED_SCHEMA as _ES, _EMBED_DESCRIPTION as _ED
        reg.register(_tool("embed_and_index", _ED, _ES,
                          partial(handle_embed_and_index, tei=tei, repo=kc_repo),
                          category="business", permission_flag="write"))
        _buc += 1


def _reg_biz(reg, name, desc, handler, category="business", permission_flag="write"):
    """注册一个业务工具（fail-safe），异常时仅打日志不抛错。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        name: 工具名称。
        desc: 工具描述。
        handler: 异步处理函数。
        category: 工具分类，默认 business。
        permission_flag: 权限标识，默认 write。

    Returns:
        int — 成功返回 1，失败返回 0，方便累加计数。
    """
    try:
        reg.register(_tool(name, desc, {"type": "object", "properties": {}}, handler,
                          category=category, permission_flag=permission_flag))
        return 1
    except Exception as e:
        logger.warning("tool '%s' registration failed: %s", name, e)
        return 0


def _h(mod, fn):
    """运行时从原始平铺 .py 文件导入 handler。

    Args:
        mod: 模块名（如 "event_tool"），对应 emily_core.tools 下的 .py 文件。
        fn: 函数名，目标模块中导出的 handler 函数名。

    Returns:
        callable — 导入的 handler 函数对象。
    """
    import importlib
    m = importlib.import_module(f".{mod}", package="emily_core.tools")
    return getattr(m, fn)


# ══════════════════════════════════════════════════════════════════════════════
# 项目级工具 — 仅管理员 / ProjectAgent
# ══════════════════════════════════════════════════════════════════════════════

def _register_project(core, reg):
    """注册项目级工具（全景节点、邮箱、聊天归档、语音录入等），仅管理员/ProjectAgent 可用。

    Args:
        core: EmilyCore 实例，取其 _node_app、_node_service、_email_service、
            _chat_archive_service、_pending_issues_service 等注入各 handler。
        reg: BusinessFlowToolRegistry 注册表实例。

    Returns:
        None — 通过子注册函数及 reg.register() 副作用写入注册表，同时更新全局变量 _pjc。
    """
    global _pjc
    _pjc = 0

    # 全景节点 (8 tools)
    na = getattr(core, "_node_app", None)
    if na is not None:
        try:
            from .node_tool import (
                handle_create_node, handle_query_node, handle_update_node_progress,
                handle_add_node_dependency, handle_mount_child_node,
                handle_update_nodes, handle_activate_nodes, handle_discard_nodes,
                _CREATE_NODE_SCHEMA, _CREATE_NODE_DESCRIPTION,
                _QUERY_NODE_SCHEMA, _QUERY_NODE_DESCRIPTION,
                _UPDATE_PROGRESS_SCHEMA, _UPDATE_PROGRESS_DESCRIPTION,
                _ADD_DEPENDENCY_SCHEMA, _ADD_DEPENDENCY_DESCRIPTION,
                _MOUNT_CHILD_SCHEMA, _MOUNT_CHILD_DESCRIPTION,
                _UPDATE_NODES_SCHEMA, _UPDATE_NODES_DESCRIPTION,
                _ACTIVATE_NODES_SCHEMA, _ACTIVATE_NODES_DESCRIPTION,
                _DISCARD_NODES_SCHEMA, _DISCARD_NODES_DESCRIPTION,
            )
            for name, desc, schema, handler in [
                ("create_node", _CREATE_NODE_DESCRIPTION, _CREATE_NODE_SCHEMA, handle_create_node),
                ("query_node", _QUERY_NODE_DESCRIPTION, _QUERY_NODE_SCHEMA, handle_query_node),
                ("update_node_progress", _UPDATE_PROGRESS_DESCRIPTION, _UPDATE_PROGRESS_SCHEMA, handle_update_node_progress),
                ("add_node_dependency", _ADD_DEPENDENCY_DESCRIPTION, _ADD_DEPENDENCY_SCHEMA, handle_add_node_dependency),
                ("mount_child_node", _MOUNT_CHILD_DESCRIPTION, _MOUNT_CHILD_SCHEMA, handle_mount_child_node),
                ("update_nodes", _UPDATE_NODES_DESCRIPTION, _UPDATE_NODES_SCHEMA, handle_update_nodes),
                ("activate_nodes", _ACTIVATE_NODES_DESCRIPTION, _ACTIVATE_NODES_SCHEMA, handle_activate_nodes),
                ("discard_nodes", _DISCARD_NODES_DESCRIPTION, _DISCARD_NODES_SCHEMA, handle_discard_nodes),
            ]:
                if not reg.has(name):
                    reg.register(_tool(name, desc, schema, handler,
                                      category="project", permission_flag="admin"))
                    _pjc += 1
        except Exception as e:
            logger.warning("node tools registration failed: %s", e)

        # 节点任务工具（替代 record_plan_task / submit_plan_task / review_plan_task / query_plan_tasks）
        ns = getattr(core, "_node_service", None)
        if ns is not None:
            try:
                from .node_task_tool import (
                    handle_create_task_node, handle_submit_node_deliverable,
                    handle_confirm_node_deliverable, handle_return_node_deliverable,
                    handle_query_my_nodes,
                )
                for name, desc, handler in [
                    ("create_task_node", "创建TASK类型叶子节点（替代record_plan_task）",
                     partial(handle_create_task_node, node_service=ns)),
                    ("submit_node_deliverable", "提交节点成果（替代submit_plan_task）",
                     partial(handle_submit_node_deliverable, node_service=ns)),
                    ("confirm_node_deliverable", "确认节点成果",
                     partial(handle_confirm_node_deliverable, node_service=ns)),
                    ("return_node_deliverable", "退回节点成果",
                     partial(handle_return_node_deliverable, node_service=ns)),
                    ("query_my_nodes", "查询我负责的节点（替代query_plan_tasks）",
                     partial(handle_query_my_nodes, node_service=ns)),
                ]:
                    if not reg.has(name):
                        reg.register(_tool(name, desc, {"type": "object", "properties": {}}, handler,
                                          category="business", permission_flag="write"))
                        _pjc += 1
            except Exception as e:
                logger.warning("node task tools registration failed: %s", e)

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
    """注册邮箱工具（send_email、fetch_inbox）。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        es: EmailService 实例，处理邮件收发。
        config: EmilyCore 配置对象，提供 SMTP/IMAP 等邮箱参数。

    Returns:
        int — 注册成功的工具数量（0 或 2），方便累加计数。
    """
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
    """注册聊天归档工具（chat_archive）。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        ca: ChatArchiveService 实例，处理聊天记录归档。

    Returns:
        int — 注册成功的工具数量（0 或 1），方便累加计数。
    """
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
    """注册待办事项管理工具（manage_pending_issues）。

    Args:
        reg: BusinessFlowToolRegistry 注册表实例。
        pi: PendingIssuesService 实例，处理待办事项的增删改查。

    Returns:
        int — 注册成功的工具数量（0 或 1），方便累加计数。
    """
    try:
        from .pending_issue_tool import create_pending_issue_tool
        from .project import _PENDING_ISSUE_SCHEMA, _PENDING_ISSUE_DESCRIPTION, handle_manage_pending_issues
        reg.register(_tool("manage_pending_issues", _PENDING_ISSUE_DESCRIPTION, _PENDING_ISSUE_SCHEMA,
                           partial(handle_manage_pending_issues, pending_issues_service=pi, is_admin=False)))
        return 1
    except Exception as e:
        logger.warning("pending_issue tool registration failed: %s", e)
        return 0
