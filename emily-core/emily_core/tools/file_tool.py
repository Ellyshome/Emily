"""文件工具 — 含业务流工具 + M13 文件传输工具。

M13: send_file（主动发送文件）和 read_local_file（按需读取本地文件）。
M14: record_file 核心逻辑提取为独立 handler，供 BusinessFlowTool 使用。
     ToolRegistry 已移除，不再包装为 LLM ToolDefinition。
M2: 补全 send_file handler（权限校验 + 本地路径解析 + outbound 事件发布）。
"""

import logging
import os
from pathlib import Path

from ..adapters.standard.result import RouteResult
from ..application.file_app import FileApplication

logger = logging.getLogger("emily.tool.file")

# ══════════════════════════════════════════════════════════════════════════════
# M14: 业务流工具 handler — record_file（框架直接调用）
# ══════════════════════════════════════════════════════════════════════════════

_FILE_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {
            "type": "string",
            "description": "项目名称（如 '翠湖庭院'）。若只知道名称不知 UUID，先调 resolve_project 拿 project_id",
        },
        "project_id": {
            "type": "string",
            "format": "uuid",
            "description": "项目 UUID。若未提供但有 project_name，必须先调 resolve_project 解析",
            "fk_target": "projects.id",
            "resolvable_from": "project_name",
            "resolver": "resolve_project",
        },
        "data": {
            "type": "object",
            "description": "文件详细参数",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "文件名（如 '施工图纸v3.pdf'）",
                },
                "file_type": {
                    "type": "string",
                    "description": "文件类型（如 pdf、docx、图纸）",
                },
                "file_category": {
                    "type": "string",
                    "description": "文件业务分类：PROJECT_LICENSE(项目证照)/CONTRACT(承包合同)/WORK_RECORD(工作记录)/PHASE_DELIVERABLE(阶段成果)/PROCESS_DOC(过程文件)/MANAGEMENT_SPEC(管理规程)/OTHER(其他文件)",
                    "default": "OTHER",
                },
                "purpose": {
                    "type": "string",
                    "description": "业务意图：EVIDENCE(凭证)/RECORD(记录)/DESIGN(图纸)/REFERENCE(参考)（CHAT 不入库，默认 RECORD）",
                    "default": "RECORD",
                },
            },
            "required": ["filename"],
        },
        "force": {
            "type": "boolean",
            "description": "是否强制录入（跳过核验，默认 false）",
        },
        "guardian_notes": {
            "type": "string",
            "description": "守护核验发现的问题描述（force=true 时填写）",
        },
    },
    "required": ["data"],
}

_FILE_TOOL_DESCRIPTION = (
    "记录一个文件信息。\n"
    "⚠️ 调用前必须完成拟录入单流程（见系统指令第10条）。\n"
    "\n"
    "purpose（业务意图，必填）5 类：\n"
    "  EVIDENCE — 凭证证据（证照/许可/合同/批复/合格单），永久保留\n"
    "  RECORD — 工作记录（验收附图/施工记录/会议附件），项目周期\n"
    "  DESIGN — 设计图纸（施工图/过程图），走版本链\n"
    "  REFERENCE — 参考样例（优秀工艺/做法参考），入通用 RAG\n"
    "  CHAT — 闲聊素材（不入库，record_file 不会收到）\n"
    "\n"
    "判断依据：文件名 + 用户对话 + 上下文（不读文件内容）。\n"
    "  文件名含'证/许可/执照/合同/批复/合格单' → EVIDENCE\n"
    "  文件名含'图/.dwg/施工图/设计' → DESIGN\n"
    "  文件名含'参考/样例/规范/工艺' → REFERENCE\n"
    "  其余 → RECORD（默认）\n"
    "\n"
    "RAG 入库策略（代码自动，LLM 无需关心）：仅 REFERENCE 自动异步入通用 RAG 库。\n"
    "\n"
    "字段分级：\n"
    "  [必有] filename — 文件名\n"
    "  [必有] purpose — 业务意图（上述 5 类，默认 RECORD）\n"
    "  [应有] project_name — 关联项目名称\n"
    "  [应有] file_type — 文件类型（从后缀推断）\n"
    "\n"
    "守护核验三选一（仅在核验不通过时）：\n"
    "  force=false — 正常录入\n"
    "  force=true + guardian_notes — 坚持录入（写入待解决清单）"
)


