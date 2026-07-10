"""SystemDescriptionBuilder —— 系统自我描述构建服务。

查询 ORM 元数据 + 权限定义 + 文件分类，生成三域结构化 JSON + 纯文本摘要。
纯数据驱动，无需 LLM。

参照模式：emily_core/services/world_book_builder.py
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ..infrastructure.database.session import get_session
from ..infrastructure.database.models import (
    Base, PublicFieldRegistry, File, FileCategory,
)

logger = logging.getLogger("emily.system_description_builder")

# ── 表名中文映射（db_perms 可见的 5 张表 + 常见补充表）──
_TABLE_DISPLAY_NAMES = {
    "project": "项目表",
    "events": "事件表",
    "tasks": "任务表",
    "meetings": "会议表",
    "files": "文件表",
    "messages": "消息表",
    "users": "用户表",
    "financial": "财务表",
    "project_nodes": "节点表",
    "node_deliverables": "节点成果表",
    "companies": "单位表",
    "conversations": "会话表",
    "permissions": "权限表",
    "permission_grants": "授权表",
    "scheduler_jobs": "调度作业表",
    "scheduler_executions": "调度执行表",
}

# ── 表描述映射 ──
_TABLE_DESCRIPTIONS = {
    "project": "项目基本信息",
    "events": "记录项目现场发生的事件",
    "tasks": "跟踪工作任务的执行情况",
    "meetings": "记录会议安排和纪要",
    "files": "项目文件归档",
    "messages": "对话消息记录",
    "users": "用户账户信息",
    "financial": "财务数据",
    "project_nodes": "全景节点树（工作分解结构）",
    "node_deliverables": "节点成果交付物",
    "companies": "参建单位信息",
    "conversations": "IM 会话记录",
    "permissions": "权限规则定义",
    "permission_grants": "跨线授权记录",
    "scheduler_jobs": "系统调度作业",
    "scheduler_executions": "调度执行日志",
}

# ── 需要提取核心字段的可见表（与 db_perms 对齐）──
_VISIBLE_TABLES = {"project", "events", "tasks", "meetings", "files", "financial"}

# ── 每张可见表的核心字段筛选（避免全量字段过载）──
_KEY_FIELDS = {
    "project": {"name", "code", "address", "status", "lifecycle_stage"},
    "events": {"title", "event_type", "event_date", "created_by", "project_id"},
    "tasks": {"title", "status", "node_id", "assignee_id", "project_id"},
    "meetings": {"title", "meeting_type", "meeting_date", "project_id", "created_by"},
    "files": {"filename", "file_type", "file_category", "confidentiality", "project_id", "uploaded_by"},
    "financial": {"title", "amount", "project_id", "category"},
}

# ── 关键表间关系（手工标注最重要的业务关系）──
_KEY_RELATIONS = [
    {"from": "events.project_id", "to": "projects.id", "description": "每个事件属于一个项目"},
    {"from": "tasks.node_id", "to": "project_nodes.node_id", "description": "每个任务关联一个全景节点"},
    {"from": "files.project_id", "to": "projects.id", "description": "每个文件属于一个项目"},
    {"from": "files.uploaded_by", "to": "users.id", "description": "文件上传人"},
    {"from": "users.company", "to": "companies.id", "description": "用户归属参建单位"},
    {"from": "events.created_by", "to": "users.id", "description": "事件记录人"},
    {"from": "files.file_category", "to": "FileCategory 枚举", "description": "文件按分类体系归档"},
]


class SystemDescriptionBuilder:
    """系统自我描述构建器——构建 D1/D2/D3 三域内容。"""

    def build(self, *, generated_by: str = "manual", dry_run: bool = False) -> dict:
        """构建/重建系统自我描述。

        Args:
            generated_by: 生成来源标记
            dry_run: 预览模式，不写 DB

        Returns:
            构建结果 dict，含 content_json, content_text, schema_hash 等
        """
        # 采集三域数据
        database = self._build_database()
        file_cognition = self._build_file()
        permission = self._build_permission()

        content_json = {
            "database": database,
            "file": file_cognition,
            "permission": permission,
        }

        # 计算 hash（用于偏差检测）
        schema_hash = self._compute_schema_hash()
        permission_hash = self._compute_permission_hash()
        file_model_hash = self._compute_file_model_hash()

        content_text = self._format_content_text(content_json)

        # 估算 token 数（中文约 1.5 字/token）
        token_count = int(len(content_text) / 1.5) if content_text else 0

        # 每域初始版本号
        domain_versions = {
            "database": 1,
            "file": 1,
            "permission": 1,
        }

        result = {
            "content_json": json.dumps(content_json, ensure_ascii=False),
            "content_text": content_text,
            "domain_versions": json.dumps(domain_versions),
            "schema_hash": schema_hash,
            "permission_hash": permission_hash,
            "file_model_hash": file_model_hash,
            "token_count": token_count,
            "generated_by": generated_by,
            "status": "preview" if dry_run else "built",
        }

        if not dry_run:
            from ..repositories.system_description_repo import SystemDescriptionRepo

            existing = SystemDescriptionRepo.get_latest()
            if existing:
                # 更新：递增版本号
                new_version = existing.version + 1
                # 合并 domain_versions：已有域版本递增，新域版本为1
                old_dv = json.loads(existing.domain_versions or "{}")
                merged_dv = {**old_dv}
                for k in domain_versions:
                    if k in merged_dv:
                        merged_dv[k] += 1
                    else:
                        merged_dv[k] = 1

                SystemDescriptionRepo.update_content(
                    content_json=result["content_json"],
                    content_text=content_text,
                    domain_versions=json.dumps(merged_dv),
                    version=new_version,
                    schema_hash=schema_hash,
                    permission_hash=permission_hash,
                    file_model_hash=file_model_hash,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = new_version
            else:
                desc = SystemDescriptionRepo.create(
                    content_json=result["content_json"],
                    content_text=content_text,
                    domain_versions=result["domain_versions"],
                    schema_hash=schema_hash,
                    permission_hash=permission_hash,
                    file_model_hash=file_model_hash,
                    token_count=token_count,
                    generated_by=generated_by,
                )
                result["version"] = 1

        return result

    # ══════════════════════════════════════════════════════════════════════════
    # D1: 数据库认知
    # ══════════════════════════════════════════════════════════════════════════

    def _build_database(self) -> dict:
        """D1：数据库认知——表清单 + 可见表核心字段 + 表间关系。"""
        try:
            tables_info = self._reflect_tables()
            visible_tables = tables_info["visible_tables"]
            all_tables = tables_info["all_tables"]

            # 关键表间关系
            key_relations = _KEY_RELATIONS

            return {
                "total_tables": all_tables["total"],
                "visible_tables": visible_tables,
                "hidden_table_count": all_tables["total"] - len(visible_tables),
                "key_relations": key_relations,
            }
        except Exception as e:
            logger.error("_build_database failed: %s", e)
            return {"total_tables": 0, "visible_tables": [], "hidden_table_count": 0, "key_relations": [], "error": str(e)}

    def _reflect_tables(self) -> dict:
        """反射 ORM 元数据获取表清单和字段信息。"""
        # 从 Base.metadata 获取所有表
        all_table_names = sorted(Base.metadata.tables.keys())
        total = len(all_table_names)

        # 构建可见表信息（_VISIBLE_TABLES 中的表 + db_perms 会覆盖的表）
        visible_tables = []
        for tbl_name in _VISIBLE_TABLES:
            if tbl_name not in Base.metadata.tables:
                continue

            table = Base.metadata.tables[tbl_name]
            display_name = _TABLE_DISPLAY_NAMES.get(tbl_name, tbl_name)
            description = _TABLE_DESCRIPTIONS.get(tbl_name, "")

            # 提取核心字段
            key_fields = self._extract_key_fields(tbl_name, table)

            # 查询 PublicFieldRegistry 获取字段描述
            field_descriptions = self._load_field_descriptions(tbl_name)

            # 合并描述
            for field in key_fields:
                if not field.get("description") and field["name"] in field_descriptions:
                    field["description"] = field_descriptions[field["name"]]

            visible_tables.append({
                "table_name": tbl_name,
                "display_name": display_name,
                "description": description,
                "key_fields": key_fields,
            })

        return {
            "visible_tables": visible_tables,
            "all_tables": {"total": total, "names": all_table_names},
        }

    def _extract_key_fields(self, tbl_name: str, table) -> list[dict]:
        """提取表的核心字段信息。"""
        selected = _KEY_FIELDS.get(tbl_name, set())
        fields = []
        for col in table.columns:
            if selected and col.name not in selected:
                continue
            field = {
                "name": col.name,
                "type": self._simplify_type(col.type),
                "required": not col.nullable if col.nullable is not None else False,
                "description": col.comment or "",
            }
            # FK 标注
            for fk in col.foreign_keys:
                target = str(fk.target_fullname)
                field["type"] = f"FK\u2192{target.split('.')[0]}"
                break
            fields.append(field)
        return fields

    @staticmethod
    def _simplify_type(col_type) -> str:
        """将 SQLAlchemy 类型简化为可读名称。"""
        type_name = type(col_type).__name__
        mapping = {
            "String": "\u6587\u672c",
            "Text": "\u957f\u6587\u672c",
            "Integer": "\u6574\u6570",
            "BigInteger": "\u5927\u6574\u6570",
            "Float": "\u6d6e\u70b9\u6570",
            "Boolean": "\u5e03\u5c14",
            "DateTime": "\u65e5\u671f\u65f6\u95f4",
            "Date": "\u65e5\u671f",
            "Numeric": "\u6570\u503c",
            "JSON": "JSON",
            "Enum": "\u679a\u4e3e",
        }
        return mapping.get(type_name, type_name)

    @staticmethod
    def _load_field_descriptions(model_name: str) -> dict[str, str]:
        """从 PublicFieldRegistry 查询字段描述（三层 fallback 第1层）。"""
        try:
            with get_session() as session:
                records = session.query(PublicFieldRegistry).filter(
                    PublicFieldRegistry.model_name == model_name,
                    PublicFieldRegistry.is_deleted == False,
                ).all()
                return {r.field_name: r.description for r in records if r.description}
        except Exception as e:
            logger.debug("_load_field_descriptions failed for %s: %s", model_name, e)
            return {}

    # ══════════════════════════════════════════════════════════════════════════
    # D2: 文件认知
    # ══════════════════════════════════════════════════════════════════════════

    def _build_file(self) -> dict:
        """D2：文件认知——分类体系 + files 表核心字段 + 访问规则。"""
        try:
            # 分类体系
            categories = []
            for cat_value in FileCategory.ALL:
                categories.append({
                    "name": FileCategory.DISPLAY_NAMES.get(cat_value, cat_value),
                    "value": cat_value,
                })

            # files 表核心字段
            if "files" in Base.metadata.tables:
                file_table = Base.metadata.tables["files"]
                file_key_fields = ["filename", "file_type", "file_category", "confidentiality", "project_id", "uploaded_by"]
                key_fields = []
                for col in file_table.columns:
                    if col.name not in file_key_fields:
                        continue
                    field = {
                        "name": col.name,
                        "type": self._simplify_type(col.type),
                        "required": not col.nullable if col.nullable is not None else False,
                        "description": col.comment or "",
                    }
                    for fk in col.foreign_keys:
                        target = str(fk.target_fullname)
                        field["type"] = f"FK\u2192{target.split('.')[0]}"
                        break
                    key_fields.append(field)
            else:
                key_fields = []

            # 文件访问规则（基于 confidentiality 字段）
            access_rules = [
                "confidentiality=0 公开文件所有用户可见",
                "confidentiality\u22651 内部/机密/绝密文件按权限级别控制",
                "上传人始终可查看自己上传的文件",
                "L5+ 管理员可查看所有文件",
            ]

            return {
                "categories": categories,
                "key_fields": key_fields,
                "access_rules": access_rules,
            }
        except Exception as e:
            logger.error("_build_file failed: %s", e)
            return {"categories": [], "key_fields": [], "access_rules": [], "error": str(e)}

    # ══════════════════════════════════════════════════════════════════════════
    # D3: 权限认知
    # ══════════════════════════════════════════════════════════════════════════

    def _build_permission(self) -> dict:
        """D3：权限认知——6 级树形结构 + 各级能力 + 继承链。"""
        try:
            from ..permission.level import (
                PermissionLevel, INHERITANCE_CHAIN, LEVEL_NAME,
            )

            # 6 级定义
            levels = []
            # 确定每级属于哪条线
            line_map = {
                1: "\u516c\u5171",
                2: "\u53c2\u5efa\u7ebf",
                3: "\u53c2\u5efa\u7ebf",
                4: "\u5efa\u8bbe\u7ebf",
                5: "\u5efa\u8bbe\u7ebf",
                6: "\u5efa\u8bbe\u7ebf",
            }
            # 各级能力概述
            capability_map = {
                1: "\u57fa\u672c\u67e5\u8be2\u3001\u516c\u5f00\u4fe1\u606f\u67e5\u770b",
                2: "\u5f55\u5165\u81ea\u5df1\u8d1f\u8d23\u7684\u4e8b\u4ef6/\u4efb\u52a1\u3001\u67e5\u770b\u9879\u76ee\u516c\u5f00\u4fe1\u606f",
                3: "\u7ba1\u7406\u53c2\u5efa\u5355\u4f4d\u5185\u4eba\u5458\u3001\u5ba1\u6838\u53c2\u5efa\u65b9\u63d0\u4ea4",
                4: "\u7ba1\u7406\u5168\u666f\u8282\u70b9\u3001\u5ba1\u6279\u8de8\u5355\u4f4d\u4e8b\u9879",
                5: "\u5168\u5c40\u7ba1\u7406\u3001\u6743\u9650\u6388\u4e88\u3001\u6570\u636e\u8131\u654f\u89c4\u5219\u914d\u7f6e",
                6: "\u7cfb\u7edf\u914d\u7f6e\u3001\u6743\u9650\u89c4\u5219\u5b9a\u4e49\u3001\u5f3a\u5236\u64a4\u9500",
            }

            for level_value in sorted(INHERITANCE_CHAIN.keys()):
                levels.append({
                    "level": level_value,
                    "name": LEVEL_NAME.get(level_value, "\u672a\u77e5"),
                    "line": line_map.get(level_value, ""),
                    "capabilities": capability_map.get(level_value, ""),
                })

            # 继承链
            inheritance = {
                "\u53c2\u5efa\u7ebf": sorted(list(INHERITANCE_CHAIN.get(3, frozenset()))),
                "\u5efa\u8bbe\u7ebf": sorted(list(INHERITANCE_CHAIN.get(6, frozenset()))),
                "note": "L4 \u4e0d\u7ee7\u627f L2/L3\uff0c\u4e24\u7ebf\u5728 L1 \u540e\u5206\u53c9",
            }

            # 跨线机制
            cross_line_mechanism = "\u8de8\u7ebf\u8bbf\u95ee\u901a\u8fc7 PermissionGrant\uff08\u4e34\u65f6/\u6c38\u4e45\u6388\u6743\uff09\u5b9e\u73b0"

            return {
                "levels": levels,
                "inheritance": inheritance,
                "cross_line_mechanism": cross_line_mechanism,
            }
        except Exception as e:
            logger.error("_build_permission failed: %s", e)
            return {"levels": [], "inheritance": {}, "cross_line_mechanism": "", "error": str(e)}

    # ══════════════════════════════════════════════════════════════════════════
    # Hash 计算（用于偏差检测）
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_schema_hash() -> str:
        """计算数据库 schema 的 SHA-256 hash。序列化 Base.metadata.tables 为规范字符串。"""
        import hashlib
        parts = []
        for tbl_name in sorted(Base.metadata.tables.keys()):
            table = Base.metadata.tables[tbl_name]
            col_parts = []
            for col in table.columns:
                col_parts.append(f"{col.name}:{type(col.type).__name__}:{col.nullable}")
            parts.append(f"{tbl_name}({','.join(sorted(col_parts))})")
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_permission_hash() -> str:
        """计算权限体系的 SHA-256 hash。序列化 PermissionLevel + INHERITANCE_CHAIN。"""
        import hashlib
        from ..permission.level import PermissionLevel, INHERITANCE_CHAIN, LEVEL_NAME
        parts = []
        for level in sorted(PermissionLevel, key=lambda x: x.value):
            parts.append(f"{level.name}={level.value}")
        for k in sorted(INHERITANCE_CHAIN.keys()):
            parts.append(f"chain_{k}={sorted(INHERITANCE_CHAIN[k])}")
        for k in sorted(LEVEL_NAME.keys()):
            parts.append(f"name_{k}={LEVEL_NAME[k]}")
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_file_model_hash() -> str:
        """计算文件模型的 SHA-256 hash。序列化 FileCategory + File 表字段。"""
        import hashlib
        parts = []
        # FileCategory 枚举
        for cat in FileCategory.ALL:
            parts.append(f"cat_{cat}={FileCategory.DISPLAY_NAMES.get(cat, cat)}")
        # File 表字段
        if "files" in Base.metadata.tables:
            file_table = Base.metadata.tables["files"]
            for col in file_table.columns:
                parts.append(f"file_{col.name}:{type(col.type).__name__}")
        canonical = "|".join(sorted(parts))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ══════════════════════════════════════════════════════════════════════════
    # 文本格式化（content_text）
    # ══════════════════════════════════════════════════════════════════════════

    def _format_content_text(self, content_json: dict) -> str:
        """将三域 JSON 格式化为纯文本摘要（注入 prompt）。目标 ~400 tokens ≈ 600 字。"""
        lines = []

        # D1: 数据库认知
        db = content_json.get("database", {})
        visible = db.get("visible_tables", [])
        if visible:
            lines.append("\U0001f4ca 数据库资源（你有权访问的表）")
            for tbl in visible:
                lines.append(f"\u00b7 {tbl['display_name']}: {tbl.get('description', '')}")
                # 核心字段（精简格式）
                fields = tbl.get("key_fields", [])
                if fields:
                    field_strs = []
                    for f in fields[:5]:
                        req_mark = ",\u5fc5\u586b" if f.get("required") else ""
                        type_str = f.get("type", "")
                        desc = f.get("description", "")
                        if desc:
                            field_strs.append(f"{f['name']}({desc}{req_mark})")
                        else:
                            field_strs.append(f"{f['name']}({type_str}{req_mark})")
                    lines.append(f"  \u6838\u5fc3\u5b57\u6bb5\uff1a{' / '.join(field_strs)}")

            hidden_count = db.get("hidden_table_count", 0)
            total = db.get("total_tables", 0)
            lines.append(f"\uff08\u53e6\u6709 {hidden_count} \u5f20\u7cfb\u7edf\u8868\uff0c\u5171 {total} \u5f20\uff09")

            # 表间关系
            relations = db.get("key_relations", [])
            if relations:
                rel_strs = [f"{r['from']} \u2192 {r['to']}" for r in relations[:5]]
                lines.append("\U0001f517 表间关系")
                lines.append(" / ".join(rel_strs))

        # D2: 文件认知
        fc = content_json.get("file", {})
        categories = fc.get("categories", [])
        if categories:
            lines.append("")
            lines.append("\U0001f4c1 文件分类体系")
            cat_strs = [f"{c['name']}" for c in categories]
            lines.append(" / ".join(cat_strs))

        access_rules = fc.get("access_rules", [])
        if access_rules:
            lines.append("\u6587\u4ef6\u8bbf\u95ee\u89c4\u5219\uff1a" + "\uff1b".join(access_rules[:3]))

        # D3: 权限认知
        perm = content_json.get("permission", {})
        levels = perm.get("levels", [])
        if levels:
            lines.append("")
            lines.append("\U0001f510 权限分级体系")
            # 参建线
            participant_levels = [l for l in levels if l["line"] == "\u53c2\u5efa\u7ebf"]
            if participant_levels:
                pl_str = " \u2192 ".join(f"L{l['level']} {l['name']}" for l in participant_levels)
                lines.append(f"\u53c2\u5efa\u7ebf: L1 \u8bbf\u5ba2 \u2192 {pl_str}")
            # 建设线
            construction_levels = [l for l in levels if l["line"] == "\u5efa\u8bbe\u7ebf"]
            if construction_levels:
                cl_str = " \u2192 ".join(f"L{l['level']} {l['name']}" for l in construction_levels)
                lines.append(f"\u5efa\u8bbe\u7ebf: L1 \u8bbf\u5ba2 \u2192 {cl_str}")

            inheritance = perm.get("inheritance", {})
            note = inheritance.get("note", "") if isinstance(inheritance, dict) else ""
            if note:
                lines.append(f"\u26a0\ufe0f {note}\uff1b\u8de8\u7ebf\u8bbf\u95ee\u9700\u4e34\u65f6/\u6c38\u4e45\u6388\u6743")

        return "\n".join(lines)
