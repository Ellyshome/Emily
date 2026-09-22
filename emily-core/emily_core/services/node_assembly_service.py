"""node_assembly_service.py —— 对象声明解析 + 四类采集器 + 只读草稿。

职责边界（规格约束）：
  · **只读**：全程不写库、可反复调用（重复调用结果稳定）
  · **确定性**：不使用 LLM 语义挑选——类型归一 + 关键词包含匹配
  · **候选而非单值**：采集返回候选集合供人增删；前置成果按「整句优先 / 双向子串 / 取最长名」取最优
  · **可解释**：每条候选带 basis（依据模板哪一条声明、匹配到什么、为什么命中）
  · **未解析项单列**：声明了但零候选 → unresolved 回显，不静默丢弃
  · **越权即丢弃**：共享文件候选限定在操作人可见文件集合内

声明承载位：模板单元 `template.yaml` 的 `declarations` 块（不进索引，随内容按需读取）。

参照模式：emily_core/services/rule_book_loader.py（资产加载）+ repositories/participation_repo.py
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from ..infrastructure.paths import resolve_data_path
from .node_template_loader import NodeTemplateLoader, TemplateDetail, TemplateUnavailable

logger = logging.getLogger("emily.node_assembly_service")

BEIJING_TZ = timezone(timedelta(hours=8))

# 候选集合上限（超出标记 truncated，由人缩小关键词后重取）
MAX_CANDIDATES = 50

OBJECT_TYPES = ("participant_companies", "participant_users", "shared_files", "pre_conditions")

# 前置条件描述里成果名往往嵌在句子中，故用双向子串匹配；成果名过短不参与，避免噪声命中。
_DEP_MIN_NAME_LEN = 4

# 成果名的通用尾词：剥离后再比对以提高召回（如「立项批复文件」→「立项批复」）
_DELIV_SUFFIXES = (
    "文件", "文本", "报告", "汇总", "记录", "清单", "说明书", "材料", "文档",
)

_SYNONYM_DEV_RELATIVE = "emily-data/config/company_type_synonyms.yaml"
_SYNONYM_CONTAINER_PATH = "/app/config/company_type_synonyms.yaml"


# ══════════════════════════════════════════════════════════════════════════
# 文本匹配工具（自控制台路由层下沉，算法保持不变）
# ══════════════════════════════════════════════════════════════════════════

def _squash(s: str) -> str:
    """去掉空白与换行，便于子串比较。"""
    return re.sub(r"\s+", "", s or "")


def _match_keys(desc: str) -> list[str]:
    """从描述产出匹配键：整句 + 去掉「已…」状态尾句后的主体。

    「建设工程规划许可证已取得」→〔整句, 建设工程规划许可证〕
    「方案设计已获甲方批复」    →〔整句, 方案设计〕（成果名常是描述的主体部分）
    """
    key = _squash(desc)
    if not key:
        return []
    keys = [key]
    head = key.split("已", 1)[0]
    if head and head != key:
        keys.append(head)
    return keys


def _name_variants(name: str) -> list[str]:
    """成果名的比对变体：原名 + 去掉通用尾词后的核心词。"""
    out = [name]
    for suf in _DELIV_SUFFIXES:
        if name.endswith(suf) and len(name) - len(suf) >= _DEP_MIN_NAME_LEN:
            out.append(name[: -len(suf)])
    return out


def _match_precondition(desc: str, pool: list[dict]) -> Optional[dict]:
    """为一条前置条件描述在项目已有成果中找最合适的对应成果。

    整句优先（更具体），整句无命中再退到主体词；每轮按命中的名称长度取最长者。
    """
    for key in _match_keys(desc):
        if len(key) < _DEP_MIN_NAME_LEN:
            continue
        best: Optional[dict] = None
        best_len = 0
        for it in pool:
            for name in _name_variants(_squash(it.get("deliverable_name", ""))):
                if len(name) < _DEP_MIN_NAME_LEN:
                    continue
                if name in key or key in name:
                    if len(name) > best_len:
                        best, best_len = it, len(name)
                    break
        if best is not None:
            return best
    return None


# ══════════════════════════════════════════════════════════════════════════
# 企业类型同义词表
# ══════════════════════════════════════════════════════════════════════════

class SynonymTable:
    """企业类型同义词归一（人工维护的配置资产）。"""

    def __init__(self, path: str = ""):
        self.path = Path(path or resolve_data_path(
            "", _SYNONYM_CONTAINER_PATH, _SYNONYM_DEV_RELATIVE))
        self.standard_types: list[str] = []
        self.raw_map: dict[str, str] = {}     # 写法 → 标准类型
        self.loaded = False
        self.error = ""
        self._load()

    def _load(self) -> None:
        try:
            import yaml
            data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            self.error = f"同义词表加载失败（{e}）"
            logger.warning("SynonymTable load failed: %s", e)
            return
        self.standard_types = [str(x).strip() for x in (data.get("standard_types") or []) if str(x).strip()]
        for std, aliases in (data.get("synonyms") or {}).items():
            std = str(std).strip()
            if std and std not in self.standard_types:
                self.standard_types.append(std)
            self.raw_map[std] = std
            for a in (aliases or []):
                a = str(a).strip()
                if a:
                    self.raw_map[a] = std
        self.loaded = bool(self.standard_types)

    def normalize(self, raw: str) -> str:
        """把企业类型自由文本归一到标准类型；未知写法原样返回（不静默降级为标准值）。"""
        s = (raw or "").strip()
        if not s:
            return ""
        return self.raw_map.get(s, s)

    def is_known(self, raw: str) -> bool:
        s = (raw or "").strip()
        return bool(s) and s in self.raw_map

    def warnings(self) -> list[str]:
        return [self.error] if self.error else []


# ══════════════════════════════════════════════════════════════════════════
# DTO
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class Candidate:
    """一条候选（参与单位 / 成员 / 共享文件 / 前置成果）。"""

    ref_id: str                 # 服务端解析出的编号（company/user/file/deliverable id）
    display_name: str
    required: bool = False
    basis: dict = field(default_factory=dict)   # {declaration_index, object_type, matched_field, matched_value, why}
    extra: dict = field(default_factory=dict)   # 类型相关附加信息（所属单位 / 目标量 / 节点等）

    def to_dict(self) -> dict:
        return {
            "ref_id": self.ref_id,
            "display_name": self.display_name,
            "required": self.required,
            "basis": self.basis,
            "extra": self.extra,
        }


@dataclass
class CandidateSet:
    """候选集合（非单值）。"""

    object_type: str
    items: list[Candidate] = field(default_factory=list)
    total: int = 0
    truncated: bool = False

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "items": [c.to_dict() for c in self.items],
            "total": self.total,
            "truncated": self.truncated,
        }


@dataclass
class Unresolved:
    """声明了但零候选的条目（必须单列回显）。"""

    declaration_index: int
    object_type: str
    description: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "declaration_index": self.declaration_index,
            "object_type": self.object_type,
            "description": self.description,
            "reason": self.reason,
        }


@dataclass
class NodeDeclarations:
    """解析后的对象声明（分节）。"""

    participant_companies: list[dict] = field(default_factory=list)
    participant_users: list[dict] = field(default_factory=list)
    shared_files: list[dict] = field(default_factory=list)
    pre_conditions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def section(self, object_type: str) -> list[dict]:
        return list(getattr(self, object_type, []) or [])

    def to_dict(self) -> dict:
        return {
            "participant_companies": self.participant_companies,
            "participant_users": self.participant_users,
            "shared_files": self.shared_files,
            "pre_conditions": self.pre_conditions,
            "warnings": self.warnings,
        }


@dataclass
class AssemblyDraft:
    """装配产物：只读草稿。"""

    ref_id: str
    node_name: str
    node_type: str
    project_id: str
    operator_id: str
    generated_at: str
    deliverables: list[dict] = field(default_factory=list)
    candidates: dict = field(default_factory=dict)      # object_type → CandidateSet
    unresolved: list[Unresolved] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ref_id": self.ref_id,
            "node_name": self.node_name,
            "node_type": self.node_type,
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "deliverables": self.deliverables,
            "candidates": {k: v.to_dict() for k, v in self.candidates.items()},
            "unresolved": [u.to_dict() for u in self.unresolved],
            "warnings": self.warnings,
        }


# ══════════════════════════════════════════════════════════════════════════
# 声明解析与校验
# ══════════════════════════════════════════════════════════════════════════

class NodeDeclarationParser:
    """对象声明解析器：结构化校验，错误一律告警不静默忽略。"""

    def __init__(self, synonyms: Optional[SynonymTable] = None):
        self.synonyms = synonyms or SynonymTable()

    def parse(self, raw: Any) -> NodeDeclarations:
        out = NodeDeclarations(warnings=list(self.synonyms.warnings()))

        if raw in (None, {}, []):
            return out
        if not isinstance(raw, dict):
            out.warnings.append("对象声明区格式错误：应为映射（按对象类型分节）")
            return out

        unknown = [k for k in raw if k not in OBJECT_TYPES]
        if unknown:
            out.warnings.append(f"对象声明存在未知分节（已忽略）：{'、'.join(sorted(unknown))}")

        declared_types: set[str] = set()
        for otype in OBJECT_TYPES:
            section = raw.get(otype)
            if section in (None, []):
                continue
            if not isinstance(section, list):
                out.warnings.append(f"{otype}：分节应为列表（当前 {type(section).__name__}）")
                continue
            parsed: list[dict] = []
            for idx, entry in enumerate(section):
                item, warns = self._parse_entry(otype, idx, entry)
                out.warnings.extend(warns)
                if item is not None:
                    parsed.append(item)
                    if otype == "participant_companies":
                        declared_types.add(item["match"].get("type_norm", ""))
            setattr(out, otype, parsed)

        # 跨节校验：members 的来源单位必须在参与单位声明里出现
        for idx, item in enumerate(out.participant_users):
            from_company = str(item["match"].get("from_company", "")).strip()
            if from_company and from_company not in declared_types:
                out.warnings.append(
                    f"participant_users[{idx}] 引用不存在的来源单位「{from_company}」："
                    f"未在 participant_companies 的类型声明中出现"
                )

        return out

    def _parse_entry(self, otype: str, idx: int, entry: Any) -> tuple[Optional[dict], list[str]]:
        warns: list[str] = []
        label = f"{otype}[{idx}]"
        if not isinstance(entry, dict):
            return None, [f"{label}：条目应为映射（含 desc / match / required）"]

        desc = str(entry.get("desc", "") or "").strip()
        if not desc:
            warns.append(f"{label}：缺少 desc（描述）")

        match = entry.get("match")
        if not isinstance(match, dict) or not match:
            return None, warns + [f"{label}：缺少 match（匹配条件）"]

        required = bool(entry.get("required", False))
        clean: dict[str, Any] = {}

        if otype == "participant_companies":
            type_norm = str(match.get("type_norm", "") or "").strip()
            if not type_norm:
                return None, warns + [f"{label}：match.type_norm 必填（参与单位按类型归一匹配）"]
            if self.synonyms.loaded and not self.synonyms.is_known(type_norm):
                warns.append(f"{label}：类型「{type_norm}」越出同义词表（可能匹配不到候选）")
            clean = {
                "type_norm": type_norm,
                "scope_keywords": [str(x).strip() for x in (match.get("scope_keywords") or []) if str(x).strip()],
                "name_keywords": [str(x).strip() for x in (match.get("name_keywords") or []) if str(x).strip()],
            }
        elif otype == "participant_users":
            from_company = str(match.get("from_company", "") or "").strip()
            if not from_company:
                return None, warns + [f"{label}：match.from_company 必填（成员由来源单位推出）"]
            role = str(match.get("role", "") or "participant").strip()
            if role not in ("participant", "approver", "observer"):
                warns.append(f"{label}：role「{role}」不在 participant/approver/observer 内，按 participant 处理")
                role = "participant"
            clean = {"from_company": from_company, "role": role}
        elif otype == "shared_files":
            clean = {
                "name_keywords": [str(x).strip() for x in (match.get("name_keywords") or []) if str(x).strip()],
                "type_keywords": [str(x).strip().lower().lstrip(".") for x in (match.get("type_keywords") or []) if str(x).strip()],
            }
            if not clean["name_keywords"] and not clean["type_keywords"]:
                warns.append(f"{label}：shared_files 至少需 name_keywords 或 type_keywords 之一")
        elif otype == "pre_conditions":
            kws = [str(x).strip() for x in (match.get("deliverable_name_keywords") or []) if str(x).strip()]
            if not kws:
                warns.append(f"{label}：pre_conditions 缺少 deliverable_name_keywords（将退化为文字匹配）")
            clean = {"deliverable_name_keywords": kws}

        return {"desc": desc, "match": clean, "required": required}, warns


# ══════════════════════════════════════════════════════════════════════════
# 装配服务
# ══════════════════════════════════════════════════════════════════════════

class NodeAssemblyService:
    """按模板声明装配候选（只读草稿）。"""

    def __init__(
        self,
        loader: Optional[NodeTemplateLoader] = None,
        synonyms: Optional[SynonymTable] = None,
        parser: Optional[NodeDeclarationParser] = None,
    ):
        self.loader = loader or NodeTemplateLoader()
        self.synonyms = synonyms or SynonymTable()
        self.parser = parser or NodeDeclarationParser(self.synonyms)

    # ── 主入口 ──

    def build_draft(self, ref_id: str, project_id: str, operator_id: str = "") -> AssemblyDraft:
        """按模板装配只读草稿。

        Raises:
            TemplateUnavailable: 模板库不可用（调用方决定降级，不外抛到主链路）
            KeyError: 模板编号不存在
        """
        detail = self.loader.get_template(ref_id)
        if detail is None:
            raise KeyError(f"模板不存在：{ref_id}")

        decls = self.parser.parse(detail.declarations_raw)
        draft = AssemblyDraft(
            ref_id=detail.ref_id,
            node_name=detail.node_name,
            node_type=detail.node_type,
            project_id=project_id or "",
            operator_id=operator_id or "",
            generated_at=datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S"),
            deliverables=[
                {
                    "deliverable_name": d.name,
                    "target_amount": d.target_amount,
                    "unit": d.unit,
                    "is_required": d.is_required,
                }
                for d in detail.deliverables
            ],
            warnings=list(detail.warnings) + list(decls.warnings) + list(self.synonyms.warnings()),
        )

        companies = self.collect_companies(decls, project_id)
        users = self.collect_users(decls, companies, project_id)
        files = self.collect_files(decls, operator_id)
        dependencies = self.collect_preconditions(
            decls, project_id, detail.preconditions)

        draft.candidates = {
            "participant_companies": companies,
            "participant_users": users,
            "shared_files": files,
            "pre_conditions": dependencies,
        }

        # 未解析项：声明了但零候选（未声明某类 → 不产出，也不记为未解析）
        draft.unresolved.extend(self._unresolved_from(companies, decls.participant_companies))
        draft.unresolved.extend(self._unresolved_from(users, decls.participant_users))
        draft.unresolved.extend(self._unresolved_from(files, decls.shared_files))
        draft.unresolved.extend(self._unresolved_from(dependencies, decls.pre_conditions))
        return draft

    # ── 采集器 ①：参与单位（项目内优先 → 退全局）──

    def collect_companies(self, decls: NodeDeclarations, project_id: str) -> CandidateSet:
        from ..repositories.company_repo import CompanyRepository
        from ..repositories.participation_repo import ParticipationRepo

        decl_list = decls.participant_companies
        result = CandidateSet(object_type="participant_companies")
        if not decl_list or not project_id:
            return result

        internal_ids = ParticipationRepo.company_ids_of_projects([project_id])
        internal = CompanyRepository.find_for_matching(internal_ids) if internal_ids else []
        global_pool = CompanyRepository.find_for_matching(None)

        seen: set[str] = set()
        for idx, decl in enumerate(decl_list):
            match = decl["match"]
            hits = self._match_companies(decl, internal, source="project_internal", index=idx)
            if not hits:
                hits = self._match_companies(decl, global_pool, source="global", index=idx)
            result.items.extend(hits)
            seen.update(c.ref_id for c in hits)

        result.total = len(result.items)
        if len(result.items) > MAX_CANDIDATES:
            result.items = result.items[:MAX_CANDIDATES]
            result.truncated = True
        logger.debug("collect_companies: %d candidates（%d internal pool）",
                     result.total, len(internal))
        return result

    def _match_companies(self, decl: dict, pool: list[dict],
                         source: str, index: int) -> list[Candidate]:
        match = decl["match"]
        type_norm = match["type_norm"]
        scope_kws = match["scope_keywords"]
        name_kws = match["name_keywords"]
        out: list[Candidate] = []
        for c in pool:
            norm = self.synonyms.normalize(c.get("type", ""))
            if norm != type_norm:
                continue
            scope = c.get("scope", "") or ""
            name = c.get("company_name", "") or ""
            if scope_kws and not any(k in scope for k in scope_kws):
                continue
            if name_kws and not any(k in name for k in name_kws):
                continue
            why_parts = [f"类型归一为「{type_norm}」"]
            if scope_kws:
                why_parts.append(f"范围含 {'/'.join(scope_kws)}")
            if name_kws:
                why_parts.append(f"名称含 {'/'.join(name_kws)}")
            out.append(Candidate(
                ref_id=c["company_id"],
                display_name=name,
                required=decl["required"],
                basis={
                    "declaration_index": index,
                    "object_type": "participant_companies",
                    "matched_field": "type_norm+scope/name_keywords",
                    "matched_value": type_norm,
                    "why": "；".join(why_parts),
                    "source": source,
                },
                extra={
                    "type": c.get("type", ""),
                    "type_norm": type_norm,
                    "scope": scope,
                    "is_admin": c.get("is_admin", False),
                    "source": source,
                },
            ))
        return out

    # ── 采集器 ②：成员（由已确定参与单位推出在职人员）──

    def collect_users(self, decls: NodeDeclarations, companies: CandidateSet,
                      project_id: str) -> CandidateSet:
        from ..repositories.participation_repo import ParticipationRepo

        result = CandidateSet(object_type="participant_users")
        decl_list = decls.participant_users
        if not decl_list or not companies.items:
            return result

        # 已确定的参与单位按类型归组（成员由「来源单位」推出）
        by_type: dict[str, list[Candidate]] = {}
        for c in companies.items:
            by_type.setdefault(c.extra.get("type_norm", ""), []).append(c)

        for idx, decl in enumerate(decl_list):
            from_company = decl["match"]["from_company"]
            role = decl["match"].get("role", "participant")
            targets = by_type.get(from_company, [])
            if not targets:
                continue
            users = ParticipationRepo.users_of_companies([c.ref_id for c in targets])
            users = sorted(users, key=lambda u: (getattr(u, "level", 0) or 0), reverse=True)
            company_name = {c.ref_id: c.display_name for c in targets}
            for u in users:
                result.items.append(Candidate(
                    ref_id=str(u.id),
                    display_name=f"{u.username}（{company_name.get(u.company, '')}）",
                    required=decl["required"],
                    basis={
                        "declaration_index": idx,
                        "object_type": "participant_users",
                        "matched_field": "from_company",
                        "matched_value": from_company,
                        "why": f"所属单位「{company_name.get(u.company, '')}」归一到「{from_company}」，默认取该企业全员",
                    },
                    extra={
                        "company_id": u.company or "",
                        "company_name": company_name.get(u.company, ""),
                        "role": role,
                        "level": int(getattr(u, "level", 0) or 0),
                    },
                ))

        result.total = len(result.items)
        if len(result.items) > MAX_CANDIDATES:
            result.items = result.items[:MAX_CANDIDATES]
            result.truncated = True
        return result

    # ── 采集器 ③：共享文件（受操作人可见范围约束）──

    def collect_files(self, decls: NodeDeclarations, operator_id: str) -> CandidateSet:
        result = CandidateSet(object_type="shared_files")
        decl_list = decls.shared_files
        if not decl_list or not operator_id:
            return result

        from ..repositories.session_accessible_file_repo import SessionAccessibleFileRepo

        visible = SessionAccessibleFileRepo.list_visible(operator_id, limit=500)
        visible_by_name: list[tuple[Any, str]] = []
        for f in visible:
            visible_by_name.append((f, f.filename or ""))

        for idx, decl in enumerate(decl_list):
            name_kws = decl["match"]["name_keywords"]
            type_kws = decl["match"]["type_keywords"]
            for f, filename in visible_by_name:
                ext = (f.file_ext or "").lower().lstrip(".")
                if not ext and "." in filename:
                    ext = filename.rsplit(".", 1)[-1].lower()
                if name_kws and not any(k in filename for k in name_kws):
                    continue
                if type_kws and ext not in type_kws:
                    continue
                why = []
                if name_kws:
                    why.append(f"文件名含 {'/'.join(name_kws)}")
                if type_kws:
                    why.append(f"类型为 {ext or '未知'}")
                result.items.append(Candidate(
                    ref_id=str(f.id),
                    display_name=filename,
                    required=decl["required"],
                    basis={
                        "declaration_index": idx,
                        "object_type": "shared_files",
                        "matched_field": "name/type_keywords",
                        "matched_value": f"{name_kws or '-'} | {type_kws or '-'}",
                        "why": "；".join(why) + "（已限定在操作人可见文件集合内）",
                        "source": "visible_files",
                    },
                    extra={"file_no": getattr(f, "file_no", ""), "ext": ext,
                           "file_category": getattr(f, "file_category", "") or ""},
                ))

        result.total = len(result.items)
        if len(result.items) > MAX_CANDIDATES:
            result.items = result.items[:MAX_CANDIDATES]
            result.truncated = True
        return result

    # ── 采集器 ④：前置成果（只指向项目内已有成果）──

    def collect_preconditions(self, decls: NodeDeclarations, project_id: str,
                              text_preconditions: list[str]) -> CandidateSet:
        from ..repositories.node_repo import NodeDeliverableRepo

        result = CandidateSet(object_type="pre_conditions")
        if not project_id:
            return result

        pool = NodeDeliverableRepo.find_by_project(project_id)
        if not pool:
            return result

        decl_list = decls.pre_conditions
        if decl_list:
            # 声明式：按成果名关键词匹配（多命中取名称最长者）
            for idx, decl in enumerate(decl_list):
                kws = decl["match"]["deliverable_name_keywords"]
                if not kws:
                    continue
                best: Optional[dict] = None
                best_len = 0
                for it in pool:
                    name = _squash(it.get("deliverable_name", ""))
                    if not name or len(name) < _DEP_MIN_NAME_LEN:
                        continue
                    if not any(k in name for k in kws):
                        continue
                    if len(name) > best_len:
                        best, best_len = it, len(name)
                if best is not None:
                    result.items.append(self._dependency_candidate(
                        best, decl, idx, f"成果名含 {'/'.join(kws)}"))
        else:
            # 文字回退：整句优先 → 无命中退主体词；双向子串；多命中取最长者
            for t_idx, desc in enumerate(text_preconditions or []):
                best = _match_precondition(desc, pool)
                if best is not None:
                    result.items.append(self._dependency_candidate(
                        best, {"desc": desc, "required": True}, t_idx,
                        "前置条件文字匹配（整句优先 → 主体词）"))

        result.total = len(result.items)
        return result

    @staticmethod
    def _dependency_candidate(pool_item: dict, decl: dict, index: int, why: str) -> Candidate:
        return Candidate(
            ref_id=pool_item.get("deliverable_id", ""),
            display_name=pool_item.get("deliverable_name", ""),
            required=bool(decl.get("required", False)),
            basis={
                "declaration_index": index,
                "object_type": "pre_conditions",
                "matched_field": "deliverable_name",
                "matched_value": why,
                "why": why + "（只指向项目内已有成果）",
                "source": "existing_deliverables",
            },
            extra={
                "node_id": pool_item.get("node_id", ""),
                "node_name": pool_item.get("node_name", ""),
                "from_desc": decl.get("desc", ""),
            },
        )

    # ── 未解析项 ──

    @staticmethod
    def _unresolved_from(cs: CandidateSet, decl_list: list[dict]) -> list[Unresolved]:
        """声明了但零候选的条目（不静默丢弃）。"""
        if not decl_list or cs.items:
            return []
        return [
            Unresolved(
                declaration_index=idx,
                object_type=cs.object_type,
                description=decl.get("desc", ""),
                reason="零候选",
            )
            for idx, decl in enumerate(decl_list)
        ]


def build_assembly_service() -> NodeAssemblyService:
    """工厂：供工具与编排层按需取用。"""
    return NodeAssemblyService()