async def handle_record_file(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    pending_issues=None,
    config=None,
    file_manager=None,
    tei_client=None,
    kc_repo=None,
    **kwargs,
) -> dict:
    """处理文件归档（M14 业务流工具 handler）。

    M13 (TC-A01): 从 params 提取附件 URL 信息并传递给 FileApplication。
    M5: REFERENCE 类自动异步入 RAG 通用参考库。
    """
    # Skill 路径传平铺参数，LLM 规划路径传嵌套 data；二者兼容
    data = params.get("data") or {}
    if not data:
        data = {k: v for k, v in params.items()
                if not k.startswith("_") and k not in ("data", "force", "guardian_notes",
                                                        "project_name", "project_id")}
    force = params.get("force", False)
    guardian_notes = params.get("guardian_notes", "")

    # M13 (TC-A01): 提取附件 URL 信息
    attachment_url = params.get("_attachment_url", "")
    attachment_type = params.get("_attachment_type", 0)
    source_filename = data.get("filename", "")
    raw_file_type = data.get("file_type", "")
    # 防御:filename/file_type 可能是 dict(ParamExtractor prev_step 取到整个 extracted dict)
    if isinstance(source_filename, dict):
        logger.warning("record_file: filename is dict, extract inner: %s", str(source_filename)[:200])
        source_filename = source_filename.get("filename") or source_filename.get("value") or ""
    if isinstance(raw_file_type, dict):
        logger.warning("record_file: file_type is dict, extract inner: %s", str(raw_file_type)[:200])
        raw_file_type = raw_file_type.get("file_type") or raw_file_type.get("value") or ""

    # ── 正常录入流程 ──
    # 防御:某些 Skill 误把工具返回 dict 当 project_id 传入(如 query_data 返回值),
    # 此处兜底为 None,避免 dict 传到 DB 层报 can't adapt type 'dict'
    raw_project_id = params.get("project_id")
    if isinstance(raw_project_id, dict):
        logger.warning("record_file: project_id is dict, not string UUID, set to None: %s",
                       str(raw_project_id)[:200])
        raw_project_id = None
    route_result = RouteResult(
        intent="file_record",
        project_name=params.get("project_name"),
        project_id=raw_project_id,
        data={
            "filename": source_filename or "未命名文件",
            "file_type": raw_file_type,
            "file_category": data.get("file_category", "OTHER"),
            "purpose": data.get("purpose") or params.get("purpose") or "RECORD",
        },
    )
    result = await file_app.handle_file(
        route_result, user_id, message_id,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        source_filename=source_filename,
    )

    reply_text = result.reply or ""
    if force and guardian_notes and result.success and pending_issues is not None:
        try:
            issue_id = pending_issues.add(
                raised_by=user_id or "用户",
                source=f"录入文件「{data.get('filename', '未命名')}」时守护核验发现异常",
                description=f"核验发现：{guardian_notes}。用户坚持录入。",
                suggestion="请核实并给出处理意见。",
                related_events=[],
            )
            reply_text = (reply_text or "") + f"\n📋 已记录待处理：{issue_id}"
            logger.info("M8a pending issue created: %s", issue_id)
        except Exception as e:
            logger.warning("M8a failed to create pending issue: %s", e)

    # M5: REFERENCE 自动异步入 RAG 通用参考库
    if result.success and data.get("purpose") == "REFERENCE" and tei_client is not None and kc_repo is not None:
        import asyncio
        asyncio.create_task(_index_reference_file(
            result.object_id, tei_client, kc_repo, file_manager=file_manager,
        ))
        logger.info("REFERENCE file scheduled for RAG indexing: %s", result.object_id)

    return {
        "success": result.success,
        "object_type": result.object_type,
        "object_id": result.object_id,
        "reply": reply_text,
        "error_code": result.error_code,
        "needs_review": False,
    }


