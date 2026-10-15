"""ingest_service —— 资料库入库引擎（Service 层，sync 核心）。

职责（US-01 / US-02 / US-09）：
  - 一次性遍历目标目录树，为**每一层目录**生成/更新索引文件（AC-US-01.1 / 01.10）
  - 索引格式与口径由**入库手册**（M3）规定，本模块只按手册填写字段（AC-US-02.2）
  - 幂等：重复入库覆盖同路径索引，不追加、不翻倍（AC-US-01.3）
  - 预览模式（dry_run）只输出"将生成/更新的索引清单"，不写任何文件（AC-US-01.7）
  - 遍历只**读**原始资料，索引之外一律不写（AC-US-01.4 / 约束 11）
  - 留痕经既有留痕门面（`@audited`）挂在动作方法上（AC-US-03.3）
  - 附带刷新**根索引**，使首层结构与查阅方法有写实索引（AC-US-01.5）

层级一致性（AC-US-02.3）：父层索引中的子目录说明引用子目录**本次生成**的类型行，
故同一轮入库产出的上下层索引口径一致（不依赖上一轮产物）。

分层：Service（sync）→ Repository（KnowledgeLibraryFs）→ 文件系统。
上层工具（M4b/M5）与脚本（M4a）以 `asyncio.to_thread()` 或直接调用包裹本层，不跨层。
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from ...infrastructure.logging.audit import audited
from .library_fs import (
    KnowledgeLibraryError,
    KnowledgeLibraryFs,
    KnowledgeLibraryNotFoundError,
    KnowledgeLibraryPathError,
)

logger = logging.getLogger("emily.knowledge_library.ingest")

# 入库手册（M3）：prompt 目录下的名称（不含 .md），机器可读段标记
_PLAYBOOK_NAME = "knowledge_ingest_playbook"
_SPEC_BEGIN = "<!-- INDEX_SPEC:BEGIN -->"
_SPEC_END = "<!-- INDEX_SPEC:END -->"
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$", re.MULTILINE)

# ── 描述口径（手册 §4：按名称推断，不解析内容）──────────────────────────────
# 扩展名 → 类型词（kind）
_EXT_KIND: dict[str, str] = {
    ".md": "文本", ".markdown": "文本", ".txt": "文本", ".text": "文本",
    ".pdf": "PDF", ".doc": "Word 文档", ".docx": "Word 文档", ".rtf": "Word 文档",
    ".xls": "表格", ".xlsx": "表格", ".et": "表格",
    ".csv": "数据表", ".tsv": "数据表",
    ".ppt": "演示文档", ".pptx": "演示文档", ".dps": "演示文档",
    ".dwg": "CAD 图", ".dxf": "CAD 图",
    ".jpg": "图片", ".jpeg": "图片", ".png": "图片", ".gif": "图片",
    ".bmp": "图片", ".webp": "图片", ".tif": "图片", ".tiff": "图片", ".heic": "图片",
    ".zip": "压缩包", ".rar": "压缩包", ".7z": "压缩包", ".tar": "压缩包", ".gz": "压缩包",
    ".json": "结构化数据", ".xml": "结构化数据",
    ".yml": "配置文本", ".yaml": "配置文本", ".ini": "配置文本", ".cfg": "配置文本",
    ".mp4": "视频", ".mov": "视频", ".avi": "视频", ".mkv": "视频",
    ".mp3": "音频", ".wav": "音频", ".m4a": "音频",
    ".eml": "邮件", ".msg": "邮件",
    ".exe": "程序文件", ".dll": "程序文件", ".bak": "备份文件",
}

# 名称语义词 → role（先匹配到的优先；越具体的口径越靠前）
_ROLE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("合同", "协议", "contract", "agreement"), "合同/协议"),
    (("纪要", "会议记录", "会议记要"), "会议纪要"),
    (("周报", "月报", "日报", "年报", "季报", "半月报"), "周期性报告"),
    (("报告", "汇报", "总结", "评估"), "报告"),
    (("台账", "清单", "明细", "汇总", "登记表", "名录"), "清单台账"),
    (("方案", "预案", "措施"), "方案"),
    (("图纸", "施工图", "总平面", "平面图", "立面图", "剖面图", "大样图", "详图", "竣工图"), "图纸"),
    (("计划", "排期", "进度", "工期"), "计划/进度"),
    (("日志", "记录", "巡查表"), "过程记录"),
    (("验收", "检验", "检测", "试验", "送检"), "验收/检测资料"),
    (("预算", "结算", "报价", "造价", "决算", "计价"), "造价文件"),
    (("通知", "联系单", "请示", "批复", "函", "签证", "变更单"), "往来文函"),
    (("照片", "相片", "影像", "视频", "录像", "航拍"), "影像资料"),
    (("规范", "标准", "规程", "指南", "导则", "条例", "准则"), "规范/标准"),
    (("证书", "证照", "执照", "资质", "许可"), "证照"),
    (("模板", "范例", "样例", "样式"), "模板"),
    (("培训", "讲义", "教材"), "培训资料"),
    # 通用说明类（放在具体业务类型之后，避免抢词）
    (("技术交底", "交底"), "技术交底"),
    (("做法", "工艺", "工法"), "做法/工艺"),
    (("readme", "说明", "简介", "概述", "基本信息", "项目概况"), "说明文档"),
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolve_playbook_path(explicit: str | None = None) -> Path | None:
    """解析入库手册路径（显式入参 → prompt 目录多级回退）。"""
    if explicit and str(explicit).strip():
        p = Path(str(explicit).strip())
        return p if p.is_file() else None
    from ...infrastructure.llm.prompt_loader import _find_prompt_path

    return _find_prompt_path(_PLAYBOOK_NAME)


def _load_playbook_spec(playbook_path: Path) -> dict:
    """从手册中解析机器可读段（INDEX_SPEC 标记之间的 YAML）。

    Raises:
        KnowledgeLibraryError: 手册缺标记 / 解析失败 / 缺必需字段。
    """
    import yaml

    try:
        raw = playbook_path.read_text(encoding="utf-8")
    except OSError as e:
        raise KnowledgeLibraryError(f"入库手册读取失败：{playbook_path}（{e}）") from e

    if _SPEC_BEGIN not in raw or _SPEC_END not in raw:
        raise KnowledgeLibraryError(
            f"入库手册缺少机器可读段（{_SPEC_BEGIN} … {_SPEC_END}）：{playbook_path}"
        )
    block = raw.split(_SPEC_BEGIN, 1)[1].split(_SPEC_END, 1)[0]
    block = _FENCE_RE.sub("", block)
    try:
        spec = yaml.safe_load(block)
    except Exception as e:  # noqa: BLE001
        raise KnowledgeLibraryError(f"入库手册机器可读段解析失败：{e}") from e
    if not isinstance(spec, dict):
        raise KnowledgeLibraryError("入库手册机器可读段不是 YAML 映射")

    required = ("frontmatter", "heading_line", "sections", "intro_template",
                "types_line", "subdir_desc", "catalog", "notes_lines")
    missing = [k for k in required if k not in spec]
    if missing:
        raise KnowledgeLibraryError(f"入库手册机器可读段缺字段：{', '.join(missing)}")
    if not spec.get("sections"):
        raise KnowledgeLibraryError("入库手册机器可读段未声明正文小节")
    return spec


class KnowledgeLibraryIngestService:
    """资料库入库引擎（sync）。"""

    def __init__(self, fs: KnowledgeLibraryFs, playbook_path: str | None = None):
        self._fs = fs
        self._playbook_path = _resolve_playbook_path(playbook_path)
        self._spec: dict | None = None

    # ── 依赖 ──

    def _playbook(self) -> dict:
        if self._spec is None:
            if self._playbook_path is None:
                raise KnowledgeLibraryError(
                    f"入库手册未找到（{_PLAYBOOK_NAME}.md）：请确认 emily-data/prompts 已挂载"
                )
            self._spec = _load_playbook_spec(self._playbook_path)
        return self._spec

    @property
    def playbook_path(self) -> str:
        """本次实际使用的入库手册路径（空串 = 未找到）。"""
        return str(self._playbook_path) if self._playbook_path else ""

    # ── 公开动作入口（按 dry_run 分派；两条路径各挂一次留痕）──

    def ingest(self, rel: str = "", dry_run: bool = False, operator_id: str = "") -> dict:
        """资料库入库（公开入口）：dry_run=True 走预览，不写任何文件。

        Returns:
            {success, ok, dry_run, target, created[], updated[], skipped[], stats{}}
            失败时 {success: False, ok: False, error, error_code}
        """
        if dry_run:
            return self._preview(rel, operator_id=operator_id)
        return self._apply(rel, operator_id=operator_id)

    @audited(
        category="knowledge_library",
        action="ingest_preview",
        target_type="knowledge_library_dir",
        actor_arg="operator_id",
        target_arg="rel",
        summary="资料库入库预览：{target}",
    )
    def _preview(self, rel: str = "", *, operator_id: str = "") -> dict:
        """预览：输出将生成/更新的索引清单与统计，不写盘（AC-US-01.7）。"""
        return self._run(rel, dry_run=True, operator_id=operator_id)

    @audited(
        category="knowledge_library",
        action="ingest",
        target_type="knowledge_library_dir",
        actor_arg="operator_id",
        target_arg="rel",
        summary="资料库入库：{target}",
    )
    def _apply(self, rel: str = "", *, operator_id: str = "") -> dict:
        """实际入库：逐层生成/覆盖索引（幂等），不改动原始资料（AC-US-01.3 / 01.4）。"""
        return self._run(rel, dry_run=False, operator_id=operator_id)

    # ── 描述推断（手册 §4）：按文件名 + 所在目录名，不解析内容 ──

    def describe_file(self, name: str, parent_dir_name: str = "") -> str:
        """由文件名 + 所属目录名推断一句话描述；图片/二进制/压缩包等不解析内容。

        Returns:
            形如「合同/协议（PDF）」的一句话描述。
        """
        filename = str(name or "").strip()
        suffix = Path(filename).suffix.lower()
        kind = _EXT_KIND.get(suffix, "文件")
        stem = Path(filename).stem.lower()
        role = ""
        for keys, phrase in _ROLE_KEYWORDS:
            if any(k in stem for k in keys):
                role = phrase
                break
        if not role and parent_dir_name:
            role = f"{parent_dir_name}相关资料"
        return f"{role}（{kind}）" if role else kind

    # ══════════════════════════════════════════════════════════════════════
    #  遍历 + 渲染
    # ══════════════════════════════════════════════════════════════════════

    def _run(self, rel: str, *, dry_run: bool, operator_id: str = "") -> dict:
        started = time.monotonic()
        try:
            spec = self._playbook()
            target = self._fs.resolve(rel)
        except KnowledgeLibraryPathError as e:
            return self._fail(str(e), "path_out_of_range")
        except KnowledgeLibraryError as e:
            return self._fail(str(e), "playbook_unavailable")

        if not target.exists() or not target.is_dir():
            return self._fail(f"目标目录不存在：{rel or '/'}", "target_missing")

        root = self._fs.root.resolve()
        target_rel = "" if target.resolve() == root else target.resolve().relative_to(root).as_posix()

        # 一次性遍历目标树（先父后子的目录清单 + 各目录直接子项 + 所辖规模）
        try:
            tree = self._scan_tree(target_rel)
        except (KnowledgeLibraryPathError, KnowledgeLibraryNotFoundError) as e:
            return self._fail(str(e), "target_missing")

        dirs: list[str] = list(tree["dirs"])
        # 附带刷新根索引（AC-US-01.5：首层结构与查阅方法有写实索引）：
        # 根不在目标树内时补扫全库，只为取得根索引的所辖规模与首层目录。
        if target_rel:
            try:
                root_tree = self._scan_tree("")
                tree["entries"][""] = root_tree["entries"].get("", [])
                tree["counts"][""] = root_tree["counts"].get("", {"file_count": 0, "dir_count": 0})
                dirs = [""] + dirs
            except KnowledgeLibraryError as e:  # 根不可达不阻断子目录入库
                logger.warning("根索引刷新跳过：%s", e)

        # 各目录的类型行（供父层引用，保证同轮产出上下层口径一致 —— 手册 §7）
        types_map = self._types_map(dirs, tree, spec)
        indexable = set(dirs)

        created: list[str] = []
        updated: list[str] = []
        skipped: list[str] = []
        generated_at = _now_iso()
        planned: list[tuple[str, str]] = []
        file_total = 0

        for dir_rel in dirs:
            try:
                content, dir_files = self._render_index(
                    dir_rel, tree, spec, generated_at, types_map, indexable)
            except KnowledgeLibraryError as e:
                skipped.append(dir_rel or "/")
                logger.warning("索引渲染跳过 %s：%s", dir_rel or "/", e)
                continue
            file_total += dir_files
            if self._fs.index_exists(dir_rel):
                updated.append(dir_rel or "/")
            else:
                created.append(dir_rel or "/")
            planned.append((dir_rel, content))

        written = 0
        if not dry_run:
            for dir_rel, content in planned:
                try:
                    self._fs.write_index(dir_rel, content)
                    written += 1
                except KnowledgeLibraryError as e:
                    skipped.append(dir_rel or "/")
                    logger.warning("索引写入失败 %s：%s", dir_rel or "/", e)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "knowledge_library ingest%s target=%s created=%d updated=%d skipped=%d written=%d operator=%s (%dms)",
            " (dry-run)" if dry_run else "", target_rel or "/",
            len(created), len(updated), len(skipped), written, operator_id or "-", elapsed_ms,
        )
        return {
            "success": True,
            "ok": True,
            "dry_run": dry_run,
            "target": target_rel or "/",
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "stats": {
                "dirs": len(dirs),
                "files": file_total,
                "created": len(created),
                "updated": len(updated),
                "skipped": len(skipped),
                "written": written,
                "elapsed_ms": elapsed_ms,
            },
        }

    @staticmethod
    def _fail(error: str, error_code: str) -> dict:
        return {"success": False, "ok": False, "error": error, "error_code": error_code,
                "created": [], "updated": [], "skipped": [], "stats": {}}

    def _scan_tree(self, rel: str) -> dict:
        """一次性遍历：目录清单（先父后子）+ 各目录直接子项 + 所辖规模统计。

        Returns:
            {dirs: [dir_rel...], entries: {dir_rel: [entry...]},
             counts: {dir_rel: {file_count, dir_count}}}  # 规模含子树
        """
        base = self._fs.resolve(rel)
        root = self._fs.root.resolve()
        dirs: list[str] = []
        entries: dict[str, list[dict]] = {}

        for cur, dirnames, _filenames in os.walk(base):
            dirnames.sort()
            cur_path = Path(cur)
            dir_rel = "" if cur_path.resolve() == root else cur_path.resolve().relative_to(root).as_posix()
            dirs.append(dir_rel)
            entries[dir_rel] = self._fs.list_entries(dir_rel)

        # 所辖规模：自底向上累加（dirs 为先父后子，逆序即先子后父）
        counts: dict[str, dict] = {}
        for dir_rel in dirs:
            subdirs = [e for e in entries[dir_rel] if e["kind"] == "dir"]
            counts[dir_rel] = {
                "file_count": len(entries[dir_rel]) - len(subdirs),
                "dir_count": len(subdirs),
            }
        for dir_rel in reversed(dirs):
            for e in entries[dir_rel]:
                if e["kind"] != "dir":
                    continue
                child_rel = f"{dir_rel}/{e['name']}" if dir_rel else e["name"]
                child = counts.get(child_rel)
                if child is None:
                    continue
                counts[dir_rel]["file_count"] += child["file_count"]
                counts[dir_rel]["dir_count"] += child["dir_count"] + 1

        return {"dirs": dirs, "entries": entries, "counts": counts}

    @staticmethod
    def _types_map(dirs: list[str], tree: dict, spec: dict) -> dict[str, str]:
        """各目录的「资料类型」行取值（本层直接子目录名归纳）——父层引用它保持口径一致。"""
        fallback = str(spec.get("types_fallback") or "")
        result: dict[str, str] = {}
        for dir_rel in dirs:
            names = [e["name"] for e in tree["entries"].get(dir_rel, []) if e["kind"] == "dir"]
            result[dir_rel] = "、".join(names) if names else fallback
        return result

    def _child_types(self, child_rel: str, spec: dict) -> str | None:
        """读取**既有**索引的「类型行」（子目录不在本轮入库范围内时的兜底，手册 §7）。"""
        try:
            text = self._fs.read_index(child_rel)
        except KnowledgeLibraryError:
            return None
        if not text:
            return None
        marker = str(spec["types_line"]).split("{types}")[0].strip()
        for line in text.splitlines():
            stripped = line.strip()
            if marker and stripped.startswith(marker):
                value = stripped[len(marker):].strip().rstrip("。").strip()
                return value or None
        return None

    def _render_index(self, dir_rel: str, tree: dict, spec: dict, generated_at: str,
                      types_map: dict[str, str], indexable: set[str]) -> tuple[str, int]:
        """按手册机器可读段渲染一份索引（含 frontmatter 元信息）。"""
        entries = tree["entries"].get(dir_rel, [])
        counts = tree["counts"].get(dir_rel, {"file_count": 0, "dir_count": 0})
        dir_name = "资料库" if not dir_rel else Path(dir_rel).name
        scope = "/" if not dir_rel else dir_rel

        # 简介：规模句 + 类型句
        intro = str(spec["intro_template"]).format(
            name=dir_name, scope=scope,
            file_count=counts["file_count"], dir_count=counts["dir_count"],
        )
        intro = f"{intro}\n{str(spec['types_line']).format(types=types_map.get(dir_rel, ''))}"

        # 文件目录：只列直接子项（子目录在前），子目录说明引用其类型行
        catalog_cfg = spec["catalog"] or {}
        subdir_cfg = spec["subdir_desc"] or {}
        types_fallback = str(spec.get("types_fallback") or "")
        lines: list[str] = []
        subdir_count = 0
        for e in entries:
            if e["kind"] == "dir":
                subdir_count += 1
                child_rel = f"{dir_rel}/{e['name']}" if dir_rel else e["name"]
                child_types = types_map.get(child_rel) if child_rel in indexable else None
                if not child_types:
                    child_types = self._child_types(child_rel, spec)
                # 子目录自身无下级类型可引用（叶目录 / 未入库）→ 用兜底说明
                if child_types and child_types != types_fallback:
                    desc = str(subdir_cfg.get("from_child_types", "含：{types}")).format(types=child_types)
                else:
                    tpl = str(subdir_cfg.get("fallback", "{name}资料"))
                    desc = tpl.format(name=e["name"])
                    # 兜底说明的重复收口：目录名本身已以「资料/文件/文档/材料」收尾时
                    # 不再追加同名后缀（避免「验收资料资料」这类重复）
                    for _tail in ("资料", "文件", "文档", "材料"):
                        if e["name"].endswith(_tail) and desc == f"{e['name']}资料":
                            desc = e["name"]
                            break
                line = str(catalog_cfg.get("dir_line", "{name}/ — {desc}")).format(
                    name=e["name"], desc=desc)
                # 本轮未生成且磁盘上也无索引 → 标注未入库（约束 12：索引缺失即未入库）
                if child_rel not in indexable and not self._fs.index_exists(child_rel):
                    line += str(catalog_cfg.get("unindexed_suffix", ""))
            else:
                line = str(catalog_cfg.get("file_line", "{name} — {desc}")).format(
                    name=e["name"], desc=self.describe_file(e["name"], dir_name))
            lines.append(line)
        catalog = "\n".join(lines) if lines else str(catalog_cfg.get("empty_line", "（本目录暂无子项）"))

        # 其他备注
        notes = "\n".join(
            f"- {str(tpl).format(generated_at=generated_at)}" for tpl in (spec["notes_lines"] or [])
        )

        bodies = {"intro": intro, "catalog": catalog, "notes": notes}

        fm_values = {
            "generated_at": generated_at,
            "scope": scope,
            "file_count": counts["file_count"],
            "dir_count": counts["dir_count"],
        }
        parts: list[str] = ["---"]
        for key in spec["frontmatter"]:
            parts.append(f"{key}: {fm_values.get(str(key), '')}")
        parts.append("---")
        parts.append("")
        parts.append(str(spec["heading_line"]).format(name=dir_name))
        for section in spec["sections"]:
            key = str(section.get("key", ""))
            parts.append("")
            parts.append(f"## {section.get('title', key)}")
            parts.append(bodies.get(key, ""))
        parts.append("")

        return "\n".join(parts), len(entries) - subdir_count
