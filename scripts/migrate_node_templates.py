#!/usr/bin/env python3
"""全景节点模板形态迁移工具：顶层单文件 md → 模板单元目录。

模板单元形态（收敛后）：
    emily-data/node_templates/{ref_id}/
      ├── template.yaml        固定命名的结构化清单入口（机器可读）
      └── <附件>               任意格式，仅登记不解析

迁移动作（幂等，可重复执行）：
  1. 扫描顶层 *.md（README.md 除外，要求有 ref_id frontmatter）
  2. 迁移前先把「编号 / 节点名称 / 节点类型 / 摘要」四项快照写入基线文件
  3. 建 {ref_id}/ 目录 → 写 template.yaml → 原 md 移入目录改名 {ref_id}-说明.md（附件）
  4. 若存在同名子目录（历史形态「顶层 md + 同名目录」），其内容整体并入 {ref_id}/

运行模式：
  uv run python scripts/migrate_node_templates.py --dry-run   # 预览迁移内容，不落盘
  uv run python scripts/migrate_node_templates.py             # 执行迁移
  uv run python scripts/migrate_node_templates.py --verify    # 四项一致性断言（AC-US-01.5）
  uv run python scripts/migrate_node_templates.py --ref-id REF-CONST-MP-001   # 只迁一个

集成点：
  - 手动触发：模板库形态上线时执行一次（宿主机）
  - 聚合薄壳：scripts/node_template_release.py 串联（migrate → index → check）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))

# ── 路径解析 ──


def _resolve_templates_dir() -> Path:
    """解析模板目录，多级回退（与 maintain_node_template_index.py 同口径）。"""
    env_dir = os.environ.get("EMILY_NODE_TEMPLATE_DIR", "")
    if env_dir:
        return Path(env_dir)
    candidates = [
        Path("/app/data/node_templates"),
        Path(__file__).resolve().parents[1] / "emily-data" / "node_templates",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[-1]


def _resolve_backup_dir() -> Path:
    """备份/基线目录：容器内 /app/runtime 与宿主机 emily-data/runtime 是**同一挂载**，双向可见。"""
    env_dir = os.environ.get("EMILY_BACKUP_DIR", "")
    if env_dir:
        return Path(env_dir)
    candidates = [
        Path("/app/runtime/backups"),                                        # 容器内
        Path(__file__).resolve().parents[1] / "emily-data" / "runtime" / "backups",  # 宿主机
    ]
    for p in candidates:
        if p.parent.exists():
            return p
    return candidates[-1]


def _resolve_baseline_path() -> Path:
    """迁移基线文件路径（四项回归对照基准）。兼容读取历史位置 emily-data/backups。"""
    new_path = _resolve_backup_dir() / "node_templates_baseline.json"
    if new_path.exists():
        return new_path
    legacy = Path(__file__).resolve().parents[1] / "emily-data" / "backups" / "node_templates_baseline.json"
    return legacy if legacy.exists() else new_path


# ── 源文件解析 ──

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ITEM_RE = re.compile(r"###\s+([^\n]+)\n(.*?)(?=\n###\s|\Z)", re.DOTALL)
_TARGET_RE = re.compile(r"目标\s*([\d.]+)\s*([^\s，。、；)）]*)")
_TYPICAL_RE = re.compile(r"\*\*典型文件名\*\*[：:]\s*(.+)")


def _parse_frontmatter(text: str) -> dict:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        import yaml
        return yaml.safe_load(m.group(1)) or {}
    except Exception:
        return {}


def _extract_summary(text: str) -> str:
    """从 ## 节点说明 中提取首句作为摘要。

    与 maintain_node_template_index.py 历史实现**逐字一致**——
    迁移前后摘要必须完全相同（AC-US-01.5）。
    """
    pattern = re.compile(r"##\s+节点说明\s*\n+(.*?)(?=\n##|\n#|\Z)", re.DOTALL)
    m = pattern.search(text)
    if not m:
        return ""
    section = m.group(1).strip()
    first_sentence = re.split(r"[。.]", section)[0].strip()
    first_sentence = re.sub(r"\n+", " ", first_sentence)
    first_sentence = re.sub(r"\s{2,}", " ", first_sentence)
    if len(first_sentence) > 120:
        first_sentence = first_sentence[:120] + "…"
    return first_sentence


def _parse_deliverables(text: str) -> list[dict]:
    """解析 ## 产物清单 → 成果清单（结构化）。"""
    m = re.search(r"##\s+产物清单\s*\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    if not m:
        return []
    section = m.group(1)
    out: list[dict] = []
    for name, body in _ITEM_RE.findall(section):
        name = re.sub(r"\s+", " ", name).strip()
        if not name:
            continue
        # 性质：先排除"非必需"（其中含"必需"二字）
        if "非必需" in body:
            is_required = False
        elif "必需" in body:
            is_required = True
        else:
            is_required = True
        tm = _TARGET_RE.search(body)
        target = float(tm.group(1)) if tm else 1.0
        unit = (tm.group(2).strip() if tm and tm.group(2).strip() else "份")
        typical = ""
        tmm = _TYPICAL_RE.search(body)
        if tmm:
            typical = re.sub(r"\s+", " ", tmm.group(1)).strip()
        out.append({
            "name": name,
            "target_amount": target,
            "unit": unit,
            "is_required": is_required,
            "typical_filenames": typical,
        })
    return out


def _parse_preconditions(text: str) -> list[str]:
    """解析 ## 前置条件 → 文字描述列表。"""
    m = re.search(r"##\s+前置条件\s*\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    if not m:
        return []
    out: list[str] = []
    for line in m.group(1).splitlines():
        s = line.strip()
        if s.startswith(("-", "*")):
            s = s.lstrip("-*").strip()
            if s:
                out.append(re.sub(r"\s{2,}", " ", s))
    return out


# ── template.yaml 生成 ──

_HEADER = """# 全景节点参考模板 —— 结构化清单入口（机器可读）
#
# 维护约定：
#   · 本文件是模板单元的**唯一结构化入口**，字段口径见 node_templates/README.md
#   · 附件（参考范例 / 成果约定 / 说明文档等）放同目录，索引只登记不解析
#   · 对象声明区 declarations 由人工维护（系统不自动生成模板内容）：
#
#       declarations:
#         participant_companies:              # 参与单位候选
#           - desc: 本项目建设单位
#             match: {type_norm: 建设单位, scope_keywords: [], name_keywords: []}
#             required: true
#         participant_users:                  # 成员候选（来源单位须已在上节声明）
#           - desc: 建设单位项目负责人
#             match: {from_company: 建设单位, role: participant}
#             required: false
#         shared_files:                       # 共享文件候选（受操作人可见范围约束）
#           - desc: 参考范例清单
#             match: {name_keywords: [全景计划], type_keywords: [xlsx]}
#             required: false
#         pre_conditions:                     # 前置成果候选（只指向项目内已有成果）
#           - desc: 立项批复文件
#             match: {deliverable_name_keywords: [立项批复]}
#             required: true
#
# 索引生成: uv run python scripts/maintain_node_template_index.py
"""


def _dump_template_yaml(data: dict) -> str:
    import yaml
    body = yaml.dump(data, allow_unicode=True, default_flow_style=False,
                     sort_keys=False, width=120)
    return _HEADER + "\n" + body


# ── 迁移 ──

def _four_fields(entry: dict) -> dict:
    """四项回归字段（AC-US-01.5 的对照口径）。"""
    return {
        "ref_id": entry.get("ref_id", ""),
        "node_name": entry.get("node_name", ""),
        "node_type": entry.get("node_type", ""),
        "summary": entry.get("summary", ""),
    }


def _scan_legacy(templates_dir: Path) -> list[dict]:
    """扫描顶层遗留单文件模板。"""
    out: list[dict] = []
    for md in sorted(templates_dir.glob("*.md")):
        if md.name == "README.md":
            continue
        text = md.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        ref_id = str(fm.get("ref_id", "") or "").strip()
        if not ref_id:
            print(f"[警告] {md.name}: 缺少 ref_id，跳过")
            continue
        out.append({
            "source": md,
            "ref_id": ref_id,
            "node_name": fm.get("node_name", ""),
            "node_type": fm.get("node_type", "TASK"),
            "stage_id": fm.get("stage_id", 0),
            "summary": _extract_summary(text),
            "deliverables": _parse_deliverables(text),
            "preconditions": _parse_preconditions(text),
            "text": text,
        })
    return out


def _write_baseline(templates_dir: Path, entries: list[dict], baseline_path: Path) -> Path:
    """写迁移基线（四项快照），供 --verify 回归对照。"""
    payload = {
        "created_at": datetime.now(BEIJING_TZ).strftime("%Y-%m-%dT%H:%M:%S"),
        "templates_dir": str(templates_dir),
        "templates": [_four_fields(e) for e in entries],
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return baseline_path


def _migrate_one(entry: dict, templates_dir: Path, dry_run: bool) -> dict:
    """迁移一个模板单元，返回动作记录。"""
    ref_id = entry["ref_id"]
    target_dir = templates_dir / ref_id
    actions: list[str] = []

    tpl = {
        "ref_id": ref_id,
        "node_name": entry["node_name"],
        "node_type": entry["node_type"],
        "stage_id": entry["stage_id"],
        "summary": entry["summary"],
        "deliverables": entry["deliverables"],
        "preconditions": entry["preconditions"],
    }

    # 附件：原 md 正文（人类可读）+ 历史同名子目录内的既有附件
    attachment_name = f"{ref_id}-说明.md"
    legacy_subdir = templates_dir / entry["source"].stem  # 「顶层 md + 同名目录」历史形态

    if dry_run:
        actions.append(f"mkdir {ref_id}/")
        actions.append(f"write {ref_id}/template.yaml（成果 {len(tpl['deliverables'])} 条 / 前置 {len(tpl['preconditions'])} 条）")
        actions.append(f"move {entry['source'].name} → {ref_id}/{attachment_name}")
        if legacy_subdir.is_dir():
            for f in sorted(legacy_subdir.iterdir()):
                actions.append(f"move {legacy_subdir.name}/{f.name} → {ref_id}/{f.name}")
        return {"ref_id": ref_id, "actions": actions}

    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "template.yaml").write_text(_dump_template_yaml(tpl), encoding="utf-8")
    actions.append(f"write {ref_id}/template.yaml")

    # 原 md 作为人类可读附件移入（正文即「说明」）
    dest_md = target_dir / attachment_name
    if dest_md.exists():
        dest_md.unlink()
    shutil.move(str(entry["source"]), str(dest_md))
    actions.append(f"move → {ref_id}/{attachment_name}")

    # 历史同名子目录内容并入
    if legacy_subdir.is_dir():
        for f in sorted(legacy_subdir.iterdir()):
            if not f.is_file():
                continue
            dest = target_dir / f.name
            if dest.exists():
                dest.unlink()
            shutil.move(str(f), str(dest))
            actions.append(f"move → {ref_id}/{f.name}")
        try:
            legacy_subdir.rmdir()
            actions.append(f"rmdir {legacy_subdir.name}/")
        except OSError:
            print(f"[警告] {legacy_subdir.name}/ 非空，保留")

    return {"ref_id": ref_id, "actions": actions}


def migrate(ref_id: str = "", dry_run: bool = False,
            templates_dir: Path | None = None) -> dict:
    """执行迁移。可 import 供聚合薄壳调用。"""
    templates_dir = templates_dir or _resolve_templates_dir()
    if not templates_dir.exists():
        return {"ok": False, "error": f"模板目录不存在: {templates_dir}"}

    entries = _scan_legacy(templates_dir)
    if ref_id:
        entries = [e for e in entries if e["ref_id"] == ref_id]
    if not entries:
        return {"ok": True, "migrated": [], "message": "无遗留单文件模板（可能已迁移完成）"}

    baseline_path = _resolve_baseline_path()
    if not dry_run:
        _write_baseline(templates_dir, _scan_legacy(templates_dir), baseline_path)

    results = [_migrate_one(e, templates_dir, dry_run) for e in entries]
    return {
        "ok": True,
        "dry_run": dry_run,
        "baseline": "" if dry_run else str(baseline_path),
        "migrated": results,
    }


def verify(templates_dir: Path | None = None) -> dict:
    """四项一致性断言：当前 template.yaml vs 迁移基线（AC-US-01.5）。"""
    templates_dir = templates_dir or _resolve_templates_dir()
    baseline_path = _resolve_baseline_path()
    if not baseline_path.exists():
        return {"ok": False, "error": f"基线文件不存在: {baseline_path}（迁移时未写基线，无法回归对照）"}

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    before = {t["ref_id"]: t for t in baseline.get("templates", [])}

    try:
        import yaml
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"yaml 不可用: {e}"}

    rows: list[dict] = []
    for ref in sorted(before):
        tpl_file = templates_dir / ref / "template.yaml"
        if not tpl_file.exists():
            rows.append({"ref_id": ref, "status": "MISSING",
                         "detail": "未找到 template.yaml"})
            continue
        cur = yaml.safe_load(tpl_file.read_text(encoding="utf-8")) or {}
        diffs = [
            f for f in ("ref_id", "node_name", "node_type", "summary")
            if str(cur.get(f, "")) != str(before[ref].get(f, ""))
        ]
        rows.append({
            "ref_id": ref,
            "status": "DIFF" if diffs else "PASS",
            "detail": "字段不一致: " + ", ".join(diffs) if diffs else "四项一致",
            "before": before[ref],
            "after": {f: cur.get(f, "") for f in ("ref_id", "node_name", "node_type", "summary")},
        })

    return {
        "ok": all(r["status"] == "PASS" for r in rows) and bool(rows),
        "baseline_created_at": baseline.get("created_at", ""),
        "rows": rows,
    }


# ══════════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="全景节点模板形态迁移工具")
    parser.add_argument("--dry-run", action="store_true", help="预览迁移动作，不落盘")
    parser.add_argument("--verify", action="store_true", help="四项一致性断言（AC-US-01.5）")
    parser.add_argument("--ref-id", default="", help="只迁移指定模板编号")
    args = parser.parse_args()

    if args.verify:
        r = verify()
        if not r.get("ok") and r.get("error"):
            print(f"[错误] {r['error']}")
            return 1
        print(f"[校验] 基线时间: {r['baseline_created_at']}")
        for row in r.get("rows", []):
            mark = "PASS" if row["status"] == "PASS" else row["status"]
            print(f"  [{mark}] {row['ref_id']} —— {row['detail']}")
        bad = [x for x in r.get("rows", []) if x["status"] != "PASS"]
        print(f"[校验] {'全部通过' if r['ok'] else f'{len(bad)} 项不通过'}（{len(r.get('rows', []))} 个模板）")
        return 0 if r["ok"] else 1

    r = migrate(ref_id=args.ref_id, dry_run=args.dry_run)
    if not r.get("ok"):
        print(f"[错误] {r.get('error')}")
        return 1
    if not r["migrated"]:
        print(f"[跳过] {r.get('message', '无待迁移模板')}")
        return 0

    mode = "预览（未落盘）" if r.get("dry_run") else "已迁移"
    print(f"[迁移] {mode} —— {len(r['migrated'])} 个模板单元")
    for item in r["migrated"]:
        print(f"  {item['ref_id']}:")
        for a in item["actions"]:
            print(f"    - {a}")
    if r.get("baseline"):
        print(f"[基线] {r['baseline']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