async def _index_reference_file(file_id: str, tei_client, kc_repo, file_manager=None) -> None:
    """异步入 RAG 通用参考库。失败标 rag_indexed=False。

    Args:
        file_id: 文件 UUID
        tei_client: TeiClient 实例
        kc_repo: KnowledgeChunkRepo 实例
        file_manager: FileManager 实例（用于 set_rag_indexed）
    """
    try:
        from ..infrastructure.database.session import get_session
        from ..infrastructure.database.models import File

        # 取本地路径
        with get_session() as session:
            f = session.query(File).filter(File.id == file_id).first()
            if not f or not f.storage_path:
                logger.debug("REFERENCE file %s has no storage_path, skip RAG indexing", file_id)
                return
            storage_path = f.storage_path
            file_no = f.file_no
            # 还原为绝对路径
            from pathlib import Path
            import os
            from emily_core.services.file_storage_service import FileStorageService
            storage_root = str(FileStorageService()._storage_root)
            local_path = str(Path(storage_root) / storage_path)
            if not os.path.exists(local_path):
                logger.debug("REFERENCE file %s local path not found: %s", file_id, local_path)
                return

        # 简单文本提取：尝试读取文件内容（非二进制）
        try:
            text_content = Path(local_path).read_text(encoding="utf-8")
        except Exception:
            logger.debug("REFERENCE file %s cannot be read as text, skipping chunk-based indexing", file_id)
            return

        if not text_content.strip():
            return

        # 简单分块（按段落）
        chunks = [{"text": p.strip(), "index": i} for i, p in enumerate(text_content.split("\n\n")) if p.strip()]
        if not chunks:
            return

        # 调 embed_and_index（通用库）
        from .embed_tool import handle_embed_and_index
        await handle_embed_and_index({
            "chunks": chunks,
            "doc_metadata": {"doc_id": file_id, "doc_name": f.file_no or file_id, "purpose": "REFERENCE"},
        }, tei=tei_client, repo=kc_repo)

        # 标记成功
        if file_manager is not None:
            file_manager.set_rag_indexed(file_id, True, "general_reference")
        logger.info("REFERENCE file indexed to RAG: %s", file_no or file_id)

    except Exception as e:
        logger.warning("REFERENCE RAG indexing failed: %s — %s", file_id, e)
        # 失败 rag_indexed 保持 False，留兜底重试


# ══════════════════════════════════════════════════════════════════════════════
# 文件查询工具 — query_files
# ══════════════════════════════════════════════════════════════════════════════

_QUERY_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "file_category": {
            "type": "string",
            "description": "按分类过滤：PROJECT_LICENSE/CONTRACT/WORK_RECORD/PHASE_DELIVERABLE/PROCESS_DOC/MANAGEMENT_SPEC/OTHER",
        },
        "keyword": {
            "type": "string",
            "description": "按文件名关键词搜索（模糊匹配）",
        },
        "project_id": {
            "type": "string",
            "description": "项目 UUID（可选，默认取当前用户项目）",
        },
        "limit": {
            "type": "integer",
            "description": "返回数量上限，默认 10",
            "default": 10,
        },
    },
}

_QUERY_FILES_DESCRIPTION = (
    "按分类或关键词查询项目文件列表。\n"
    "\n"
    "字段分级：\n"
    "  [应有] file_category — 按业务分类过滤（如 CONTRACT 查承包合同类）\n"
    "  [应有] keyword — 按文件名关键词模糊搜索\n"
    "  [可选] project_id — 指定项目范围\n"
    "  [可选] limit — 返回数量上限"
)


async def handle_query_files(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    project_ids: list[str] | None = None,
    **kwargs,
) -> dict:
    """处理文件分类查询。"""
    file_category = params.get("file_category")
    keyword = params.get("keyword", "")
    project_id = params.get("project_id")
    limit = params.get("limit", 10)

    result = await file_app.handle_list_by_category(
        file_category=file_category,
        project_id=project_id,
        project_ids=project_ids,
        keyword=keyword,
        limit=limit,
        user_id=user_id,  # M1: 传递 user_id 走权限统一出口
    )

    return {
        "success": result.success,
        "reply": result.reply,
        "data": getattr(result, 'data', {}),
        "error_code": result.error_code,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 文件分类修改工具 — update_file_category
# ══════════════════════════════════════════════════════════════════════════════

_UPDATE_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
        "file_category": {
            "type": "string",
            "description": "目标分类：PROJECT_LICENSE/CONTRACT/WORK_RECORD/PHASE_DELIVERABLE/PROCESS_DOC/MANAGEMENT_SPEC/OTHER",
        },
    },
    "required": ["file_no", "file_category"],
}

_UPDATE_CATEGORY_DESCRIPTION = (
    "修改文件的分类归属。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号\n"
    "  file_category — 目标分类枚举值"
)


async def handle_update_file_category(
    params: dict,
    file_app: FileApplication,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """处理文件分类修改。"""
    file_no = params.get("file_no", "")
    file_category = params.get("file_category", "OTHER")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号", "error_code": "missing_file_no"}

    result = await file_app.handle_update_category(
        file_no=file_no,
        file_category=file_category,
        user_id=user_id,
    )

    return {
        "success": result.success,
        "reply": result.reply,
        "data": getattr(result, 'data', {}),
        "error_code": result.error_code,
    }


# ══════════════════════════════════════════════════════════════════════════════
# M2: send_file 工具 — Emily 主动发送文件
# ══════════════════════════════════════════════════════════════════════════════

_SEND_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001），必填",
        },
        "caption": {
            "type": "string",
            "description": "附带的文字说明（可选）",
        },
    },
    "required": ["file_no"],
}

_SEND_FILE_DESCRIPTION = (
    "向当前会话用户发送一个 Emily 已有的文件。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号（必须是用户有权访问的文件）\n"
    "可选字段：\n"
    "  caption — 附带文字说明"
)


async def handle_send_file(
    params: dict,
    file_manager=None,      # FileManager 注入
    outbound_bus=None,      # OutboundEventBus 注入
    user_id: str = "",
    message_id: str = "",
    conversation_id: str = "",
    **kwargs,
) -> dict:
    """处理 send_file 工具调用。

    流程：权限校验 → 解析本地路径 → publish file_send 出站事件。
    """
    file_no = params.get("file_no", "")
    caption = params.get("caption", "")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号 (file_no)", "error_code": "missing_file_no"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    # 1. 解析 file_no → file_id
    file_record = file_manager.get_by_file_no(file_no)
    if file_record is None:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    # 2. 权限校验（fail-closed）
    if not file_manager.can_access(user_id, file_record.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    # 3. 解析本地路径
    local_path = file_manager.resolve_local_path(file_no)
    if not local_path:
        return {"success": False, "reply": f"文件 {file_no} 未在本地存储", "error_code": "file_not_stored"}

    # 4. publish file_send 出站事件
    if outbound_bus is not None:
        outbound_bus.publish("file_send", {
            "conversation_id": conversation_id,
            "file_paths": [{"path": local_path, "name": file_record.filename}],
            "caption": caption,
        })
        logger.info("file_send event published: %s → %s", file_no, conversation_id)

    return {
        "success": True,
        "reply": f"已发送文件：{file_record.filename}（{file_no}）",
    }


# ══════════════════════════════════════════════════════════════════════════════
# M4: 文件关联与版本工具
# ══════════════════════════════════════════════════════════════════════════════

# ── link_file ──

_LINK_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
        "module_id": {
            "type": "string",
            "description": "目标模块 ID（节点ID/事件ID/会议ID等）",
        },
        "module_type": {
            "type": "string",
            "description": "目标模块类型：NODE_STARTUP_DOC/NODE_WORKLOAD_DOC/NODE_DELIVERABLE_DOC/NODE_ATTACHMENT/EVENT_DOC/MEETING_DOC",
        },
    },
    "required": ["file_no", "module_id", "module_type"],
}

_LINK_FILE_DESCRIPTION = (
    "将文件关联到业务对象（节点/事件/会议等）。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号\n"
    "  module_id — 目标模块 ID\n"
    "  module_type — 模块类型枚举值"
)


async def handle_link_file(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """关联文件到业务对象。"""
    file_no = params.get("file_no", "")
    module_id = params.get("module_id", "")
    module_type = params.get("module_type", "")

    if not file_no or not module_id or not module_type:
        return {"success": False, "reply": "请提供 file_no、module_id 和 module_type", "error_code": "missing_params"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    file_record = file_manager.get_by_file_no(file_no)
    if file_record is None:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, file_record.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    result = file_manager.link_to_module(file_record.id, module_id, module_type, user_id)
    if result is None:
        return {"success": False, "reply": "文件关联失败", "error_code": "link_failed"}

    return {
        "success": True,
        "reply": f"已关联文件 {file_no} 到 {module_type}({module_id})",
    }


# ── new_file_version ──

_NEW_FILE_VERSION_SCHEMA = {
    "type": "object",
    "properties": {
        "parent_file_no": {
            "type": "string",
            "description": "父版本文件编号（如 FIL-20260709-0001）",
        },
        "version_label": {
            "type": "string",
            "description": "新版本标签（如 V2.0、V2.1）",
        },
        "new_filename": {
            "type": "string",
            "description": "新版本文件名",
        },
        "file_type": {
            "type": "string",
            "description": "文件类型",
        },
    },
    "required": ["parent_file_no", "version_label", "new_filename"],
}

_NEW_FILE_VERSION_DESCRIPTION = (
    "创建文件的新版本（旧版本将标记为非最新）。\n"
    "\n"
    "必填字段：\n"
    "  parent_file_no — 被升版的父文件编号\n"
    "  version_label — 新版本标签\n"
    "  new_filename — 新版本文件名\n"
    "可选字段：\n"
    "  file_type — 文件类型"
)


async def handle_new_file_version(
    params: dict,
    file_app: FileApplication,
    file_manager=None,
    user_id: str = "",
    message_id: str = "",
    **kwargs,
) -> dict:
    """创建文件新版本。"""
    parent_file_no = params.get("parent_file_no", "")
    version_label = params.get("version_label", "")
    new_filename = params.get("new_filename", "")
    file_type = params.get("file_type", "")

    if not parent_file_no or not version_label or not new_filename:
        return {"success": False, "reply": "请提供 parent_file_no、version_label 和 new_filename",
                "error_code": "missing_params"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    parent = file_manager.get_by_file_no(parent_file_no)
    if parent is None:
        return {"success": False, "reply": f"找不到父文件编号 {parent_file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, parent.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    # 先用 FileService 创建新文件记录
    from ..adapters.standard.command import FileCommand
    cmd = FileCommand(
        project_id=parent.project_id,
        filename=new_filename,
        file_type=file_type or parent.file_type or "",
        uploaded_by=user_id,
    )
    new_file = file_app.file_service.create_file_record(cmd)

    # 创建版本关联
    result = file_manager.create_version(parent_file_no, new_file.id, version_label, user_id)
    if result is None:
        return {"success": False, "reply": "版本创建失败", "error_code": "version_create_failed"}

    return {
        "success": True,
        "reply": f"已为 {parent_file_no} 创建新版本 {version_label} → {new_file.file_no}",
    }


# ── delete_file ──

_DELETE_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
    },
    "required": ["file_no"],
}

_DELETE_FILE_DESCRIPTION = (
    "软删除一个文件（标记为已删除，不删除物理文件）。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号"
)


async def handle_delete_file(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """软删除文件。"""
    file_no = params.get("file_no", "")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号 (file_no)", "error_code": "missing_file_no"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    file_record = file_manager.get_by_file_no(file_no)
    if file_record is None:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, file_record.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    ok = file_manager.soft_delete(file_record.id, user_id)
    if not ok:
        return {"success": False, "reply": "文件删除失败", "error_code": "delete_failed"}

    return {
        "success": True,
        "reply": f"已删除文件：{file_record.filename}（{file_no}）",
    }


# ── list_file_versions ──

_LIST_FILE_VERSIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
    },
    "required": ["file_no"],
}

_LIST_FILE_VERSIONS_DESCRIPTION = (
    "列出指定文件的所有版本。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号"
)


async def handle_list_file_versions(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """列出文件所有版本。"""
    file_no = params.get("file_no", "")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号 (file_no)", "error_code": "missing_file_no"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    file_record = file_manager.get_by_file_no(file_no)
    if file_record is None:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, file_record.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    versions = file_manager.list_versions(file_no)
    if not versions:
        return {"success": True, "reply": f"文件 {file_no} 暂无版本记录", "data": {"versions": []}}

    version_list = []
    for v in versions:
        version_list.append({
            "file_no": v.file_no,
            "filename": v.filename,
            "version": v.version or "V1.0",
            "is_latest": v.is_latest,
        })

    lines = [f"文件 {file_no} 的版本列表（{len(versions)} 个）", "──────────────"]
    for i, v in enumerate(version_list, 1):
        latest_tag = " [最新]" if v["is_latest"] else ""
        lines.append(f"{i}. {v['version']} — {v['filename']}（{v['file_no']}）{latest_tag}")

    return {
        "success": True,
        "reply": "\n".join(lines),
        "data": {"versions": version_list},
    }


# ══════════════════════════════════════════════════════════════════════════════
# M5 附件链工具 — link_to_master / unlink_attachment / list_attachments
# ══════════════════════════════════════════════════════════════════════════════

# ── link_to_master ──

_LINK_TO_MASTER_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "要挂载的附件文件编号（如 FIL-20260709-0001）",
        },
        "master_file_no": {
            "type": "string",
            "description": "主文件编号（如 FIL-20260709-0002）",
        },
    },
    "required": ["file_no", "master_file_no"],
}

_LINK_TO_MASTER_DESCRIPTION = (
    "将文件挂载为另一文件的附件（主从附件链）。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 附件文件编号\n"
    "  master_file_no — 主文件编号\n"
    "\n"
    "规则：禁止嵌套（主文件本身不能是附件）；禁止自挂。"
)


async def handle_link_to_master(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """挂载附件到主文件。"""
    file_no = params.get("file_no", "")
    master_no = params.get("master_file_no", "")

    if not file_no or not master_no:
        return {"success": False, "reply": "请提供 file_no 和 master_file_no", "error_code": "missing_params"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    f = file_manager.get_by_file_no(file_no)
    m = file_manager.get_by_file_no(master_no)
    if not f:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}
    if not m:
        return {"success": False, "reply": f"找不到主文件编号 {master_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, f.id):
        return {"success": False, "reply": "您无权访问附件文件", "error_code": "permission_denied"}
    if not file_manager.can_access(user_id, m.id):
        return {"success": False, "reply": "您无权访问主文件", "error_code": "permission_denied"}

    result = file_manager.link_to_master(f.id, m.id, operator_id=user_id)
    if not result["success"]:
        return {"success": False, "reply": result["error"], "error_code": "link_failed"}

    return {
        "success": True,
        "reply": f"✅ 已挂载 {file_no} → {master_no}",
    }


# ── unlink_attachment ──

_UNLINK_ATTACHMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "要卸载的附件文件编号",
        },
    },
    "required": ["file_no"],
}

_UNLINK_ATTACHMENT_DESCRIPTION = (
    "卸载附件，将其提升为独立文件。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 要卸载的附件文件编号"
)


async def handle_unlink_attachment(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """卸载附件为独立文件。"""
    file_no = params.get("file_no", "")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号 file_no", "error_code": "missing_file_no"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    f = file_manager.get_by_file_no(file_no)
    if not f:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, f.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    result = file_manager.unlink_attachment(f.id, operator_id=user_id)
    if not result["success"]:
        return {"success": False, "reply": result["error"], "error_code": "unlink_failed"}

    return {
        "success": True,
        "reply": f"✅ 已卸载 {file_no}，现在是独立文件",
    }


# ── list_attachments ──

_LIST_ATTACHMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "master_file_no": {
            "type": "string",
            "description": "主文件编号",
        },
    },
    "required": ["master_file_no"],
}

_LIST_ATTACHMENTS_DESCRIPTION = (
    "列出主文件下的所有附件。\n"
    "\n"
    "必填字段：\n"
    "  master_file_no — 主文件编号"
)


async def handle_list_attachments(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """列出主文件下的附件。"""
    master_no = params.get("master_file_no", "")

    if not master_no:
        return {"success": False, "reply": "请提供主文件编号 master_file_no", "error_code": "missing_params"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    m = file_manager.get_by_file_no(master_no)
    if not m:
        return {"success": False, "reply": f"找不到主文件编号 {master_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, m.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    attachments = file_manager.list_attachments(m.id)
    if not attachments:
        return {"success": True, "reply": f"文件 {master_no} 暂无附件", "data": {"attachments": []}}

    att_list = []
    for a in attachments:
        att_list.append({
            "file_no": a.file_no,
            "filename": a.filename,
            "file_type": a.file_type or "",
        })

    lines = [f"📎 文件 {master_no} 的附件列表（{len(attachments)} 个）", "──────────────"]
    for i, a in enumerate(att_list, 1):
        lines.append(f"{i}. {a['filename']}（{a['file_no']}）")

    return {
        "success": True,
        "reply": "\n".join(lines),
        "data": {"attachments": att_list},
    }


# ══════════════════════════════════════════════════════════════════════════════
# M5 purpose 校正工具 — update_file_purpose
# ══════════════════════════════════════════════════════════════════════════════

_UPDATE_PURPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "file_no": {
            "type": "string",
            "description": "文件编号（如 FIL-20260709-0001）",
        },
        "purpose": {
            "type": "string",
            "description": "目标业务意图：EVIDENCE(凭证)/RECORD(记录)/DESIGN(图纸)/REFERENCE(参考)",
        },
    },
    "required": ["file_no", "purpose"],
}

_UPDATE_PURPOSE_DESCRIPTION = (
    "校正文件的业务意图（purpose）。\n"
    "\n"
    "必填字段：\n"
    "  file_no — 文件编号\n"
    "  purpose — 目标意图：EVIDENCE/RECORD/DESIGN/REFERENCE\n"
    "\n"
    "用途：当规则引擎或 LLM 初次判断的 purpose 有误时，用户可手动纠正。\n"
    "纠正后 purpose_confirmed 标为 True。"
)


async def handle_update_file_purpose(
    params: dict,
    file_manager=None,
    user_id: str = "",
    **kwargs,
) -> dict:
    """校正文件的 purpose。"""
    file_no = params.get("file_no", "")
    purpose = params.get("purpose", "RECORD")

    if not file_no:
        return {"success": False, "reply": "请提供文件编号 file_no", "error_code": "missing_file_no"}

    if file_manager is None:
        return {"success": False, "reply": "文件服务未就绪", "error_code": "service_unavailable"}

    f = file_manager.get_by_file_no(file_no)
    if not f:
        return {"success": False, "reply": f"找不到文件编号 {file_no}", "error_code": "file_not_found"}

    if not file_manager.can_access(user_id, f.id):
        return {"success": False, "reply": "您无权访问该文件", "error_code": "permission_denied"}

    result = file_manager.update_purpose(f.id, purpose, operator_id=user_id)
    if not result["success"]:
        return {"success": False, "reply": result["error"], "error_code": "update_failed"}

    from ..infrastructure.database.models import FilePurpose
    purpose_display = FilePurpose.display(result["purpose"])
    return {
        "success": True,
        "reply": f"✅ 文件 {file_no} 的 purpose 已改为 {purpose_display}（{result['purpose']}）",
    }
